import os
import re
import logging
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from google import genai
from google.genai import types

from app.config import settings
from app.models.tenant import Tenant
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.branch import Branch
from app.models.delivery_rate import DeliveryRate
from app.models.user import User
from app.models.sale import Sale
from app.models.favorite import Favorite
from app.models.user_wallet import UserWallet
from app.models.reservation import Reservation, ReservationStatus
from app.schemas.chatbot import ChatMessage, ChatbotResponse
from app.services.sale_service import SaleService

logger = logging.getLogger("chatbot_service")


class ChatbotService:
    @classmethod
    def get_api_key(cls) -> str:
        return (
            getattr(settings, "CHATBOT_GEMINI_API_KEY", "")
            or os.getenv("CHATBOT_GEMINI_API_KEY", "")
            or getattr(settings, "VOICE_GEMINI_API_KEY", "")
            or os.getenv("VOICE_GEMINI_API_KEY", "")
            or getattr(settings, "GEMINI_API_KEY", "")
            or os.getenv("GEMINI_API_KEY", "")
        )

    @classmethod
    def process_message(
        cls,
        db: Session,
        message: str,
        history: List[ChatMessage],
        tenant_slug: Optional[str] = None,
        current_user: Optional[User] = None
    ) -> ChatbotResponse:
        """
        Procesa un mensaje conversacional con IA para la tienda activa.
        Inyecta el catálogo, sucursales y métricas comerciales según el rol del usuario.
        """
        # 1. Determinar el Tenant activo
        tenant = None
        if current_user and current_user.tenant:
            tenant = current_user.tenant
        elif tenant_slug:
            clean_slug = tenant_slug.lower().strip()
            slug_normalized = clean_slug.replace("-", "")
            tenant = (
                db.query(Tenant)
                .filter(
                    (Tenant.slug == clean_slug)
                    | (Tenant.slug == slug_normalized)
                    | (Tenant.slug.ilike(f"%{clean_slug}%"))
                )
                .first()
            )
            if not tenant:
                if "moda" in clean_slug:
                    tenant = db.query(Tenant).filter(Tenant.slug.ilike("%moda%")).first()
                elif "urban" in clean_slug:
                    tenant = db.query(Tenant).filter(Tenant.slug.ilike("%urban%")).first()

        if not tenant:
            tenant = db.query(Tenant).filter(Tenant.activo == True).order_by(Tenant.created_at.asc()).first()

        tenant_name = tenant.nombre if tenant else "FICCT Store"
        tenant_slug_val = tenant.slug if tenant else "urbanstyle"
        is_moda_express = "moda" in tenant_slug_val.lower()

        # 2. Identificar si el usuario tiene rol administrativo / operativo
        is_staff_user = False
        user_role_name = "Cliente"
        if current_user and current_user.role:
            user_role_name = current_user.role.name or "Cliente"
            role_lower = user_role_name.lower()
            if any(r in role_lower for r in ["admin", "superadmin", "administrador", "gerente", "cajero", "encargado"]):
                is_staff_user = True

        # 3. Recopilar datos de catálogo y sucursales
        products_info = []
        if tenant:
            products = (
                db.query(Product)
                .filter(Product.tenant_id == tenant.id, Product.activo == True)
                .limit(15)
                .all()
            )
            for p in products:
                tallas = list(set([v.talla for v in p.variantes if v.activo and v.talla]))
                colores = list(set([v.color for v in p.variantes if v.activo and v.color]))
                precio_str = f"{float(p.precio_oferta):.2f} Bs. (Oferta)" if p.en_oferta and p.precio_oferta else f"{float(p.precio_base):.2f} Bs."
                products_info.append(
                    f"- {p.nombre}: {precio_str}. "
                    f"Tallas: {', '.join(tallas) or 'Única'}. Colores: {', '.join(colores) or 'Varios'}."
                )

        branches_info = []
        if tenant:
            branches = db.query(Branch).filter(Branch.tenant_id == tenant.id, Branch.activa == True).all()
            for b in branches:
                branches_info.append(f"- {b.nombre} en {b.ciudad} ({b.direccion})")

        delivery_info = []
        if tenant:
            rates = db.query(DeliveryRate).filter(DeliveryRate.tenant_id == tenant.id, DeliveryRate.activo == True).all()
            for r in rates:
                delivery_info.append(f"- {r.zona_nombre} ({r.ciudad}): {float(r.costo_envio):.2f} Bs ({r.tiempo_estimado_horas} hrs)")

        # 4. Datos comerciales para Administradores / Cajeros
        business_metrics = {}
        top_products_data = []
        top_products_text_list = []

        if is_staff_user:
            try:
                chatbot_tenant_id = tenant.id if tenant else None
                business_metrics = SaleService.get_summary_report(db, tenant_id=chatbot_tenant_id)
                top_products_data = SaleService.get_top_products(db, limit=5, tenant_id=chatbot_tenant_id)
                for idx, tp in enumerate(top_products_data, start=1):
                    top_products_text_list.append(
                        f"  #{idx} {tp.get('producto_nombre', 'Prenda')} ({tp.get('talla', '')}/{tp.get('color', '')}): "
                        f"{tp.get('unidades_vendidas', 0)} unidades vendidas (Bs. {tp.get('ingresos_generados', 0.0):,.2f})"
                    )
            except Exception as e:
                logger.warning(f"Error cargando métricas de negocio para chatbot: {e}")

        # 5. Datos privados del cliente autenticado
        user_sales_info = []
        user_favs_info = []
        user_wallet_balance = 0.0
        user_reservations_info = []

        if current_user:
            user_role_context = f"{current_user.full_name} ({current_user.email}) - Rol: {user_role_name}"
            try:
                sales = (
                    db.query(Sale)
                    .filter(Sale.cliente_id == current_user.id)
                    .order_by(Sale.created_at.desc())
                    .limit(5)
                    .all()
                )
                for s in sales:
                    items_desc = []
                    for d in s.detalles:
                        prod_name = d.producto.nombre if d.producto else (d.nombre_producto or "Prenda")
                        items_desc.append(f"{d.cantidad}x {prod_name} ({float(d.precio_unitario):.2f} Bs.)")
                    fecha_fmt = s.created_at.strftime("%d/%m/%Y") if s.created_at else ""
                    user_sales_info.append(
                        f"- Nota/Recibo #{s.numero_recibo} ({fecha_fmt}) por {float(s.monto_total):.2f} Bs: {', '.join(items_desc) if items_desc else 'Prendas de catálogo'}"
                    )
            except Exception as e:
                logger.warning(f"Error cargando ventas para chatbot: {e}")

            try:
                favs = (
                    db.query(Favorite)
                    .filter(Favorite.user_id == current_user.id)
                    .limit(8)
                    .all()
                )
                for f in favs:
                    if f.producto:
                        user_favs_info.append(f"- {f.producto.nombre} ({float(f.producto.precio_base):.2f} Bs.)")
            except Exception as e:
                logger.warning(f"Error cargando favoritos para chatbot: {e}")

            try:
                wallet = db.query(UserWallet).filter(UserWallet.user_id == current_user.id).first()
                if wallet:
                    user_wallet_balance = float(wallet.saldo or 0.0)
            except Exception as e:
                logger.warning(f"Error cargando billetera para chatbot: {e}")

            try:
                reservas = (
                    db.query(Reservation)
                    .filter(
                        Reservation.cliente_id == current_user.id,
                        Reservation.estado.in_([ReservationStatus.PENDIENTE, ReservationStatus.PREPARADA])
                    )
                    .limit(3)
                    .all()
                )
                for res in reservas:
                    fecha_exp = res.fecha_limite.strftime("%d/%m/%Y %H:%M") if res.fecha_limite else "48 horas"
                    user_reservations_info.append(f"- Reserva #{res.codigo_reserva} (Expira: {fecha_exp})")
            except Exception as e:
                logger.warning(f"Error cargando reservas para chatbot: {e}")
        else:
            user_role_context = "Visitante / Cliente anónimo (No autenticado)"

        catalog_text = "\n".join(products_info) if products_info else "Catálogo disponible en tienda."
        branches_text = "\n".join(branches_info) if branches_info else "Sucursales disponibles en red."
        delivery_text = "\n".join(delivery_info) if delivery_info else "Envíos disponibles según zona (15 a 25 Bs)."
        user_sales_text = "\n".join(user_sales_info) if user_sales_info else "No registra compras previas aún."
        user_favs_text = "\n".join(user_favs_info) if user_favs_info else "No tiene prendas marcadas como favoritas."
        user_reservations_text = "\n".join(user_reservations_info) if user_reservations_info else "No tiene reservas activas pendientes de recojo."
        top_prods_str = "\n".join(top_products_text_list) if top_products_text_list else "Sin datos consolidados de ventas."

        # 6. Construir System Prompt especializado según ROL
        if is_staff_user:
            tot_ventas = business_metrics.get("total_ventas", 0)
            tot_ingresos = business_metrics.get("ingresos_totales", 0.0)
            ticket_prom = business_metrics.get("ticket_promedio", 0.0)
            v_efectivo = business_metrics.get("ventas_efectivo", 0.0)
            v_qr = business_metrics.get("ventas_qr", 0.0)
            v_tarjeta = business_metrics.get("ventas_tarjeta", 0.0)
            v_paypal = business_metrics.get("ventas_paypal", 0.0)
            ganancia_neta = business_metrics.get("ganancia_neta", 0.0)
            total_iva = business_metrics.get("total_iva", 0.0)

            system_instruction = f"""
Eres "FICCT Bot", el Asistente Ejecutivo Inteligente y Asesor Oficial de Operaciones para la empresa "{tenant_name}".
Estás interactuando directamente con {current_user.full_name} ({current_user.email}), quien es {user_role_name.upper()} con plenos privilegios administrativos de la empresa.

AUTORIZACIÓN Y ACCESO A DATOS:
- El usuario es ADMINISTRADOR AUTORIZADO. TIENE ACCESO TOTAL Y OFICIAL a todos los datos comerciales, financieros, de facturación, inventario, ventas y reportes de la empresa.
- REGLA ESTRICTA: NUNCA le digas que tu acceso a las ventas globales o finanzas está restringido por seguridad. Tienes acceso completo a todas las métricas que te proporcionamos a continuación y DEBES responder a cualquier consulta sobre ventas, ingresos o inventario de inmediato.

MÉTRICAS Y ESTADÍSTICAS DEL NEGOCIO EN TIEMPO REAL ({tenant_name}):
- Facturación Total Acumulada: Bs. {tot_ingresos:,.2f}
- Total de Ventas/Transacciones Realizadas: {tot_ventas} ventas completadas
- Ticket Promedio: Bs. {ticket_prom:,.2f}
- Ganancia Neta Comercial: Bs. {ganancia_neta:,.2f} (IVA 13% estimado: Bs. {total_iva:,.2f})
- Desglose Oficial por Medio de Cobro:
  * Cobros en Efectivo en mostrador: Bs. {v_efectivo:,.2f}
  * Cobros mediante Pago QR: Bs. {v_qr:,.2f}
  * Cobros mediante Tarjeta de Débito/Crédito: Bs. {v_tarjeta:,.2f}
  * Cobros vía Pasarela Digital PayPal: Bs. {v_paypal:,.2f}
- Top 5 Prendas Más Vendidas y Rentables:
{top_prods_str}
- Sucursales Activas de la Red:
{branches_text}
- Muestrario de Prendas en Catálogo:
{catalog_text}

DIRECTIVAS PARA CONSULTAS ADMINISTRATIVAS:
1. Si el administrador pregunta por ventas, facturación, total vendido o ingresos, dale las cifras exactas de inmediato (ej. "Actualmente registramos una Facturación Total de Bs. {tot_ingresos:,.2f} en {tot_ventas} ventas completadas").
2. Si pregunta por medios de pago o distribución de caja, desglosa los cobros en efectivo, QR, tarjeta y PayPal.
3. Si pregunta por productos estrella, más vendidos o rotación, enumera el ranking con sus cantidades e ingresos.
4. Si pregunta por catálogo, inventario, precios o sucursales, respóndele con precisión ejecutiva.
5. Mantén un tono ejecutivo, ágil, formal y conciso. Usa viñetas y negrita para destacar las cifras monetarias.
"""
        else:
            system_instruction = f"""
Eres "FICCT Bot", el Asistente Virtual Inteligente oficial de retail para la empresa "{tenant_name}".
Tu propósito es asesorar a clientes en moda, responder dudas del catálogo, precios, tallas, sucursales, envíos y políticas de tienda.

DATOS DEL NEGOCIO ({tenant_name}):
- Enfoque de Marca: {"Moda ejecutiva, formal, ternos de gala y vestidos" if is_moda_express else "Moda masculina contemporánea, streetwear, poleras oversize, camisas y chaquetas denim"}.
- Moneda Oficial: Bolivianos (BOB - Bs.).
- Catálogo Actual de Productos:
{catalog_text}
- Sucursales Activas:
{branches_text}
- Tarifas de Delivery:
{delivery_text}
- Políticas de Garantía y Devolución:
  * Plazo de cambio de prendas: Hasta 30 días posteriores a la compra con recibo o nota de venta.
  * Cambios de talla sin costo adicional (si es el mismo precio).
  * Si la nueva prenda tiene mayor precio, se abona la diferencia en efectivo o PayPal.
  * Si la prenda es de menor valor, la diferencia se abona como saldo a favor en la Billetera Virtual del cliente.
- Métodos de Pago Oficiales: Efectivo en caja de tienda física, Pago QR, Tarjeta y PayPal Checkout en dólares para compras digitales.
- Sistema Click & Collect (Reservas): El cliente puede apartar prendas desde la web o celular y tiene 48 horas para recogerlas en sucursal.

DATOS PRIVADOS DEL CLIENTE ACTUAL:
- Identidad: {user_role_context}
- Saldo Actual en Billetera Prepago: {user_wallet_balance:.2f} Bs.
- Historial de Compras Recientes:
{user_sales_text}
- Prendas Favoritas en su Guardarropa Virtual:
{user_favs_text}
- Reservas Activas:
{user_reservations_text}

DIRECTIVAS CONVERSACIONALES:
1. Responde de forma amable, empática, fluida y con tono boliviano profesional y cercano.
2. Si el cliente pregunta por sus compras previas, léele exactamente sus recibos y prendas compradas.
3. Si el cliente pregunta por su billetera prepago, indícale su saldo exacto ({user_wallet_balance:.2f} Bs.).
4. Si solicita recomendaciones de moda, analiza sus compras pasadas y favoritos para proponerle outfits.
5. Da respuestas concisas (máximo 2 a 3 párrafos cortos) con viñetas.
"""

        # 7. Inferencia con Google Gemini (Ultra-rápida con gemini-flash-lite-latest y timeout 10s)
        api_key = cls.get_api_key()
        reply = None

        if api_key:
            try:
                client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=10000))

                contents = []
                for h in history[-4:]:  # Últimas 4 interacciones para no inflar latencia
                    contents.append(
                        types.Content(
                            role="user" if h.role == "user" else "model",
                            parts=[types.Part.from_text(text=h.content)]
                        )
                    )

                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=message)]
                    )
                )

                candidate_models = ["gemini-flash-lite-latest", "gemini-3.5-flash-lite"]

                for model_name in candidate_models:
                    try:
                        response = client.models.generate_content(
                            model=model_name,
                            contents=contents,
                            config=types.GenerateContentConfig(
                                system_instruction=system_instruction,
                                temperature=0.3,
                                max_output_tokens=350,
                                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
                            )
                        )
                        if response and response.text:
                            reply = response.text.strip()
                            break
                    except Exception as err:
                        logger.warning(f"Fallo modelo {model_name} en chatbot: {err}")
                        continue

            except Exception as e:
                logger.error(f"Error general en Chatbot Gemini: {e}", exc_info=True)

        # 8. Fallback Local Determinista Inmediato (< 5 ms) si Gemini no respondió o falló
        if not reply:
            reply = cls._generate_local_fallback(
                message=message,
                tenant_name=tenant_name,
                is_moda=is_moda_express,
                products=products_info,
                branches=branches_info,
                current_user=current_user,
                user_sales=user_sales_info,
                user_favs=user_favs_info,
                wallet_balance=user_wallet_balance,
                is_staff_user=is_staff_user,
                business_metrics=business_metrics,
                top_products=top_products_data
            )

        # 9. Generar Chips de Sugerencias Rápidas según rol y marca
        if is_staff_user:
            suggested_prompts = [
                "¿Cuánto es el total de ventas?",
                "¿Cuáles son los productos más vendidos?",
                "Desglose de cobros por medio de pago",
                "Estado de las sucursales"
            ]
        elif is_moda_express:
            suggested_prompts = [
                "¿Qué ternos o vestidos tienen?",
                "¿Dónde queda la sucursal de Tarija?",
                "¿Cómo funciona el cambio de prendas?",
                "¿Cuáles son los métodos de pago?"
            ]
        else:
            suggested_prompts = [
                "¿Qué poleras y chaquetas tienen?",
                "¿Cómo reservo para recoger en tienda?",
                "¿Cuáles son las tarifas de delivery?",
                "¿Cómo solicito un cambio de talla?"
            ]

        return ChatbotResponse(
            reply=reply,
            suggested_prompts=suggested_prompts,
            tenant_name=tenant_name,
            tenant_slug=tenant_slug_val
        )

    @classmethod
    def _generate_local_fallback(
        cls,
        message: str,
        tenant_name: str,
        is_moda: bool,
        products: List[str],
        branches: List[str],
        current_user: Optional[User] = None,
        user_sales: Optional[List[str]] = None,
        user_favs: Optional[List[str]] = None,
        wallet_balance: float = 0.0,
        is_staff_user: bool = False,
        business_metrics: Optional[dict] = None,
        top_products: Optional[List[dict]] = None
    ) -> str:
        """Motor local de respaldo ultra-rápido (< 5 ms) ante fallos de conexión externa."""
        msg = message.lower()

        # Si el usuario es administrador o staff
        if is_staff_user and any(w in msg for w in ["venta", "vendido", "vendimos", "facturac", "ingreso", "caja", "cuanto es el total", "total de ventas", "cuanto tenemos"]):
            b = business_metrics or {}
            ingresos = b.get("ingresos_totales", 0.0)
            total_v = b.get("total_ventas", 0)
            ticket = b.get("ticket_promedio", 0.0)
            efectivo = b.get("ventas_efectivo", 0.0)
            qr = b.get("ventas_qr", 0.0)
            tarjeta = b.get("ventas_tarjeta", 0.0)
            paypal = b.get("ventas_paypal", 0.0)
            return (
                f"📊 **Informe Ejecutivo de Ventas — {tenant_name}**\n\n"
                f"- **Facturación Total Acumulada:** **Bs. {ingresos:,.2f}**\n"
                f"- **Ventas Registradas:** **{total_v} transacciones completadas**\n"
                f"- **Ticket Promedio:** **Bs. {ticket:,.2f}**\n\n"
                f"💳 **Desglose de Cobros por Medio de Pago:**\n"
                f"• Efectivo en mostrador: Bs. {efectivo:,.2f}\n"
                f"• Cobros por QR: Bs. {qr:,.2f}\n"
                f"• Tarjetas Débito/Crédito: Bs. {tarjeta:,.2f}\n"
                f"• Pasarela Digital PayPal: Bs. {paypal:,.2f}\n\n"
                f"¿Deseas consultar el ranking de productos más vendidos o consultar una sucursal en específico?"
            )

        if is_staff_user and any(w in msg for w in ["top", "mas vendido", "rotacion", "mas vendidas", "mejores productos"]):
            if top_products:
                top_lines = [
                    f"#{i} **{tp.get('producto_nombre', 'Prenda')}** ({tp.get('talla', '')}/{tp.get('color', '')}): "
                    f"{tp.get('unidades_vendidas', 0)} unidades (Bs. {tp.get('ingresos_generados', 0.0):,.2f})"
                    for i, tp in enumerate(top_products, 1)
                ]
                return f"🏆 **Top Prendas Más Vendidas — {tenant_name}**\n\n" + "\n".join(top_lines)

        if any(w in msg for w in ["mis compras", "que compre", "mis pedidos", "historial", "recibo"]):
            if not current_user:
                return (
                    "Para consultar tus compras previas o notas de venta, por favor inicia sesión con tu cuenta. "
                    "Así podré mostrarte el detalle exacto de tus recibos."
                )
            if user_sales:
                sales_str = "\n".join(user_sales[:3])
                return (
                    f"¡Hola, {current_user.full_name}! Estas son tus compras recientes registradas en **{tenant_name}**:\n\n"
                    f"{sales_str}\n\n"
                    "Si necesitas realizar un cambio de talla o consultar la garantía, recuerda que tienes 30 días con tu nota de venta."
                )
            return (
                f"¡Hola, {current_user.full_name}! Actualmente no tienes compras registradas en tu historial. "
                "¡Visita nuestro catálogo para armar tu primer pedido!"
            )

        if any(w in msg for w in ["saldo", "billetera", "wallet", "cuanta plata tengo", "cuanto tengo"]):
            if not current_user:
                return "Para ver tu saldo disponible en la Billetera Prepago, por favor inicia sesión en tu cuenta."
            return (
                f"💳 **Billetera Virtual Prepago ({tenant_name})**\n\n"
                f"Tu saldo actual disponible es de: **{wallet_balance:.2f} Bs.**\n\n"
                "Puedes usar este saldo para pagar directamente en tienda física o en la App Móvil (incluso en Modo Offline sin conexión a internet)."
            )

        if any(w in msg for w in ["recomienda", "recomendacion", "que me queda", "combinar", "outfit", "estilo"]):
            fav_hint = ""
            if user_favs:
                fav_hint = f"Tomando en cuenta tus prendas favoritas ({', '.join([f.split('(')[0].replace('-', '').strip() for f in user_favs[:2]])}), "
            sample = "\n".join(products[:3]) if products else "nuestras poleras y chaquetas en tendencia."
            return (
                f"👕 **Recomendación de Moda ({tenant_name})**:\n\n"
                f"{fav_hint}te sugerimos combinar cortes contemporáneos en tonos neutros con calzado urbano. "
                f"Prendas destacadas recomendadas para ti hoy:\n\n{sample}\n\n"
                "¿Te gustaría que te asesore sobre alguna talla en específico?"
            )

        if any(w in msg for w in ["hola", "buenas", "buenos dias", "buenas tardes", "saludos"]):
            if is_staff_user:
                return (
                    f"¡Hola, {current_user.full_name if current_user else 'Administrador'}! "
                    f"Soy tu Asistente Ejecutivo con IA para **{tenant_name}**. "
                    "Estoy listo para brindarte métricas de ventas, facturación, productos más vendidos, stock en sucursales o políticas de tienda. "
                    "¿Qué deseas consultar?"
                )
            return (
                f"¡Hola! Bienvenido a **{tenant_name}**. Soy tu asistente virtual de retail. "
                "Puedo ayudarte a buscar prendas, verificar tallas, consultar nuestras sucursales o explicarte "
                "cómo apartar ropa con *Click & Collect*. ¿Qué estilo estás buscando hoy?"
            )

        if any(w in msg for w in ["sucursal", "tienda", "donde", "ubicacion", "direccion"]):
            b_list = "\n".join(branches[:3]) if branches else "Nuestras sucursales atienden de 09:00 a 20:00."
            return (
                f"Actualmente **{tenant_name}** cuenta con las siguientes sucursales activas:\n\n"
                f"{b_list}\n\n"
                "¡Puedes visitarnos o hacer tu reserva online y retirar en el mostrador sin costo extra!"
            )

        if any(w in msg for w in ["cambio", "devolucion", "garantia", "talla no me quedo"]):
            return (
                "Nuestra **Política de Garantía y Cambios** te permite cambiar tu prenda hasta en **30 días** "
                "posteriores a la compra presentando tu recibo o nota de venta en cualquier sucursal. "
                "El cambio por otra talla del mismo producto no tiene recargo; si eliges una prenda de mayor precio, "
                "solo abonas la diferencia en caja."
            )

        if any(w in msg for w in ["pago", "pagar", "paypal", "efectivo", "metodo", "qr"]):
            return (
                "Nuestros métodos de pago oficiales autorizados son:\n"
                "1. **Efectivo** en caja de cualquiera de nuestras sucursales físicas (con cálculo automático de vuelto).\n"
                "2. **Pago QR y Tarjetas** en mostrador.\n"
                "3. **Billetera Móvil Prepago** para compras rápidas tanto online como offline.\n"
                "4. **PayPal Checkout** para compras digitales desde la App Móvil o Tienda Web (en dólares al tipo de cambio 6.96 Bs/USD)."
            )

        if any(w in msg for w in ["reserva", "click and collect", "apartar", "recojo"]):
            return (
                "¡El servicio **Click & Collect** es muy sencillo! Eliges tu prenda favorita en la Web o App Móvil, "
                "seleccionas la sucursal de recojo y generas tu reserva. El encargado de tienda prepara tu prenda "
                "y tienes hasta **48 horas** para retirarla y pagarla en caja."
            )

        sample_prods = "\n".join(products[:3]) if products else "Variedad en prendas casuales y formales."
        return (
            f"En **{tenant_name}** disponemos de las mejores prendas de la temporada:\n\n"
            f"{sample_prods}\n\n"
            "¿Deseas conocer más detalles sobre alguna prenda en particular o sobre nuestras opciones de envío?"
        )
