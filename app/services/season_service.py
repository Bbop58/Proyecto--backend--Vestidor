import uuid
from typing import List, Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from app.models.season import Season
from app.models.product import Product
from app.schemas.season import SeasonCreate, SeasonUpdate


class SeasonService:
    @staticmethod
    def get_all(db: Session, año: Optional[int] = None, activa_only: bool = False) -> List[Season]:
        query = db.query(Season)
        if año is not None:
            query = query.filter(Season.año == año)
        if activa_only:
            query = query.filter(Season.activa == True)
        return query.order_by(Season.año.desc(), Season.fecha_inicio.asc()).all()

    @staticmethod
    def get_by_id(db: Session, season_id: uuid.UUID) -> Optional[Season]:
        return db.query(Season).filter(Season.id == season_id).first()

    @staticmethod
    def get_by_name(db: Session, nombre: str) -> Optional[Season]:
        return db.query(Season).filter(Season.nombre.ilike(nombre.strip())).first()

    @staticmethod
    def create(db: Session, season_in: SeasonCreate) -> Season:
        nombre = season_in.nombre.strip()
        existing = SeasonService.get_by_name(db, nombre)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Ya existe una temporada con el nombre '{nombre}'"
            )

        # Si se crea como activa, desactivar las otras del mismo año
        if season_in.activa:
            db.query(Season).filter(
                Season.año == season_in.año,
                Season.activa == True
            ).update({"activa": False})

        season = Season(
            nombre=nombre,
            año=season_in.año,
            fecha_inicio=season_in.fecha_inicio,
            fecha_fin=season_in.fecha_fin,
            activa=season_in.activa
        )
        db.add(season)
        db.commit()
        db.refresh(season)
        return season

    @staticmethod
    def update(db: Session, season_id: uuid.UUID, season_in: SeasonUpdate) -> Season:
        season = SeasonService.get_by_id(db, season_id)
        if not season:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Temporada no encontrada"
            )

        if season_in.nombre is not None:
            nombre = season_in.nombre.strip()
            if nombre.lower() != season.nombre.lower():
                existing = SeasonService.get_by_name(db, nombre)
                if existing:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Ya existe una temporada con el nombre '{nombre}'"
                    )
            season.nombre = nombre

        if season_in.año is not None:
            season.año = season_in.año
        if season_in.fecha_inicio is not None:
            season.fecha_inicio = season_in.fecha_inicio
        if season_in.fecha_fin is not None:
            season.fecha_fin = season_in.fecha_fin

        # Validar fechas consolidadas
        if season.fecha_fin <= season.fecha_inicio:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La fecha_fin debe ser posterior a la fecha_inicio"
            )

        if season_in.activa is not None:
            if season_in.activa:
                # Desactivar otras del mismo año
                db.query(Season).filter(
                    Season.año == season.año,
                    Season.id != season_id,
                    Season.activa == True
                ).update({"activa": False})
            season.activa = season_in.activa

        db.commit()
        db.refresh(season)
        return season

    @staticmethod
    def delete(db: Session, season_id: uuid.UUID) -> bool:
        season = SeasonService.get_by_id(db, season_id)
        if not season:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Temporada no encontrada"
            )

        # Regla de negocio: No se puede eliminar una temporada con productos asociados
        has_products = db.query(Product).filter(Product.temporada.ilike(season.nombre)).first()
        if has_products:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No se puede eliminar la temporada porque tiene productos asociados"
            )

        db.delete(season)
        db.commit()
        return True
