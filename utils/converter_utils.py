"""Offline cross-platform Markdown -> sanitized HTML -> ReportLab PDF (ADR-5)."""
from __future__ import annotations
from html import escape
import io
import os
from pathlib import Path
from threading import Lock
import bleach
from bs4 import BeautifulSoup, NavigableString
import markdown
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

_FONT_LOCK = Lock()
_TAGS = {'p','h1','h2','h3','h4','h5','h6','strong','em','b','i','u','br','hr','ul','ol','li','blockquote','pre','code','table','thead','tbody','tr','th','td','a'}


def _clean(text):
    return bleach.clean(text, tags=_TAGS, attributes={'a':['href','title']}, protocols={'http','https','mailto'}, strip=True)


def md_to_html(md_text: str) -> str:
    if len(md_text.encode('utf-8')) > 2 * 1024 * 1024:
        raise ValueError('Markdown 超过 2 MiB')
    body = _clean(markdown.markdown(md_text, extensions=['tables','fenced_code']))
    return '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>' + body + '</body></html>'


def _font():
    with _FONT_LOCK:
        if 'TanyuanCJK' in pdfmetrics.getRegisteredFontNames(): return 'TanyuanCJK'
        configured = os.getenv('PDF_FONT_PATH','').strip()
        candidates = [configured] if configured else ['/System/Library/Fonts/Supplemental/Arial Unicode.ttf','/System/Library/Fonts/Supplemental/Songti.ttc','C:/Windows/Fonts/simsun.ttc','/usr/share/fonts/truetype/arphic/uming.ttc']
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                font = TTFont('TanyuanCJK',candidate)
                if ord('药') not in font.face.charToGlyph: raise ValueError('PDF 字体不支持中文')
                pdfmetrics.registerFont(font)
                return 'TanyuanCJK'
        if configured: raise ValueError('PDF_FONT_PATH 不存在')
        if 'STSong-Light' not in pdfmetrics.getRegisteredFontNames(): pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
        return 'STSong-Light'


def _inline(node):
    if isinstance(node,NavigableString): return escape(str(node))
    content = ''.join(_inline(child) for child in node.children)
    if node.name == 'br': return '<br/>'
    if node.name in ('strong','b'): return '<b>'+content+'</b>'
    if node.name in ('em','i'): return '<i>'+content+'</i>'
    if node.name == 'u': return '<u>'+content+'</u>'
    if node.name == 'a' and node.get('href'): return '<link href="'+escape(node['href'],quote=True)+'">'+content+'</link>'
    return content


def html_to_pdf(html_text: str, pdf_path: Path) -> Path:
    if len(html_text.encode('utf-8')) > 4 * 1024 * 1024: raise ValueError('HTML 超过 4 MiB')
    original = BeautifulSoup(html_text,'html.parser')
    for tag in original.find_all(['script','style','iframe','object','embed']): tag.decompose()
    soup = BeautifulSoup(_clean(str(original.body or original)),'html.parser')
    font = _font()
    pdfmetrics.registerFontFamily(font,normal=font,bold=font,italic=font,boldItalic=font)
    base = ParagraphStyle('body',fontName=font,fontSize=10,leading=17,wordWrap='CJK',spaceAfter=8)
    cell_style = ParagraphStyle('cell',parent=base,fontSize=9,leading=14,spaceAfter=0)
    story = []
    width = A4[0]-96
    for node in soup.contents:
        if isinstance(node,NavigableString):
            if node.strip(): story.append(Paragraph(escape(str(node)),base))
            continue
        if node.name == 'table':
            rows = [[Paragraph(_inline(cell),cell_style) for cell in row.find_all(['td','th'],recursive=False)] for row in node.find_all('tr')]
            if not rows: continue
            columns = max(map(len,rows))
            if columns > 20 or len(rows)>1000: raise ValueError('PDF 表格超过限制')
            rows = [row+['']*(columns-len(row)) for row in rows]
            table = Table(rows,colWidths=[width/columns]*columns,repeatRows=1,hAlign='LEFT')
            table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.5,colors.HexColor('#cbd5e1')),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e8eef5')),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
            story.extend([table,Spacer(1,10)])
        elif node.name in ('ul','ol'):
            for index,item in enumerate(node.find_all('li',recursive=False),1): story.append(Paragraph((str(index)+'. ' if node.name=='ol' else '- ')+_inline(item),base))
        elif node.name=='hr': story.append(Spacer(1,10))
        else:
            style=base
            if node.name in ('h1','h2','h3','h4','h5','h6'):
                size={'h1':20,'h2':15,'h3':12}.get(node.name,11)
                style=ParagraphStyle(node.name,parent=base,fontSize=size,leading=size*1.5,spaceBefore=12,spaceAfter=10,keepWithNext=True)
            text=_inline(node)
            if node.name=='pre': text=text.replace('\n','<br/>')
            if text.strip(): story.append(Paragraph(text,style))
    if not story: raise ValueError('没有可转换的文档正文')
    output=io.BytesIO()
    def footer(canvas,doc):
        canvas.setFont(font,8)
        canvas.drawRightString(A4[0]-48,28,str(doc.page))
    SimpleDocTemplate(output,pagesize=A4,leftMargin=48,rightMargin=48,topMargin=42,bottomMargin=42).build(story,onFirstPage=footer,onLaterPages=footer)
    pdf_path=Path(pdf_path)
    pdf_path.parent.mkdir(parents=True,exist_ok=True)
    with pdf_path.open('xb') as stream: stream.write(output.getvalue())
    return pdf_path


def convert_markdown_to_pdf(md_text: str, pdf_path: Path) -> Path:
    return html_to_pdf(md_to_html(md_text),pdf_path)
