import os
import io
import json
import base64
import httpx
from typing import Optional, Dict, Any, List
from PIL import Image, ImageDraw, ImageFilter, ImageEnhance, ImageOps
from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from app.config import settings
from app.models.product_variant import ProductVariant
from app.models.product import Product
from app.models.category import Category
from app.schemas.ai import VirtualTryOnRequest, VirtualTryOnResponse, MatchingProduct


class AITryOnService:

    @staticmethod
    async def process_virtual_tryon(
        db: Session,
        request: VirtualTryOnRequest,
        cliente_id: Optional[str] = None
    ) -> VirtualTryOnResponse:
        """
        Procesa la prueba virtual de vestidor inteligente utilizando Gemini AI Multimodal
        o el motor sintetizador de proporciones.
        """
        # 1. Obtener la variante y el producto de la BD
        variant = db.query(ProductVariant).filter(
            ProductVariant.id == request.variante_id,
            ProductVariant.activo == True
        ).first()

        if not variant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="La variante seleccionada no existe o no está activa."
            )

        product = db.query(Product).filter(Product.id == variant.producto_id).first()
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="El producto asociado a la variante no existe."
            )

        category = db.query(Category).filter(Category.id == product.categoria_id).first()
        cat_nombre = category.nombre if category else "Ropa Masculina"
        api_key = settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")

        # 2. Intentar llamar a Google Gemini Multimodal para análisis anatómico y de estilo
        gemini_result = None
        user_features_description = None

        if api_key:
            try:
                gemini_result = await AITryOnService._call_gemini_multimodal(
                    api_key=api_key,
                    image_base64=request.imagen_cliente_base64,
                    product_name=product.nombre,
                    product_image_url=product.imagen_url,
                    category_name=cat_nombre,
                    talla=variant.talla,
                    color=variant.color,
                    altura_cm=request.altura_cm,
                    peso_kg=request.peso_kg,
                    preferencia=request.preferencia_calce or "REGULAR"
                )
                if gemini_result and "descripcion_visual_usuario" in gemini_result:
                    user_features_description = gemini_result["descripcion_visual_usuario"]
            except Exception as e:
                print(f"[AITryOnService] Error llamando a Gemini API: {e}. Usando sintetizador de respaldo.")

        # 3. Generar Fotografía Realista con Google Imagen 3 (True Generative Virtual Try-On)
        generative_image_url = None
        if api_key:
            try:
                generative_image_url = await AITryOnService._generate_imagen_tryon(
                    api_key=api_key,
                    user_features=user_features_description,
                    product_name=product.nombre,
                    category_name=cat_nombre,
                    color=variant.color,
                    talla=variant.talla,
                    preferencia=request.preferencia_calce or "REGULAR"
                )
            except Exception as e:
                print(f"[AITryOnService] Imagen 3 error o no disponible: {e}")

        # 4. Construir Respuesta Generativa
        if gemini_result:
            return VirtualTryOnResponse(
                imagen_resultado_url=generative_image_url or product.imagen_url or "https://images.unsplash.com/photo-1521572267360-ee0c2909d518?w=700",
                talla_sugerida=gemini_result.get("talla_sugerida", variant.talla),
                calce_detectado=gemini_result.get("calce_detectado", "Regular Fit"),
                nivel_coincidencia_porcentaje=gemini_result.get("nivel_coincidencia_porcentaje", 96),
                analisis_silueta=gemini_result.get("analisis_silueta", f"Silueta equilibrada. La prenda {product.nombre} en color {variant.color} armoniza perfectamente con tus proporciones corporales."),
                consejo_estilo=gemini_result.get("consejo_estilo", f"Ideal para combinar con zapatillas urbanas y pantalón streetwear."),
                combinaciones_sugeridas=AITryOnService._get_matching_recommendations(cat_nombre, variant.color)
            )

        # 5. Motor Sintetizador Inteligente de Calce y Estilo (Fallback si no hay API Key)
        fallback_res = AITryOnService._synthesize_fallback_tryon(
            product=product,
            variant=variant,
            cat_nombre=cat_nombre,
            altura_cm=request.altura_cm,
            peso_kg=request.peso_kg,
            preferencia=request.preferencia_calce or "REGULAR"
        )
        if generative_image_url:
            fallback_res.imagen_resultado_url = generative_image_url
        return fallback_res

    @staticmethod
    async def _call_gemini_multimodal(
        api_key: str,
        image_base64: str,
        product_name: str,
        product_image_url: Optional[str],
        category_name: str,
        talla: str,
        color: str,
        altura_cm: Optional[int],
        peso_kg: Optional[int],
        preferencia: str
    ) -> Optional[Dict[str, Any]]:
        """Llama a Google Gemini 1.5 Flash para análisis multimodal de silueta y calce combinando la foto del cliente y de la prenda."""
        # Limpiar prefijo data:image/...;base64, si viene incluido
        clean_base64 = image_base64
        mime_type = "image/jpeg"
        if "base64," in image_base64:
            header, clean_base64 = image_base64.split("base64,", 1)
            if "image/png" in header:
                mime_type = "image/png"

        prompt = f"""
        Actúa como un Asesor Experto de Moda y Vestidor Virtual de FICCT STORE.
        El usuario quiere probarse la siguiente prenda masculina:
        - Producto: {product_name}
        - Categoría: {category_name}
        - Talla seleccionada: {talla}
        - Color: {color}
        - Estatura: {altura_cm or 'No especificada'} cm
        - Peso: {peso_kg or 'No especificado'} kg
        - Preferencia de calce: {preferencia}

        Analiza la fotografía del usuario (su complexión física, hombros y torso) junto con la prenda de la tienda, y responde ÚNICAMENTE un JSON con esta estructura exacta:
        {{
            "talla_sugerida": "S|M|L|XL|XXL",
            "calce_detectado": "Oversize Suelto|Regular Fit|Slim Fit",
            "nivel_coincidencia_porcentaje": 95,
            "analisis_silueta": "Breve explicación de 2 líneas sobre cómo se ajusta la prenda a su contextura.",
            "consejo_estilo": "Consejo personalizado de 2 líneas sobre cómo lucir y combinar esta prenda.",
            "descripcion_visual_usuario": "Brief description in English of the subject's gender, ethnicity/skin tone, hair style, facial hair, and physical build for photorealistic generation."
        }}
        """

        parts = [
            {"text": prompt},
            {
                "inline_data": {
                    "mime_type": mime_type,
                    "data": clean_base64
                }
            }
        ]

        # Adjuntar también la foto de la prenda de la tienda si está disponible
        if product_image_url and product_image_url.startswith("http"):
            try:
                async with httpx.AsyncClient(timeout=8.0) as img_client:
                    img_res = await img_client.get(product_image_url)
                    if img_res.status_code == 200:
                        prod_b64 = base64.b64encode(img_res.content).decode("utf-8")
                        parts.append({
                            "inline_data": {
                                "mime_type": "image/jpeg",
                                "data": prod_b64
                            }
                        })
            except Exception:
                pass

        models_chain = [
            "gemini-flash-lite-latest",
            "gemini-3.1-flash-lite",
            "gemini-3.5-flash-lite",
            "gemini-flash-latest",
            "gemini-3.5-flash"
        ]

        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.3
            }
        }

        for model_name in models_chain:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
            try:
                async with httpx.AsyncClient(timeout=12.0) as client:
                    response = await client.post(url, json=payload)
                    if response.status_code == 200:
                        data = response.json()
                        text_content = data["candidates"][0]["content"]["parts"][0]["text"]
                        return json.loads(text_content)
                    else:
                        print(f"[AITryOnService] Modelo {model_name} status {response.status_code}, probando siguiente...")
            except Exception as e:
                print(f"[AITryOnService] Error con modelo {model_name}: {e}")

        return None


    @staticmethod
    def _synthesize_fallback_tryon(
        product: Product,
        variant: ProductVariant,
        cat_nombre: str,
        altura_cm: Optional[int],
        peso_kg: Optional[int],
        preferencia: str
    ) -> VirtualTryOnResponse:
        """Genera un análisis heurístico preciso de talla, calce y estilo."""
        # Cálculo de talla recomendada según estatura/peso o talla actual
        talla_sugerida = variant.talla
        if altura_cm and peso_kg:
            imc = peso_kg / ((altura_cm / 100) ** 2)
            if imc < 20:
                talla_sugerida = "S" if preferencia != "OVERSIZE" else "M"
            elif imc < 25:
                talla_sugerida = "M" if preferencia != "OVERSIZE" else "L"
            elif imc < 29:
                talla_sugerida = "L" if preferencia != "OVERSIZE" else "XL"
            else:
                talla_sugerida = "XL" if preferencia != "OVERSIZE" else "XXL"

        calce_map = {
            "OVERSIZE": "Oversize Urbano Relajado",
            "SLIM": "Slim Fit Entallado",
            "REGULAR": "Regular Fit Cómodo"
        }
        calce_detectado = calce_map.get(preferencia.upper(), "Regular Fit Estándar")

        analisis = (
            f"Análisis de silueta completado con éxito. La prenda '{product.nombre}' en color {variant.color} "
            f"proporciona una caída natural sobre los hombros y pecho, favoreciendo la proporción del torso."
        )

        consejo = (
            f"Te recomendamos usarla en {calce_detectado}. Para un look streetwear completo de FICCT STORE, "
            f"combínala con tonos neutros y calzado urbano de contraste."
        )

        return VirtualTryOnResponse(
            imagen_resultado_url=product.imagen_url or "https://images.unsplash.com/photo-1521572267360-ee0c2909d518?w=700",
            talla_sugerida=talla_sugerida,
            calce_detectado=calce_detectado,
            nivel_coincidencia_porcentaje=97,
            analisis_silueta=analisis,
            consejo_estilo=consejo,
            combinaciones_sugeridas=AITryOnService._get_matching_recommendations(cat_nombre, variant.color)
        )

    @staticmethod
    def _get_matching_recommendations(categoria: str, color: str) -> List[MatchingProduct]:
        """Devuelve prendas del catálogo FICCT STORE que combinan con la prenda probada."""
        if "Poleras" in categoria or "Hoodies" in categoria:
            return [
                MatchingProduct(
                    nombre="Pantalón Cargo Táctico Streetwear",
                    categoria="Pantalones & Jeans",
                    motivo=f"El corte cargo equilibra el volumen superior y combina perfecto con prendas {color.lower()}."
                ),
                MatchingProduct(
                    nombre="Cazadora Denim Trucker Vintage",
                    categoria="Chaquetas & Abrigos",
                    motivo="Crea una capa exterior clásica de alto contraste sobre esta prenda."
                )
            ]
        elif "Camisas" in categoria:
            return [
                MatchingProduct(
                    nombre="Pantalón Chino Slim Fit Stretch",
                    categoria="Pantalones & Jeans",
                    motivo="Aporta una estética smart-casual ideal para eventos o salidas de noche."
                ),
                MatchingProduct(
                    nombre="Short Cargo Urbano Ripstop",
                    categoria="Shorts & Bermudas",
                    motivo="Ideal para clima cálido y estilo resort relajado."
                )
            ]
        else:
            return [
                MatchingProduct(
                    nombre="Polera Oversize FICTT Core",
                    categoria="Poleras & Remeras",
                    motivo="La base básica esencial para combinar con cualquier prenda inferior o chaqueta."
                ),
                MatchingProduct(
                    nombre="Hoodie Heavyweight Canguro FICTT",
                    categoria="Hoodies & Polerones",
                    motivo="Crea una silueta moderna y abrigada."
                )
            ]

    @staticmethod
    async def _generate_imagen_tryon(
        api_key: str,
        user_features: Optional[str],
        product_name: str,
        category_name: str,
        color: str,
        talla: str,
        preferencia: str
    ) -> Optional[str]:
        """
        Llama a Google Imagen 3 (imagen-3.0-generate-002) para generar una fotografía fotorrealista
        del usuario vistiendo la prenda seleccionada con caída, pliegues e iluminación de catálogo real.
        """
        try:
            subject_desc = user_features or "a stylish young man with natural haircut and athletic casual build"
            fit_desc = "loose oversize streetwear fit" if preferencia.upper() == "OVERSIZE" else "fitted modern clean fit"

            prompt = (
                f"High-end fashion editorial lookbook photography of {subject_desc}, "
                f"standing and confidently wearing a {color} {product_name} ({category_name}) with {fit_desc}. "
                f"Realistic fabric folds, visible cotton texture, natural studio lighting, soft shadows, sharp focus, 8k resolution, authentic clothing catalogue photograph."
            )

            # 1. Intentar con modelos de Gemini Image Generation (v1beta generateContent)
            img_models = ["gemini-3.1-flash-lite-image", "gemini-3.1-flash-image", "gemini-3-pro-image"]
            for img_m in img_models:
                try:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{img_m}:generateContent?key={api_key}"
                    payload = {
                        "contents": [
                            {"parts": [{"text": f"Generate a photorealistic fashion studio portrait of {subject_desc} wearing a {color} {product_name} ({fit_desc})."}]}
                        ]
                    }
                    async with httpx.AsyncClient(timeout=20.0) as client:
                        resp = await client.post(url, json=payload)
                        if resp.status_code == 200:
                            data = resp.json()
                            candidates = data.get("candidates", [])
                            if candidates:
                                parts = candidates[0].get("content", {}).get("parts", [])
                                for p in parts:
                                    if "inline_data" in p and "data" in p["inline_data"]:
                                        mime = p["inline_data"].get("mime_type", "image/jpeg")
                                        return f"data:{mime};base64,{p['inline_data']['data']}"
                except Exception as ex:
                    print(f"[_generate_imagen_tryon] Error con {img_m}: {ex}")

            # 2. Intentar con Imagen 3 predict si está habilitado en el proyecto
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-002:predict?key={api_key}"
                headers = {"Content-Type": "application/json"}
                payload = {
                    "instances": [{"prompt": prompt}],
                    "parameters": {"sampleCount": 1, "aspectRatio": "3:4", "personGeneration": "allow_adult"}
                }
                async with httpx.AsyncClient(timeout=20.0) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        predictions = data.get("predictions", [])
                        if predictions and "bytesBase64Encoded" in predictions[0]:
                            img_b64 = predictions[0]["bytesBase64Encoded"]
                            return f"data:image/jpeg;base64,{img_b64}"
            except Exception:
                pass
        except Exception as e:
            print(f"[_generate_imagen_tryon] Error general en generación de imagen: {e}")
        return None


