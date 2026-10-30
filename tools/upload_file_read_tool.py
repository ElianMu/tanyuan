"""上传文件解析工具（TDD §7.4）。"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许以 `python tools/upload_file_read_tool.py` 直接运行时导入项目内模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.tools import tool  # noqa: E402

from api import context  # noqa: E402
from utils.file_utils import resolve_file


def _get_session_dir() -> Path:
    """返回会话工作目录；未设置会话上下文时回退到默认会话目录。"""
    d = context.get_session_dir()
    if d:
        return Path(d)
    raise ValueError("文件工具必须在有效会话内调用")


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("gb18030")


def _read_docx(path: Path) -> str:
    import docx

    d = docx.Document(str(path))
    parts = [p.text for p in d.paragraphs]
    parts.extend(" | ".join(cell.text for cell in row.cells) for table in d.tables for row in table.rows)
    return "\n".join(parts)


def _read_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


def _read_excel(path: Path) -> str:
    import pandas as pd

    sheets = pd.read_excel(path, sheet_name=None, nrows=10000)
    parts = []
    for sheet, df in sheets.items():
        rows, cols = df.shape
        parts.append(f"工作表：{sheet}（最多解析 10000 行）\n" + (
        f"文件：{path.name}\n"
        f"行数：{rows}，列数：{cols}\n\n"
        f"前 5 行：\n{df.head(5).to_string(index=False)}\n\n"
        f"统计描述：\n{df.describe(include='all').to_string() if len(df.columns) else '无可统计列'}"
        ))
    return "\n\n".join(parts)


@tool
def read_file_content(filename: str, instruction: str = "提取全部内容") -> str:
    """按后缀解析会话工作目录中的文件，返回其文本内容。"""
    target = resolve_file(_get_session_dir(), filename)
    if not target.exists():
        return f"错误：文件不存在 {target}"
    if not target.is_file() or target.stat().st_size > 10 * 1024 * 1024:
        return "错误：只允许不超过 10 MiB 的文件"
    ext = target.suffix.lower()
    readers = {".md": _read_text, ".txt": _read_text, ".docx": _read_docx,
               ".pdf": _read_pdf, ".xlsx": _read_excel, ".xls": _read_excel}
    if ext not in readers:
        return f"不支持的文件类型：{ext}"
    try:
        if ext in (".docx", ".xlsx"):
            from zipfile import ZipFile
            with ZipFile(target) as archive:
                entries = archive.infolist()
                if len(entries) > 2000 or sum(item.file_size for item in entries) > 50 * 1024 * 1024:
                    return "文件解析失败：压缩文档展开后超过限制"
        result = readers[ext](target)
        return result[:100000] + ("\n【内容超过 100000 字符，已截断】" if len(result) > 100000 else "")
    except Exception:
        return "文件解析失败：文件损坏或格式不匹配"
