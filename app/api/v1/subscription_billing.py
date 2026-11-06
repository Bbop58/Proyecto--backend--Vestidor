import json,uuid
from fastapi import APIRouter,Depends,Query,Request,HTTPException
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel,ConfigDict
from app.database import get_db
from app.api.deps import get_subscription_manager,require_platform_admin
from app.services.subscription_billing_service import SubscriptionBillingService as Service
from app.api.secret_safe_route import SecretSafeRoute

router=APIRouter(tags=['Facturación SaaS'],route_class=SecretSafeRoute)

class EmptyBody(BaseModel):
    model_config=ConfigDict(extra='forbid')

@router.get('/subscriptions/billing')
def payments(limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),db=Depends(get_db),actor=Depends(get_subscription_manager)):
    return Service.list_payments(db,actor,limit=limit,offset=offset)

@router.get('/subscriptions/requests/{request_id}/payment')
def payment(request_id:uuid.UUID,db=Depends(get_db),actor=Depends(get_subscription_manager)):
    return Service.for_request(db,actor,request_id)

@router.post('/subscriptions/billing/{payment_id}/create')
def create(payment_id:uuid.UUID,body:EmptyBody,db=Depends(get_db),actor=Depends(get_subscription_manager)):
    return Service.create(db,actor,payment_id)

@router.post('/subscriptions/billing/{payment_id}/capture')
def capture(payment_id:uuid.UUID,body:EmptyBody,db=Depends(get_db),actor=Depends(get_subscription_manager)):
    return Service.capture(db,actor,payment_id)

@router.get('/platform/subscription-billing')
def platform_payments(limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),db=Depends(get_db),actor=Depends(require_platform_admin)):
    return Service.list_payments(db,actor,platform=True,limit=limit,offset=offset)

@router.post('/subscriptions/paypal/webhook')
async def webhook(request:Request,db=Depends(get_db)):
    data=bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data)>131072:raise HTTPException(413,'Evento demasiado grande')
    try:
        event=json.loads(data)
        if not isinstance(event,dict):raise ValueError()
    except (ValueError,UnicodeDecodeError):raise HTTPException(400,'Evento inválido')
    return await run_in_threadpool(Service.webhook,db,dict(request.headers),event)
