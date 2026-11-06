import uuid
from fastapi import APIRouter,Depends,Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.api.deps import get_subscription_manager,require_platform_admin
from app.schemas.subscription_contract import ContractQuoteRequest,ContractChangeRequest,ContractDecision,ContractCancellation
from app.services.subscription_contract_service import SubscriptionContractService as Service

router=APIRouter(tags=['Contratos de suscripción'])


@router.post('/subscriptions/quote')
def quote(body: ContractQuoteRequest,db: Session=Depends(get_db),actor=Depends(get_subscription_manager)):
    return Service.quote(db,actor,body.version_id,body.kind)


@router.post('/subscriptions/requests',status_code=201)
def request_change(body: ContractChangeRequest,db: Session=Depends(get_db),actor=Depends(get_subscription_manager)):
    return Service.request_change(db,actor,body)


@router.get('/subscriptions/requests')
def own_requests(limit: int=Query(50,ge=1,le=200),offset: int=Query(0,ge=0),
                 db: Session=Depends(get_db),actor=Depends(get_subscription_manager)):
    return Service.list_requests(db,actor,limit=limit,offset=offset)


@router.get('/subscriptions/contract')
def contract(db: Session=Depends(get_db),actor=Depends(get_subscription_manager)):
    return Service.contract(db,actor)


@router.post('/subscriptions/requests/{request_id}/cancel')
def cancel(request_id: uuid.UUID,body: ContractCancellation,db: Session=Depends(get_db),actor=Depends(get_subscription_manager)):
    return Service.cancel(db,actor,request_id,body)


@router.get('/platform/subscription-requests')
def requests(limit: int=Query(50,ge=1,le=200),offset: int=Query(0,ge=0),
             db: Session=Depends(get_db),actor=Depends(require_platform_admin)):
    return Service.list_requests(db,actor,platform=True,limit=limit,offset=offset)


@router.post('/platform/subscription-requests/{request_id}/decision')
def decide(request_id: uuid.UUID,body: ContractDecision,db: Session=Depends(get_db),actor=Depends(require_platform_admin)):
    return Service.decide_request(db,actor,request_id,body)
