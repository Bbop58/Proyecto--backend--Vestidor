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
from app.schemas.chatbot import ChatMessage, ChatbotResponse

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
        Inyecta el catálogo, sucursales y políticas de la empresa en el System Prompt.
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

        # 2. Recopilar datos reales de la BD para este tenant
        products_info = []
        if tenant:
            products = (
                db.query(Product)
                .filter(Product.tenant_id == tenant.id, Product.activo == True)
                .limit(15)
                .all()
            )
            for p in products:
                variants = db.query(ProductVariant).filter(
                    ProductVariant.producto_id == p.id,
                    ProductVariant.activo == True
                ).all()
                tallas = list(set([v.talla for v in variants if v.talla]))
                colores = list(set([v.color for v in variants if v.color]))
                products_info.append(
                    f"- {p.nombre}: {float(p.precio_base):.2f} Bs. "
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

        user_role_context = "Cliente / Visitante"
        if current_user and current_user.role:
            user_role_context = f"Empleado con rol {current_user.role.name} ({current_user.full_name})"

        catalog_text = "\n".join(products_info) if products_info else "Catálogo disponible en tienda."
        branches_text = "\n".join(branches_info) if branches_info else "Sucursales disponibles en red."
        delivery_text = "\n".join(delivery_info) if delivery_info else "Envíos disponibles según zona (15 a 25 Bs)."

        # 3. Construir System Prompt especializado
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
  * Plazo de cambio de prendas: Hasta 15 días posteriores a la compra con recibo o número de venta.
  * Cambios de talla sin costo adicional (si es el mismo precio).
  * Si la nueva talla tiene sobreprecio, solo se abona la diferencia en mostrador.
- Métodos de Pago Aceptados: Efectivo en caja (con cálculo de vuelto), QR Simple interoperable, Tarjetas Visa/Mastercard y PayPal Sandbox en dólares (tipo de cambio oficial 6.96 Bs/USD).
- Sistema Click & Collect (Reservas): El cliente puede apartar prendas desde la web o celular y tiene 48 horas para recogerlas en la sucursal elegida.

Usuario que te contacta: {user_role_context}.

DIRECTIVAS CONVERSACIONALES:
1. Responde de forma amable, empática, fluida y con tono boliviano profesional y cercano.
2. Da respuestas concisas (máximo 2 a 3 párrafos cortos). Usa viñetas para enumerar opciones si es útil.
3. Menciona siempre precios en Bolivianos (Bs.) y cita prendas reales de la lista proporcionada.
4. Si preguntan por horarios o atención, indica que las sucursales abren de Lunes a Sábado de 09:00 a 20:00.
5. Si el usuario es un empleado, ayúdalo con procesos del sistema (cambios de prenda en /sales, reservas en /reservations, etc.).
"""

        # 4. Intentar inferencia con Google Gemini
        api_key = cls.get_api_key()
        reply = None

        if api_key:
            try:
                client = genai.Client(api_key=api_key)

                # Convertir historial al formato de Gemini
                contents = []
                for h in history[-6:]:  # Últimas 6 interacciones para no saturar tokens
                    contents.append(
                        types.Content(
                            role="user" if h.role == "user" else "model",
                            parts=[types.Part.from_text(text=h.content)]
                        )
                    )

                # Agregar mensaje actual
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=message)]
                    )
                )

                candidate_models = ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]
                last_err = None

                for model_name in candidate_models:
                    try:
                        response = client.models.generate_content(
                            model=model_name,
                            contents=contents,
                            config=types.GenerateContentConfig(
                                system_instruction=system_instruction,
                                temperature=0.7,
                                max_output_tokens=600,
                            )
                        )
                        if response and response.text:
                            reply = response.text.strip()
                            break
                    except Exception as err:
                        last_err = err
                        logger.warning(f"Fallo modelo {model_name} en chatbot: {err}")
                        continue

            except Exception as e:
                logger.error(f"Error general en Chatbot Gemini: {e}", exc_info=True)

        # 5. Fallback Local Determinista (Si Gemini no respondió o no hay API key)
        if not reply:
            reply = cls._generate_local_fallback(message, tenant_name, is_moda_express, products_info, branches_info)

        # 6. Generar Chips de Sugerencias Rápidas según la marca
        suggested_prompts = (
            [
                "¿Qué ternos o vestidos tienen?",
                "¿Dónde queda la sucursal de Tarija?",
                "¿Cómo funciona el cambio de prendas?",
                "¿Cuáles son los métodos de pago?"
            ]
            if is_moda_express
            else [
                "¿Qué poleras y chaquetas tienen?",
                "¿Cómo reservo para recoger en tienda?",
                "¿Cuáles son las tarifas de delivery?",
                "¿Cómo solicito un cambio de talla?"
            ]
        )

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
        branches: List[str]
    ) -> str:
        """Motor local de respaldo ante fallos de conexión externa."""
        msg = message.lower()

        if any(w in msg for w in ["hola", "buenas", "buenos dias", "buenas tardes", "saludos"]):
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
                "Nuestra **Política de Garantía y Cambios** te permite cambiar tu prenda hasta en **15 días** "
                "posteriores a la compra presentando tu recibo en cualquier sucursal. "
                "El cambio por otra talla del mismo producto no tiene recargo; si eliges una prenda de mayor precio, "
                "solo abonas la diferencia en caja."
            )

        if any(w in msg for w in ["pago", "pagar", "qr", "paypal", "tarjeta", "efectivo"]):
            return (
                "Aceptamos 4 métodos de pago tanto en tiendas como digitalmente:\n"
                "1. **Efectivo** en mostrador con cálculo automático de vuelto.\n"
                "2. **QR Simple** interoperable con cualquier banco nacional.\n"
                "3. **Tarjetas de débito y crédito** Visa / Mastercard.\n"
                "4. **PayPal Sandbox** en dólares estadounidenses (tipo de cambio oficial 6.96 Bs/USD)."
            )

        if any(w in msg for w in ["reserva", "click and collect", "apartar", "recojo"]):
            return (
                "¡El servicio **Click & Collect** es muy sencillo! Eliges tu prenda favorita en la Web o App Móvil, "
                "seleccionas la sucursal de recojo y generas tu reserva. El encargado de tienda prepara tu prenda "
                "y tienes hasta **48 horas** para retirarla y pagarla en caja."
            )

        # Respuesta genérica con productos
        sample_prods = "\n".join(products[:3]) if products else "Variedad en prendas casuales y formales."
        return (
            f"En **{tenant_name}** disponemos de las mejores prendas de la temporada:\n\n"
            f"{sample_prods}\n\n"
            "¿Deseas conocer más detalles sobre alguna prenda en particular o sobre nuestras opciones de envío?"
        )
