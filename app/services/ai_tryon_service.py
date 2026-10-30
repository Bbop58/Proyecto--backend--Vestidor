import os
import io
import base64
import logging
import tempfile
import httpx
from typing import Optional
from PIL import Image
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from gradio_client import Client, handle_file
from google import genai

from app.config import settings
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.schemas.ai import VirtualTryOnRequest, VirtualTryOnResponse

logger = logging.getLogger("ai_tryon_service")


class AITryOnService:

    @classmethod
    def _tryon_with_gemini(
        cls,
        user_bytes: bytes,
        garment_bytes: bytes,
        product_name: str
    ) -> bytes:
        """
        Inferencia de Virtual Try-On de alta velocidad con Google Gemini Flash Image (Nano Banana).
        Latencia típica: 3 a 5 segundos. Sin colas ni cooldown de ZeroGPU.
        """
        api_key = settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY no está configurada.")

        client = genai.Client(api_key=api_key)

        # Convertir a PIL RGB para asegurar compatibilidad de canales y formatos
        person_pil = Image.open(io.BytesIO(user_bytes)).convert("RGB")
        garment_pil = Image.open(io.BytesIO(garment_bytes)).convert("RGB")

        prompt = (
            "You are an expert virtual try-on fashion AI system. "
            "Image 1 is the customer/person. Image 2 is the clothing item. "
            f"Dress the person in Image 1 with the clothing item shown in Image 2 ({product_name}). "
            "Strict Requirements: "
            "1. Perfectly preserve the person's exact face, identity, hair, skin tone, body shape, and pose. "
            "2. Replace only the corresponding clothing area with the exact texture, pattern, logos, colors, and style of the garment in Image 2. "
            "3. Ensure natural fabric folds, realistic lighting, realistic shadows, and correct seam fitting around the body. "
            "4. Keep the background clean and natural. "
            "5. Output strictly a clean photorealistic portrait image of the person wearing the garment."
        )

        logger.info(f"Enviando solicitud de vestidor virtual a Gemini Flash Image ('gemini-2.5-flash-image')...")
        response = client.models.generate_content(
            model="gemini-2.5-flash-image",
            contents=[person_pil, garment_pil, prompt],
        )

        found_parts = []
        for candidate in response.candidates:
            if candidate.content and candidate.content.parts:
                for part in candidate.content.parts:
                    if part.inline_data and part.inline_data.data:
                        logger.info(f"Imagen generada con éxito por Gemini Flash Image ({len(part.inline_data.data)} bytes)")
                        return part.inline_data.data
                    if part.text:
                        found_parts.append(f"Text: {part.text}")
            else:
                found_parts.append(f"Finish reason: {candidate.finish_reason}")

        error_msg = f"El modelo Gemini Flash Image no devolvió datos de imagen en la respuesta. Contenido devuelto: {found_parts}"
        logger.warning(error_msg)
        raise RuntimeError(error_msg)

    @classmethod
    def _tryon_with_vton_fallback(
        cls,
        user_bytes: bytes,
        garment_bytes: bytes,
        product_name: str
    ) -> bytes:
        """
        Fallback secundario con IDM-VTON (Gradio/Hugging Face o servidor dedicado).
        """
        person_tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        garment_tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)

        try:
            person_tmp.write(user_bytes)
            person_tmp.flush()
            person_tmp.close()

            garment_tmp.write(garment_bytes)
            garment_tmp.flush()
            garment_tmp.close()

            target_endpoint = settings.VTON_API_URL.strip() if settings.VTON_API_URL else None
            token = settings.HF_TOKEN if settings.HF_TOKEN else None

            if target_endpoint:
                logger.info(f"Conectando a servidor dedicado IDM-VTON: {target_endpoint}")
                client = Client(target_endpoint)
            else:
                logger.info("Conectando a Hugging Face Space IDM-VTON...")
                try:
                    client = Client("yisol/IDM-VTON", token=token)
                except Exception as conn_err:
                    logger.warning(f"Fallo conexión por nombre de espacio ({conn_err}), intentando URL directa...")
                    client = Client("https://yisol-idm-vton.hf.space", token=token)

            result = client.predict(
                dict={
                    "background": handle_file(person_tmp.name),
                    "layers": [],
                    "composite": None
                },
                garm_img=handle_file(garment_tmp.name),
                garment_des=product_name or "Prenda de vestir de tienda",
                is_checked=True,
                is_checked_crop=False,
                denoise_steps=30,
                seed=42,
                api_name="/tryon"
            )

            output_image_path = result[0] if isinstance(result, (list, tuple)) else result
            if not output_image_path or not os.path.exists(output_image_path):
                raise RuntimeError("El modelo IDM-VTON no devolvió una imagen válida.")

            with open(output_image_path, "rb") as f:
                res_bytes = f.read()

            return res_bytes
        finally:
            for tmp_path in [person_tmp.name, garment_tmp.name]:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception as e:
                        logger.debug(f"Error eliminando archivo temporal {tmp_path}: {e}")

    @staticmethod
    def process_virtual_tryon(
        db: Session,
        request: VirtualTryOnRequest,
        cliente_id: Optional[str] = None
    ) -> VirtualTryOnResponse:
        """
        Procesa el vestidor virtual con Google Gemini Flash Image (Nano Banana)
        con fallback resiliente a IDM-VTON.
        """
        # 1. Identificar y cargar el producto de la base de datos
        product = None
        if request.producto_id:
            product = db.query(Product).filter(
                Product.id == request.producto_id,
                Product.activo == True
            ).first()

        if not product and request.variante_id:
            variant = db.query(ProductVariant).filter(
                ProductVariant.id == request.variante_id,
                ProductVariant.activo == True
            ).first()
            if variant:
                product = db.query(Product).filter(Product.id == variant.producto_id).first()

        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="El producto seleccionado no existe o no está activo."
            )

        # 2. Limpiar y decodificar la foto del usuario
        user_raw = request.imagen_cliente_base64
        if "base64," in user_raw:
            _, user_b64 = user_raw.split("base64,", 1)
        else:
            user_b64 = user_raw

        try:
            user_bytes = base64.b64decode(user_b64)
        except Exception as e:
            logger.warning(f"Error decodificando foto de usuario: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La imagen proporcionada no tiene un formato válido."
            )

        # 3. Obtener la imagen de la prenda
        garment_bytes: Optional[bytes] = None
        if product.imagen_url:
            if product.imagen_url.startswith("/static/"):
                local_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), product.imagen_url.lstrip("/"))
                if os.path.exists(local_path):
                    try:
                        with open(local_path, "rb") as f:
                            garment_bytes = f.read()
                    except Exception as err:
                        logger.warning(f"Error leyendo imagen local {local_path}: {err}")
            elif product.imagen_url.startswith("http"):
                # Si apunta a localhost/static, intentar leerlo de disco primero
                if "/static/products/" in product.imagen_url:
                    fname = product.imagen_url.split("/static/products/")[-1]
                    local_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "products", fname)
                    if os.path.exists(local_path):
                        with open(local_path, "rb") as f:
                            garment_bytes = f.read()
                if not garment_bytes:
                    try:
                        resp = httpx.get(product.imagen_url, timeout=10.0)
                        if resp.status_code == 200:
                            garment_bytes = resp.content
                    except Exception as e:
                        logger.info(f"No se pudo descargar la imagen remota {product.imagen_url}: {e}")

        if not garment_bytes:
            garment_bytes = user_bytes

        # 4. Intentar con Gemini Flash Image (Nano Banana) primero
        res_bytes = None
        if settings.GEMINI_API_KEY:
            try:
                logger.info(f"Iniciando vestidor virtual con Gemini Flash Image para '{product.nombre}'...")
                res_bytes = AITryOnService._tryon_with_gemini(
                    user_bytes=user_bytes,
                    garment_bytes=garment_bytes,
                    product_name=product.nombre or "Prenda de vestir"
                )
            except Exception as gemini_err:
                logger.warning(f"Fallo inferencia con Gemini Flash Image ({gemini_err}), intentando fallback VTON...", exc_info=True)

        # 5. Si Gemini no está configurado o falló, usar fallback IDM-VTON
        if not res_bytes:
            try:
                logger.info(f"Ejecutando inferencia con IDM-VTON fallback para '{product.nombre}'...")
                res_bytes = AITryOnService._tryon_with_vton_fallback(
                    user_bytes=user_bytes,
                    garment_bytes=garment_bytes,
                    product_name=product.nombre or "Prenda de vestir"
                )
            except Exception as vton_err:
                logger.warning(f"Motores externos de IA agotados o sin saldo ({vton_err}). Activando motor de contingencia visual...", exc_info=True)
                res_bytes = AITryOnService._tryon_with_composite_fallback(
                    user_bytes=user_bytes,
                    garment_bytes=garment_bytes,
                    product_name=product.nombre or "Prenda de vestir"
                )

        # 6. Formatear salida en base64 para la aplicación móvil
        result_b64 = f"data:image/jpeg;base64,{base64.b64encode(res_bytes).decode('utf-8')}"
        logger.info(f"Vestidor virtual procesado exitosamente ({len(res_bytes)} bytes)")
        return VirtualTryOnResponse(imagen_resultado_base64=result_b64)

    @staticmethod
    def _tryon_with_composite_fallback(
        user_bytes: bytes,
        garment_bytes: bytes,
        product_name: str
    ) -> bytes:
        """
        Genera una vista previa referencial de contingencia superponiendo la prenda
        de forma elegante sobre la foto del cliente, garantizando que el usuario
        reciba una respuesta visual fluida sin errores si los servicios externos de IA
        están saturados o sin saldo.
        """
        try:
            from PIL import Image, ImageDraw, ImageOps
            import io

            user_img = Image.open(io.BytesIO(user_bytes)).convert("RGBA")
            user_img = ImageOps.exif_transpose(user_img)

            target_w = 800
            scale = target_w / float(user_img.width)
            target_h = int(user_img.height * scale)
            user_img = user_img.resize((target_w, target_h), Image.Resampling.LANCZOS)

            if garment_bytes and garment_bytes != user_bytes:
                try:
                    garment_img = Image.open(io.BytesIO(garment_bytes)).convert("RGBA")
                    garment_w = int(target_w * 0.55)
                    g_scale = garment_w / float(garment_img.width)
                    garment_h = int(garment_img.height * g_scale)
                    garment_img = garment_img.resize((garment_w, garment_h), Image.Resampling.LANCZOS)

                    pos_x = (target_w - garment_w) // 2
                    pos_y = int(target_h * 0.28)
                    user_img.alpha_composite(garment_img, dest=(pos_x, pos_y))
                except Exception as g_err:
                    logger.warning(f"No se pudo superponer la prenda en el fallback: {g_err}")

            draw = ImageDraw.Draw(user_img)
            badge_h = 44
            badge_rect = [20, target_h - badge_h - 20, target_w - 20, target_h - 20]
            draw.rectangle(badge_rect, fill=(15, 23, 42, 220))
            draw.text(
                (40, target_h - badge_h - 8),
                f"FICCT STORE • Vista Previa Referencial ({product_name[:30]})",
                fill=(248, 250, 252, 255)
            )

            out_buf = io.BytesIO()
            user_img.convert("RGB").save(out_buf, format="JPEG", quality=85)
            return out_buf.getvalue()
        except Exception as e:
            logger.error(f"Error generando imagen de contingencia: {e}", exc_info=True)
            return user_bytes
