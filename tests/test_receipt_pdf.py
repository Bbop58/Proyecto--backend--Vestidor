from io import BytesIO
from datetime import datetime,timezone
from uuid import uuid4
import pytest
from pypdf import PdfReader
from fastapi import HTTPException
from app.schemas.receipt import ReceiptDocument,ReceiptParty,ReceiptItem
from app.services.receipt_pdf_service import render_receipt_pdf


def sample(**overrides):
    return ReceiptDocument(**(dict(kind='sale',entity_id=uuid4(),title='Nota de venta',number='VTA-QA-123',
        issued_at=datetime(2026,11,9,16,0,tzinfo=timezone.utc),company=ReceiptParty(name='Moda Ágil & Compañía'),
        status='COMPLETADA',payment_status='PAGADO',payment_method='EFECTIVO',
        customer=ReceiptParty(name='José Muñoz'),total='26.25',
        items=[ReceiptItem(description='Camisa <lino> & algodón',size='XL',color='Azul',quantity=1,unit_price='26.25',subtotal='26.25')],
        notes=['Documento comercial. No constituye factura fiscal.'])|overrides))


def test_pdf_is_readable_unicode_escaped_and_one_page():
    data=render_receipt_pdf(sample());reader=PdfReader(BytesIO(data));text=reader.pages[0].extract_text()
    assert len(reader.pages)==1
    for label in ('José Muñoz','Moda Ágil & Compañía','Camisa <lino> & algodón','26.25','VTA-QA-123','PAGADO','XL','Azul'):
        assert label in text
    assert 'IVA' not in text
    assert '09/11/2026 12:00 (Bolivia)' in text


def test_pdf_multi_page_contains_every_item_once_and_page_numbers():
    rows=[ReceiptItem(description=f'PRENDA-{i:03d} '+('Descripción larga con acentos y color azul. '*3),quantity=1,
        unit_price='1.00',subtotal='1.00') for i in range(90)]
    reader=PdfReader(BytesIO(render_receipt_pdf(sample(items=rows,total='90.00'))))
    assert len(reader.pages)>2
    text='\n'.join(page.extract_text() for page in reader.pages)
    for i in range(90):assert text.count(f'PRENDA-{i:03d}')==1
    for i,page in enumerate(reader.pages,1):assert f'Página {i}' in page.extract_text()


def test_provisional_has_no_central_receipt_or_paid_label():
    identifier=uuid4()
    doc=sample(number=str(identifier),status='PENDIENTE',payment_status='PENDIENTE DE SINCRONIZACIÓN',provisional=True)
    text=PdfReader(BytesIO(render_receipt_pdf(doc))).pages[0].extract_text()
    assert 'PROVISIONAL' in text and str(identifier) in text and 'PAGADO' not in text


def test_unsupported_glyph_is_explicit_not_a_corrupt_pdf():
    with pytest.raises(HTTPException) as error:render_receipt_pdf(sample(customer=ReceiptParty(name='Cliente 🧥')))
    assert error.value.status_code==422


def test_same_document_has_identical_download_and_attachment_bytes():
    doc=sample()
    assert render_receipt_pdf(doc)==render_receipt_pdf(doc)
