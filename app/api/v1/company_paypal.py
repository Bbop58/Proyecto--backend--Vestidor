from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from app.database import get_db
from app.api.deps import get_current_user, require_permission
from app.schemas.company_paypal import CompanyPayPalUpdate, RevisionRequest
from app.services.company_paypal_service import CompanyPayPalService as Service


from app.api.secret_safe_route import SecretSafeRoute

router = APIRouter(prefix='/company/paypal', tags=['PayPal empresarial'], route_class=SecretSafeRoute)
manager = require_permission('payments.configure')


@router.get('')
def status(db=Depends(get_db), user=Depends(manager)):
    return Service.state(db, user)


@router.put('')
def configure(body: CompanyPayPalUpdate, db=Depends(get_db), user=Depends(manager)):
    return Service.configure(db, user, body)


@router.post('/disable')
def disable(body: RevisionRequest, db=Depends(get_db), user=Depends(manager)):
    return Service.disable(db, user, body.expected_revision)


@router.post('/verify')
def verify(body: RevisionRequest, db=Depends(get_db), user=Depends(manager)):
    state = Service.state(db, user)
    if not state['enabled']: raise HTTPException(409, 'Configura la cuenta antes de verificar')
    return Service.configure(db, user, CompanyPayPalUpdate(client_id=state['client_id'], mode=state['mode'], expected_revision=body.expected_revision))
