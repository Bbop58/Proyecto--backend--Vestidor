import uuid
from typing import Optional, List
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from app.models.delivery_rate import DeliveryRate
from app.models.branch import Branch
from app.schemas.delivery_rate import DeliveryRateCreate, DeliveryRateUpdate, DeliveryRateResponse
from app.services.audit_service import audit_service


class DeliveryRateService:
    @staticmethod
    def list_delivery_rates(
        db: Session,
        tenant_id: uuid.UUID,
        branch_id: Optional[uuid.UUID] = None,
        only_active: bool = True
    ) -> List[DeliveryRateResponse]:
        query = db.query(DeliveryRate).filter(DeliveryRate.tenant_id == tenant_id)
        if branch_id:
            query = query.filter(DeliveryRate.branch_id == branch_id)
        if only_active:
            query = query.filter(DeliveryRate.activo == True)
        
        rates = query.order_by(DeliveryRate.ciudad.asc(), DeliveryRate.costo_envio.asc()).all()
        result = []
        for r in rates:
            result.append(
                DeliveryRateResponse(
                    id=r.id,
                    branch_id=r.branch_id,
                    tenant_id=r.tenant_id,
                    zona_nombre=r.zona_nombre,
                    ciudad=r.ciudad,
                    costo_envio=float(r.costo_envio),
                    tiempo_estimado_horas=r.tiempo_estimado_horas,
                    activo=r.activo,
                    branch_nombre=r.branch.nombre if r.branch else None,
                    created_at=r.created_at,
                    updated_at=r.updated_at
                )
            )
        return result

    @staticmethod
    def create_delivery_rate(
        db: Session,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        data: DeliveryRateCreate,
        request=None
    ) -> DeliveryRateResponse:
        branch = db.query(Branch).filter(
            Branch.id == data.branch_id,
            Branch.tenant_id == tenant_id,
            Branch.activa == True
        ).first()
        if not branch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sucursal no encontrada o inactiva"
            )

        rate = DeliveryRate(
            branch_id=data.branch_id,
            tenant_id=tenant_id,
            zona_nombre=data.zona_nombre,
            ciudad=data.ciudad,
            costo_envio=data.costo_envio,
            tiempo_estimado_horas=data.tiempo_estimado_horas,
            activo=data.activo
        )
        db.add(rate)
        db.commit()
        db.refresh(rate)

        audit_service.log(
            db=db,
            user_id=user_id,
            action="CREAR_TARIFA_DELIVERY",
            module="Envíos",
            details=f"Tarifa creada: {rate.zona_nombre} ({rate.ciudad}) - {rate.costo_envio} BOB",
            tenant_id=tenant_id,
            request=request
        )

        return DeliveryRateResponse(
            id=rate.id,
            branch_id=rate.branch_id,
            tenant_id=rate.tenant_id,
            zona_nombre=rate.zona_nombre,
            ciudad=rate.ciudad,
            costo_envio=float(rate.costo_envio),
            tiempo_estimado_horas=rate.tiempo_estimado_horas,
            activo=rate.activo,
            branch_nombre=branch.nombre,
            created_at=rate.created_at,
            updated_at=rate.updated_at
        )

    @staticmethod
    def update_delivery_rate(
        db: Session,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        rate_id: uuid.UUID,
        data: DeliveryRateUpdate,
        request=None
    ) -> DeliveryRateResponse:
        rate = db.query(DeliveryRate).filter(
            DeliveryRate.id == rate_id,
            DeliveryRate.tenant_id == tenant_id
        ).first()
        if not rate:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tarifa de delivery no encontrada"
            )

        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(rate, key, value)

        db.commit()
        db.refresh(rate)

        audit_service.log(
            db=db,
            user_id=user_id,
            action="ACTUALIZAR_TARIFA_DELIVERY",
            module="Envíos",
            details=f"Tarifa ID {rate_id} actualizada: {rate.costo_envio} BOB",
            tenant_id=tenant_id,
            request=request
        )

        return DeliveryRateResponse(
            id=rate.id,
            branch_id=rate.branch_id,
            tenant_id=rate.tenant_id,
            zona_nombre=rate.zona_nombre,
            ciudad=rate.ciudad,
            costo_envio=float(rate.costo_envio),
            tiempo_estimado_horas=rate.tiempo_estimado_horas,
            activo=rate.activo,
            branch_nombre=rate.branch.nombre if rate.branch else None,
            created_at=rate.created_at,
            updated_at=rate.updated_at
        )

    @staticmethod
    def delete_delivery_rate(
        db: Session,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        rate_id: uuid.UUID,
        request=None
    ) -> dict:
        rate = db.query(DeliveryRate).filter(
            DeliveryRate.id == rate_id,
            DeliveryRate.tenant_id == tenant_id
        ).first()
        if not rate:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tarifa de delivery no encontrada"
            )

        rate.activo = False
        db.commit()

        audit_service.log(
            db=db,
            user_id=user_id,
            action="ELIMINAR_TARIFA_DELIVERY",
            module="Envíos",
            details=f"Tarifa ID {rate_id} desactivada",
            tenant_id=tenant_id,
            request=request
        )

        return {"message": "Tarifa de delivery desactivada exitosamente"}


delivery_rate_service = DeliveryRateService()
