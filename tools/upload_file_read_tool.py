"""上传文件解析工具（TDD §7.4）。"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许以 `python tools/upload_file_read_tool.py` 直接运行时导入项目内模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.tools import tool  # noqa: E402

from api import context  # noqa: E402
from utils import path_utils  # noqa: E402


def _get_session_dir() -> Path:
    """返回会话工作目录；未设置会话上下文时回退到默认会话目录。"""
    d = context.get_session_dir()
    if d:
        return Path(d)
    return path_utils.get_session_dir(context.get_thread_id() or "default")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _read_docx(path: Path) -> str:
    import docx

    d = docx.Document(str(path))
    return "\n".join(p.text for p in d.paragraphs)


def _read_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


def _read_excel(path: Path) -> str:
    import pandas as pd

    df = pd.read_excel(path)
    rows, cols = df.shape
    return (
        f"文件：{path.name}\n"
        f"行数：{rows}，列数：{cols}\n\n"
        f"前 5 行：\n{df.head(5).to_string(index=False)}\n\n"
        f"统计描述：\n{df.describe(include='all').to_string()}"
    )


@tool
def read_file_content(filename: str, instruction: str = "提取全部内容") -> str:
    """按后缀解析会话工作目录中的文件，返回其文本内容。"""
    target = path_utils.resolve_within(_get_session_dir(), filename)
    if not target.exists():
        return f"错误：文件不存在 {target}"
    ext = target.suffix.lower()
    if ext in (".md", ".txt"):
        return _read_text(target)
    if ext == ".docx":
        return _read_docx(target)
    if ext == ".pdf":
        return _read_pdf(target)
    if ext in (".xlsx", ".xls"):
        return _read_excel(target)
    return f"不支持的文件类型：{ext}"


if __name__ == "__main__":
    import pandas as pd

    context.set_session("demo", str(path_utils.get_session_dir("demo")))
    session_dir = path_utils.get_session_dir("demo")
    session_dir.mkdir(parents=True, exist_ok=True)
    xlsx_path = session_dir / "销售数据.xlsx"
    pd.DataFrame(
        {
            "药品": ["阿莫西林", "布洛芬", "二甲双胍", "阿托伐他汀", "奥司他韦"],
            "数量": [100, 200, 150, 80, 120],
            "单价": [25.0, 15.0, 35.0, 45.0, 100.0],
        }
    ).to_excel(xlsx_path, index=False)
    print(read_file_content.invoke({"filename": "销售数据.xlsx"}))
