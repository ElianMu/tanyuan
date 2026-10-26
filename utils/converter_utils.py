"""Markdown→HTML→Word COM→PDF 核心转换（TDD §4.4 / ADR-5）。

依赖 Windows + 本机 Microsoft Word（Word COM）。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import markdown


def md_to_html(md_text: str) -> str:
    """Markdown → HTML（UTF-8 + 中文字体 + 表格/代码块支持）。"""
    body = markdown.markdown(md_text, extensions=["tables", "fenced_code"])
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        "<style>"
        'body{font-family:"Microsoft YaHei","SimSun",sans-serif;'
        "line-height:1.6;margin:2em;}"
        "table{border-collapse:collapse;}"
        "td,th{border:1px solid #999;padding:6px;}"
        "</style></head><body>"
        + body
        + "</body></html>"
    )


def html_to_pdf(html_text: str, pdf_path: Path) -> Path:
    """HTML → Word COM → PDF。

    将 HTML 写入临时文件，用 Word COM 打开并另存为 PDF（FileFormat=17）。
    """
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    # 临时 HTML 文件（系统临时目录，ASCII 路径，避免 Word 打开非 ASCII 路径的问题）
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as tmp:
        tmp.write(html_text)
        html_file = tmp.name

    word = None
    try:
        import win32com.client

        word = win32com.client.Dispatch("Word.Application")
        word.Visible = False
        doc = word.Documents.Open(html_file)
        doc.SaveAs(str(pdf_path), FileFormat=17)  # 17 = wdFormatPDF
        doc.Close(False)
    finally:
        if word is not None:
            word.Quit()
        if os.path.exists(html_file):
            os.unlink(html_file)
    return pdf_path


def convert_markdown_to_pdf(md_text: str, pdf_path: Path) -> Path:
    """Markdown → PDF 全流程核心。"""
    return html_to_pdf(md_to_html(md_text), pdf_path)
