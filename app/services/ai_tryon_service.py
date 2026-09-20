import os
import base64
import logging
import tempfile
import httpx
from typing import Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from gradio_client import Client, handle_file

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
        Procesa el vestidor virtual con el modelo de IA IDM-VTON (Virtual Try-On).
        Recibe la foto del usuario y el ID del producto que está viendo en la tienda,
        ajusta con precisión la prenda sobre el cuerpo de la persona conservando intactos
        su rostro, cuerpo, pose y el fondo original, 100% libre de costo.
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
        if product.imagen_url and product.imagen_url.startswith("http"):
            try:
                resp = httpx.get(product.imagen_url, timeout=10.0)
                if resp.status_code == 200:
                    garment_bytes = resp.content
            except Exception as e:
                logger.info(f"No se pudo descargar la imagen remota {product.imagen_url}: {e}")

        if not garment_bytes:
            garment_bytes = user_bytes

        # 4. Guardar archivos temporales locales para el cliente de predicción
        person_tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        garment_tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)

        try:
            person_tmp.write(user_bytes)
            person_tmp.flush()
            person_tmp.close()

            garment_tmp.write(garment_bytes)
            garment_tmp.flush()
            garment_tmp.close()

            logger.info(f"Iniciando inferencia de vestidor virtual para producto '{product.nombre}'...")

            # 5. Invocar el motor de IA IDM-VTON
            client = Client("yisol/IDM-VTON")
            result = client.predict(
                dict={
                    "background": handle_file(person_tmp.name),
                    "layers": [],
                    "composite": None
                },
                garm_img=handle_file(garment_tmp.name),
                garment_des=product.nombre or "Prenda de vestir de tienda",
                is_checked=True,
                is_checked_crop=False,
                denoise_steps=30,
                seed=42,
                api_name="/tryon"
            )

            output_image_path = result[0] if isinstance(result, (list, tuple)) else result
            if not output_image_path or not os.path.exists(output_image_path):
                logger.error(f"Ruta de imagen de salida inválida: {output_image_path}")
                raise Exception("El modelo no devolvió una imagen válida.")

            with open(output_image_path, "rb") as f:
                res_bytes = f.read()

            result_b64 = f"data:image/png;base64,{base64.b64encode(res_bytes).decode('utf-8')}"
            logger.info(f"Vestidor virtual procesado exitosamente ({len(res_bytes)} bytes)")
            return VirtualTryOnResponse(imagen_resultado_base64=result_b64)

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error procesando vestidor virtual: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="No se pudo generar la imagen del vestidor virtual en este momento. Por favor, intenta más tarde."
            )
        finally:
            for tmp_path in [person_tmp.name, garment_tmp.name]:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception as e:
                        logger.debug(f"Error eliminando archivo temporal {tmp_path}: {e}")
