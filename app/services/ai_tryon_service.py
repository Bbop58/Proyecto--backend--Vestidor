import os
import base64
import logging
import httpx
from typing import Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from google import genai
from google.genai import types
from google.genai import errors

from app.config import settings
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.schemas.ai import VirtualTryOnRequest, VirtualTryOnResponse

logger = logging.getLogger("ai_tryon_service")


class AITryOnService:

    @staticmethod
    def process_virtual_tryon(
        db: Session,
        request: VirtualTryOnRequest,
        cliente_id: Optional[str] = None
    ) -> VirtualTryOnResponse:
        """
        Procesa el vestidor virtual con la API oficial de Google Gemini (google-genai).
        Recibe la foto del usuario y el ID del producto que está viendo, y genera
        una fotografía realista de la persona vistiendo la prenda manteniendo intactos
        su rostro, cuerpo, pose y fondo original.
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

        # 2. Obtener la clave de API de Gemini
        api_key = settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            logger.error("GEMINI_API_KEY no configurada.")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="No se pudo generar la imagen del vestidor virtual en este momento. Por favor, intenta más tarde."
            )

        # 3. Limpiar y decodificar la foto del usuario
        user_raw = request.imagen_cliente_base64
        user_mime = "image/jpeg"
        if "base64," in user_raw:
            header, user_b64 = user_raw.split("base64,", 1)
            if "png" in header.lower():
                user_mime = "image/png"
            elif "webp" in header.lower():
                user_mime = "image/webp"
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

        # 4. Obtener la imagen de la prenda
        garment_bytes: Optional[bytes] = None
        garment_mime = "image/jpeg"
        if product.imagen_url and product.imagen_url.startswith("http"):
            try:
                resp = httpx.get(product.imagen_url, timeout=4.0)
                if resp.status_code == 200:
                    garment_bytes = resp.content
                    content_type = resp.headers.get("content-type", "")
                    if "png" in content_type:
                        garment_mime = "image/png"
                    elif "webp" in content_type:
                        garment_mime = "image/webp"
            except Exception as e:
                logger.info(f"No se pudo descargar la imagen remota {product.imagen_url}: {e}")

        if not garment_bytes:
            garment_bytes = user_bytes
            garment_mime = user_mime

        # 5. Prompt de alta fidelidad para el Vestidor Virtual
        prompt_text = (
            f"Image 1 is a photograph of a real person. "
            f"Image 2 is a clothing item from our retail catalogue: '{product.nombre}'. "
            f"Generate a realistic photograph of this EXACT same person from Image 1 wearing the clothing item from Image 2. "
            f"Crucial requirements: "
            f"1. Strictly preserve the person's face, facial features, hair, skin tone, body shape, and proportions from Image 1. "
            f"2. Keep the person's exact posture, pose, camera angle, and original lighting intact. "
            f"3. Keep the original background and environment of Image 1 completely intact. "
            f"4. Replace the upper/relevant clothing with the garment from Image 2, naturally draping and fitting their torso with realistic fabric texture, natural wrinkles, and proper contact shadows. "
            f"5. The final image must look completely natural and photorealistic, avoiding any visible cutouts or digital montage effect."
        )

        # 6. Invocar la API oficial de Google Gemini usando google-genai
        try:
            client = genai.Client(api_key=api_key)
            contents = [
                types.Part.from_text(text=prompt_text),
                types.Part.from_bytes(data=user_bytes, mime_type=user_mime),
                types.Part.from_bytes(data=garment_bytes, mime_type=garment_mime),
            ]

            response = client.models.generate_content(
                model="gemini-3.1-flash-image",
                contents=contents
            )

            result_b64: Optional[str] = None
            if response.candidates:
                for part in response.candidates[0].content.parts:
                    if hasattr(part, "inline_data") and part.inline_data:
                        p_mime = part.inline_data.mime_type or "image/png"
                        p_b64 = base64.b64encode(part.inline_data.data).decode("utf-8")
                        result_b64 = f"data:{p_mime};base64,{p_b64}"
                        break

            if not result_b64:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="No se pudo generar la imagen del vestidor virtual en este momento. Por favor, intenta más tarde."
                )

            return VirtualTryOnResponse(imagen_resultado_base64=result_b64)

        except errors.APIError as api_err:
            logger.warning(f"Error en API Gemini: {api_err}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No se pudo generar la imagen del vestidor virtual en este momento. Por favor, intenta más tarde."
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error inesperado en vestidor virtual: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="No se pudo generar la imagen del vestidor virtual en este momento. Por favor, intenta más tarde."
            )
