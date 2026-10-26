import re
import json
import uuid
import logging
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_
from google import genai
from google.genai import types

from app.config import settings
from app.models.product import Product
from app.models.category import Category
from app.models.product_variant import ProductVariant
from app.models.inventory import Inventory
from app.schemas.voice_search import (
    VoiceCatalogSearchRequest,
    VoiceCatalogSearchResponse,
    ExtractedFilters,
    VoiceProductItem
)
from app.services.audit_service import audit_service

logger = logging.getLogger("voice_catalog_search")

KNOWN_COLORS = [
    "rojo", "roja", "azul", "negro", "negra", "blanco", "blanca",
    "verde", "amarillo", "amarilla", "rosado", "rosada", "rosa",
    "gris", "beige", "cafe", "marron", "celeste", "morado", "morada"
]

KNOWN_SIZES = ["xs", "s", "m", "l", "xl", "xxl", "28", "30", "32", "34", "36", "38", "40"]

KNOWN_CATEGORIES = [
    "vestido", "camisa", "pantalon", "jeans", "falda", "blusa",
    "casaca", "chaqueta", "polera", "remera", "chompa", "sueter",
    "abrigo", "short", "traje", "calzado", "zapato"
]


class VoiceCatalogSearchService:
    @staticmethod
    def _extract_entities_fallback(transcript: str) -> ExtractedFilters:
        text_lower = transcript.lower()
        extracted = ExtractedFilters()

        for c in KNOWN_CATEGORIES:
            if c in text_lower:
                extracted.categoria = c
                break

        for col in KNOWN_COLORS:
            if re.search(rf"\b{col}\b", text_lower):
                extracted.color = col.replace("roja", "rojo").replace("negra", "negro").replace("blanca", "blanco").replace("amarilla", "amarillo").replace("rosada", "rosado").replace("morada", "morado")
                break

        for s in KNOWN_SIZES:
            if re.search(rf"\btalla\s+{s}\b|\b{s}\b", text_lower):
                extracted.talla = s.upper()
                break

        price_match = re.search(r"(?:menos de|menor a|maximo|hasta)\s*(\d+(?:\.\d+)?)", text_lower)
        if price_match:
            try:
                extracted.rango_precio_max = float(price_match.group(1))
            except Exception:
                pass

        return extracted

    @staticmethod
    def _extract_entities_gemini(transcript: str) -> ExtractedFilters:
        api_key = getattr(settings, "GEMINI_API_KEY", None)
        if not api_key:
            return VoiceCatalogSearchService._extract_entities_fallback(transcript)

        try:
            client = genai.Client(api_key=api_key)
            prompt = f"""
Eres un asistente de catálogo de moda y prendas de vestir.
El usuario dijo por voz la siguiente frase: "{transcript}".
Extrae en formato JSON exacto las siguientes entidades de búsqueda de ropa (si no se menciona un campo, usa null):
{{
    "categoria": null o string (ej. "vestido", "camisa", "pantalon"),
    "color": null o string (ej. "rojo", "azul", "negro"),
    "talla": null o string (ej. "S", "M", "L", "XL"),
    "rango_precio_max": null o número flotante,
    "estilo_o_evento": null o string (ej. "fiesta", "casual", "formal")
}}
Responde ÚNICAMENTE con el bloque JSON, sin texto explicativo.
"""
            candidate_models = ["gemini-flash-latest", "gemini-2.5-flash", "gemini-1.5-flash"]
            response = None
            for m_name in candidate_models:
                try:
                    response = client.models.generate_content(
                        model=m_name,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            temperature=0.1,
                            response_mime_type="application/json"
                        )
                    )
                    if response and response.text:
                        break
                except Exception:
                    continue

            if not response or not response.text:
                return VoiceCatalogSearchService._extract_entities_fallback(transcript)

            raw = response.text.strip()
            data = json.loads(raw)
            return ExtractedFilters(
                categoria=data.get("categoria"),
                color=data.get("color"),
                talla=data.get("talla"),
                rango_precio_max=float(data["rango_precio_max"]) if data.get("rango_precio_max") else None,
                estilo_o_evento=data.get("estilo_o_evento")
            )
        except Exception as e:
            logger.warning(f"Error procesando búsqueda por voz con Gemini: {e}. Usando fallback local.")
            return VoiceCatalogSearchService._extract_entities_fallback(transcript)

    @staticmethod
    def search_catalog_by_voice(
        db: Session,
        tenant_id: uuid.UUID,
        payload: VoiceCatalogSearchRequest,
        user_id: Optional[uuid.UUID] = None,
        request=None
    ) -> VoiceCatalogSearchResponse:
        transcript = payload.transcript.strip()
        
        # 1. Extraer entidades
        filters = VoiceCatalogSearchService._extract_entities_gemini(transcript)

        # 2. Construir consulta sobre productos activos del tenant
        query = db.query(Product).filter(
            Product.tenant_id == tenant_id,
            Product.activo == True
        )

        has_variant_join = False

        if filters.categoria:
            query = query.outerjoin(Category, Product.categoria_id == Category.id).filter(
                or_(
                    Product.nombre.ilike(f"%{filters.categoria}%"),
                    Product.descripcion.ilike(f"%{filters.categoria}%"),
                    Category.nombre.ilike(f"%{filters.categoria}%")
                )
            )

        if filters.color or filters.talla or payload.sucursal_id:
            query = query.join(ProductVariant, Product.id == ProductVariant.producto_id)
            has_variant_join = True

            if filters.color:
                query = query.filter(ProductVariant.color.ilike(f"%{filters.color}%"))
            if filters.talla:
                query = query.filter(ProductVariant.talla.ilike(filters.talla))

            if payload.sucursal_id:
                query = query.join(Inventory, ProductVariant.id == Inventory.variante_id).filter(
                    Inventory.sucursal_id == payload.sucursal_id,
                    Inventory.stock_actual > 0
                )

        if filters.rango_precio_max:
            query = query.filter(Product.precio_base <= filters.rango_precio_max)

        products = query.distinct().limit(20).all()

        # 3. Fallback amplio por palabras clave si no hay resultados estructurados
        if not products:
            tokens = [t.lower() for t in transcript.split() if len(t) > 2]
            token_filters = []
            for token in tokens:
                token_filters.append(Product.nombre.ilike(f"%{token}%"))
                token_filters.append(Product.descripcion.ilike(f"%{token}%"))
            if token_filters:
                products = (
                    db.query(Product)
                    .filter(
                        Product.tenant_id == tenant_id,
                        Product.activo == True,
                        or_(*token_filters)
                    )
                    .distinct()
                    .limit(20)
                    .all()
                )

        # 4. Formatear resultados
        items: List[VoiceProductItem] = []
        for p in products:
            colores = list({v.color for v in p.variantes if v.activo and v.color})
            tallas = list({v.talla for v in p.variantes if v.activo and v.talla})
            cat_name = p.categoria.nombre if p.categoria else "General"
            
            items.append(
                VoiceProductItem(
                    id=p.id,
                    nombre=p.nombre,
                    descripcion=p.descripcion,
                    precio_base=float(p.precio_base) if p.precio_base else 0.0,
                    categoria_nombre=cat_name,
                    imagen_url=p.imagen_url,
                    colores_disponibles=colores,
                    tallas_disponibles=tallas
                )
            )

        # 5. Auditoría
        if user_id:
            audit_service.log(
                db=db,
                user_id=user_id,
                action="BUSQUEDA_VOZ_CATALOGO",
                module="Catálogo",
                details=f"Consulta: '{transcript}' -> {len(items)} prendas encontradas",
                tenant_id=tenant_id,
                request=request
            )

        return VoiceCatalogSearchResponse(
            query_original=transcript,
            filtros_interpretados=filters,
            total_encontrados=len(items),
            productos=items
        )


voice_catalog_search_service = VoiceCatalogSearchService()
