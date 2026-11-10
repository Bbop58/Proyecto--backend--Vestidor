"""PDF attachment for the exact report snapshot shown by web and mobile."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from fastapi import HTTPException
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle

from app.services.receipt_pdf_service import _FONT, _font


def render_voice_report_pdf(question, interpretation, columns, rows):
    font=_font()
    values=[str(question),str(interpretation),*(str(c) for c in columns)]
    values.extend(str(value) for row in rows for value in row.values() if value is not None)
    if any(ord(char) not in font.face.charToGlyph for value in values for char in value if not char.isspace()):
        raise HTTPException(422,'Un carácter del reporte no es compatible con la fuente PDF')
    regular=ParagraphStyle('report',fontName=_FONT,fontSize=8,leading=11,textColor=colors.HexColor('#182435'),splitLongWords=True)
    heading=ParagraphStyle('report_heading',parent=regular,fontSize=16,leading=20,spaceAfter=7)
    subtitle=ParagraphStyle('report_subtitle',parent=regular,fontSize=9,leading=13,spaceAfter=5)
    stream=BytesIO()
    doc=SimpleDocTemplate(stream,pagesize=A4,leftMargin=36,rightMargin=36,topMargin=36,bottomMargin=38,
        title='Reporte FashionStore',author='FashionStore',pageCompression=1,invariant=1)
    cell=lambda value:Paragraph(escape(str(value if value is not None else '—')),regular)
    table_data=[[cell(column.replace('_',' ').upper()) for column in columns]]
    for row in rows:
        table_data.append([cell(row.get(column)) for column in columns])
    col_width=doc.width/max(1,len(columns))
    table=LongTable(table_data,colWidths=[col_width]*len(columns),repeatRows=1,hAlign='LEFT')
    table.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#334155')),
        ('TEXTCOLOR',(0,0),(-1,0),colors.white),('VALIGN',(0,0),(-1,-1),'TOP'),
        ('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),
        ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),
        ('LINEBELOW',(0,0),(-1,-1),.25,colors.HexColor('#DCE2EA')),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#F8FAFC')])]))
    story=[Paragraph('FashionStore · Reporte gerencial',heading),
        Paragraph('Consulta: '+escape(str(question)),subtitle),
        Paragraph('Resumen: '+escape(str(interpretation or 'Sin resumen disponible')),subtitle),
        Paragraph(f'Registros incluidos: {len(rows)}',regular),Spacer(1,10),table]
    def footer(canvas,page):
        canvas.saveState();canvas.setFont(_FONT,8);canvas.setFillColor(colors.HexColor('#596579'))
        canvas.drawString(36,22,'Reporte informativo · FashionStore')
        canvas.drawRightString(A4[0]-36,22,'Página '+str(page.page));canvas.restoreState()
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return stream.getvalue()
