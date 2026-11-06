import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.api.deps import require_platform_admin
from app.database import get_db
from app.models.platform_audit_log import PlatformAuditLog
from app.schemas.platform import PlatformCompanyCreate, PlatformCompanyStatus, PlatformCompanyResponse, PlatformCompanyList
from app.services.platform_company_service import PlatformCompanyService

router = APIRouter(prefix='/platform', tags=['Administración SaaS'])

from app.schemas.plan_catalog import (CatalogPlanCreate,CatalogPlanResponse,PlanDraftCreate,
    PlanDraftUpdate,PlanVersionDecision,PlanVersionResponse)
from app.services.plan_catalog_service import PlanCatalogService


@router.get('/plans',response_model=list[CatalogPlanResponse])
def list_plans(limit: int=Query(50,ge=1,le=200),offset: int=Query(0,ge=0),
               db: Session=Depends(get_db),actor=Depends(require_platform_admin)):
    return PlanCatalogService.list_plans(db,actor,limit,offset)


@router.post('/plans',response_model=CatalogPlanResponse,status_code=201)
def create_plan(request: CatalogPlanCreate,db: Session=Depends(get_db),actor=Depends(require_platform_admin)):
    return PlanCatalogService.create_plan(db,actor,request)


@router.get('/plans/{plan_id}/versions',response_model=list[PlanVersionResponse])
def list_plan_versions(plan_id: uuid.UUID,limit: int=Query(50,ge=1,le=200),offset: int=Query(0,ge=0),
                       db: Session=Depends(get_db),actor=Depends(require_platform_admin)):
    return PlanCatalogService.list_versions(db,actor,plan_id,limit,offset)


@router.post('/plans/{plan_id}/versions',response_model=PlanVersionResponse,status_code=201)
def create_plan_draft(plan_id: uuid.UUID,request: PlanDraftCreate,
                      db: Session=Depends(get_db),actor=Depends(require_platform_admin)):
    return PlanCatalogService.create_draft(db,actor,plan_id,request)


@router.get('/plan-versions/{version_id}',response_model=PlanVersionResponse)
def get_plan_version(version_id: uuid.UUID,db: Session=Depends(get_db),actor=Depends(require_platform_admin)):
    return PlanCatalogService.get_version(db,actor,version_id)


@router.put('/plan-versions/{version_id}',response_model=PlanVersionResponse)
def update_plan_draft(version_id: uuid.UUID,request: PlanDraftUpdate,
                     db: Session=Depends(get_db),actor=Depends(require_platform_admin)):
    return PlanCatalogService.update_draft(db,actor,version_id,request)


@router.post('/plan-versions/{version_id}/publish',response_model=PlanVersionResponse)
def publish_plan_version(version_id: uuid.UUID,request: PlanVersionDecision,
                         db: Session=Depends(get_db),actor=Depends(require_platform_admin)):
    return PlanCatalogService.publish_version(db,actor,version_id,request)


@router.post('/plan-versions/{version_id}/retire',response_model=PlanVersionResponse)
def retire_plan_version(version_id: uuid.UUID,request: PlanVersionDecision,
                        db: Session=Depends(get_db),actor=Depends(require_platform_admin)):
    return PlanCatalogService.retire_version(db,actor,version_id,request)


@router.get('/companies', response_model=PlatformCompanyList)
def list_companies(limit: int = Query(50,ge=1,le=200), offset: int = Query(0,ge=0),
                   db: Session = Depends(get_db), actor=Depends(require_platform_admin)):
    return PlatformCompanyService.list_companies(db,actor,limit,offset)


@router.get('/companies/{company_id}', response_model=PlatformCompanyResponse)
def get_company(company_id: uuid.UUID, db: Session = Depends(get_db), actor=Depends(require_platform_admin)):
    return PlatformCompanyService.get_company(db,actor,company_id)


@router.post('/companies', response_model=PlatformCompanyResponse, status_code=201)
def create_company(request: PlatformCompanyCreate, db: Session = Depends(get_db), actor=Depends(require_platform_admin)):
    return PlatformCompanyService.create_company(db,actor,request)


@router.put('/companies/{company_id}/status', response_model=PlatformCompanyResponse)
def set_company_status(company_id: uuid.UUID, request: PlatformCompanyStatus,
                       db: Session = Depends(get_db), actor=Depends(require_platform_admin)):
    return PlatformCompanyService.set_company_status(db,actor,company_id,request)


@router.get('/audit')
def list_audit(limit: int = Query(50,ge=1,le=200), offset: int = Query(0,ge=0),
               db: Session = Depends(get_db), actor=Depends(require_platform_admin)):
    # No join to company users or commercial audit logs.
    columns=[PlatformAuditLog.id,PlatformAuditLog.actor_id,PlatformAuditLog.action,
             PlatformAuditLog.target_type,PlatformAuditLog.target_id,PlatformAuditLog.reason,
             PlatformAuditLog.before_data,PlatformAuditLog.after_data,PlatformAuditLog.created_at]
    return [dict(row) for row in db.connection().execute(select(*columns)
        .order_by(PlatformAuditLog.created_at.desc(),PlatformAuditLog.id).limit(limit).offset(offset)).mappings()]
