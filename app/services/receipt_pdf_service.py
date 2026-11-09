"""One offline-capable A4 layout; no network, assets fetched or DB writes."""
from io import BytesIO
from pathlib import Path
from threading import Lock
from datetime import timezone
from zoneinfo import ZoneInfo
from xml.sax.saxutils import escape
from fastapi import HTTPException
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
from app.schemas.receipt import ReceiptDocument

_FONT='FashionStoreReceipt'
_font_lock=Lock()


def _font():
    with _font_lock:
        if _FONT not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(_FONT,str(Path(__file__).resolve().parents[1]/'assets/receipts/NotoSans-Regular.ttf')))
    return pdfmetrics.getFont(_FONT)


def render_receipt_pdf(document: ReceiptDocument) -> bytes:
    font=_font()
    # Do not silently emit blank squares for an unsupported customer's name.
    texts=[value for value in _strings(document.model_dump(mode='json'))]
    if any(ord(char) not in font.face.charToGlyph for text in texts for char in text if not char.isspace()):
        raise HTTPException(422,'Un carácter del comprobante no es compatible con la fuente PDF')
    body=ParagraphStyle('receipt',fontName=_FONT,fontSize=9,leading=13,textColor=colors.HexColor('#182435'),splitLongWords=True)
    small=ParagraphStyle('small',parent=body,fontSize=8,leading=11)
    title=ParagraphStyle('title',parent=body,fontSize=18,leading=23,spaceAfter=8)
    heading=ParagraphStyle('heading',parent=body,fontSize=11,leading=16,spaceAfter=4)
    right=ParagraphStyle('right',parent=body,alignment=TA_RIGHT)
    def paragraph(value,style=body):return Paragraph(escape(str(value)),style)
    def date_text(value):
        aware=value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return aware.astimezone(ZoneInfo('America/La_Paz')).strftime('%d/%m/%Y %H:%M')+' (Bolivia)'
    stream=BytesIO()
    pdf=SimpleDocTemplate(stream,pagesize=A4,rightMargin=36,leftMargin=36,topMargin=36,bottomMargin=43,
        title=document.title,author=document.company.name,pageCompression=1,invariant=1)
    parts=[paragraph(document.company.name,title),paragraph(document.title,heading),
        paragraph('N.º '+document.number),paragraph('Fecha: '+date_text(document.issued_at))]
    if document.branch:
        parts.append(paragraph('Sucursal: '+document.branch.name))
        if document.branch.address:parts.append(paragraph(document.branch.address,small))
    if document.customer:parts.append(paragraph('Cliente: '+document.customer.name))
    parts.extend([Spacer(1,9),paragraph(document.status+' · '+document.payment_status,heading)])
    if document.provisional:parts.append(paragraph('PROVISIONAL · Pendiente de sincronización',heading))
    if document.expires_at:parts.append(paragraph('Vence: '+date_text(document.expires_at)))
    if document.payment_method:parts.append(paragraph('Método: '+document.payment_method))
    if document.reference:parts.append(paragraph('Referencia: '+document.reference,small))
    parts.append(Spacer(1,12))
    rows=[[paragraph('Detalle',small),paragraph('Cant.',small),paragraph('Precio Bs',small),paragraph('Subtotal Bs',small)]]
    for item in document.items:
        detail=item.description
        identifiers=['SKU '+item.sku if item.sku else '', 'Talla '+item.size if item.size else '',item.color or '']
        extra=' · '.join(value for value in identifiers if value)
        if item.section:detail=item.section+' · '+detail
        label=escape(detail)+('<br/>'+escape(extra) if extra else '')
        rows.append([Paragraph(label,body),paragraph(item.quantity,right),paragraph(item.unit_price,right),paragraph(item.subtotal,right)])
    table=Table(rows,colWidths=[pdf.width-190,38,76,76],repeatRows=1,hAlign='LEFT')
    table.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#E9EEF5')),
        ('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),7),
        ('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),7),
        ('BOTTOMPADDING',(0,0),(-1,-1),7),('LINEBELOW',(0,0),(-1,0),.6,colors.HexColor('#C2CAD6')),
        ('LINEBELOW',(0,1),(-1,-1),.25,colors.HexColor('#DCE2EA'))]))
    parts.extend([table,Spacer(1,12)])
    totals=[]
    for label,value in [('Subtotal',document.subtotal),('Descuento',document.discount),('Envío',document.shipping)]:
        if value is not None:totals.append(paragraph(f'{label}: Bs {value}',right))
    label='Importe estimado' if document.kind=='reservation' else 'Diferencia' if document.kind=='exchange' else 'Total'
    totals.append(paragraph(f'{label}: Bs {document.total}',right))
    if document.received is not None:totals.append(paragraph('Efectivo recibido: Bs '+document.received,right))
    if document.change is not None:totals.append(paragraph('Cambio: Bs '+document.change,right))
    parts.append(KeepTogether(totals));parts.append(Spacer(1,14))
    parts.extend(paragraph(note,small) for note in document.notes)
    def footer(canvas,doc):
        canvas.saveState();canvas.setFont(_FONT,8);canvas.setFillColor(colors.HexColor('#596579'))
        canvas.drawString(36,24,'FashionStore · Documento comercial')
        canvas.drawRightString(A4[0]-36,24,'Página '+str(doc.page));canvas.restoreState()
    pdf.build(parts,onFirstPage=footer,onLaterPages=footer)
    return stream.getvalue()


def _strings(value):
    if isinstance(value,str):yield value
    elif isinstance(value,dict):
        for item in value.values():yield from _strings(item)
    elif isinstance(value,list):
        for item in value:yield from _strings(item)
