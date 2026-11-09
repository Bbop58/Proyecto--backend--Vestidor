from uuid import UUID
import re
from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
from app.api.deps import get_history_user
from app.database import get_db
from app.schemas.receipt import ReceiptDocument, ReceiptKind, ReceiptEmailRequest, ReceiptEmailResult
from app.services.receipt_document_service import get_receipt_document
from app.services.receipt_pdf_service import render_receipt_pdf

router=APIRouter(prefix='/receipts',tags=['Comprobantes'])


@router.get('/sale-number/{number}',response_model=ReceiptDocument)
def sale_number(number:str,response:Response,db:Session=Depends(get_db),actor=Depends(get_history_user)):
    # Legacy wallet SQLite retains the confirmed receipt number, not a sale UUID.
    from fastapi import HTTPException
    from app.models.sale import Sale
    from app.services.receipt_document_service import ensure_receipt_actor
    ensure_receipt_actor(actor)
    row=db.query(Sale.id).filter(Sale.tenant_id==actor.tenant_id,Sale.numero_recibo==number).first()
    if not row:raise HTTPException(404,'Comprobante no encontrado')
    response.headers['Cache-Control']='private, no-store'
    return get_receipt_document(db,actor,'sale',row.id)


@router.get('/capabilities')
def capabilities(response:Response,actor=Depends(get_history_user)):
    from app.services.receipt_email_service import email_enabled
    response.headers['Cache-Control']='private, no-store'
    return {'email_enabled':email_enabled()}


@router.post('/{kind}/{entity_id}/email',response_model=ReceiptEmailResult)
def send_email(kind:ReceiptKind,entity_id:UUID,payload:ReceiptEmailRequest,response:Response,
               db:Session=Depends(get_db),actor=Depends(get_history_user)):
    # A synchronous route runs in FastAPI's worker pool, not the event loop.
    from app.services.receipt_email_service import send_receipt_email
    response.headers['Cache-Control']='private, no-store'
    return send_receipt_email(db,actor,kind,entity_id,str(payload.recipient),payload.operation_id)


@router.get('/{kind}/{entity_id}',response_model=ReceiptDocument)
def document(kind:ReceiptKind,entity_id:UUID,response:Response,
             db:Session=Depends(get_db),actor=Depends(get_history_user)):
    response.headers['Cache-Control']='private, no-store'
    return get_receipt_document(db,actor,kind,entity_id)


@router.get('/{kind}/{entity_id}/pdf')
def pdf(kind:ReceiptKind,entity_id:UUID,db:Session=Depends(get_db),actor=Depends(get_history_user)):
    note=get_receipt_document(db,actor,kind,entity_id)
    name=re.sub(r'[^a-zA-Z0-9_-]','_',note.number)[:90] or str(entity_id)
    return Response(content=render_receipt_pdf(note),media_type='application/pdf',headers={
        'Content-Disposition':f'attachment; filename="comprobante-{name}.pdf"',
        'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
