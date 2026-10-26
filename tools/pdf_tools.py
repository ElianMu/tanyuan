"""Markdown→PDF 工具（TDD §7.4）。"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

# 允许以 `python tools/pdf_tools.py` 直接运行时导入项目内模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.tools import tool  # noqa: E402

from api import context  # noqa: E402
from utils import converter_utils  # noqa: E402
from utils import path_utils  # noqa: E402


def _get_session_dir() -> Path:
    """返回会话工作目录；未设置会话上下文时回退到默认会话目录。"""
    d = context.get_session_dir()
    if d:
        return Path(d)
    return path_utils.get_session_dir(context.get_thread_id() or "default")


@tool
def convert_md_to_pdf(md_filename: str, pdf_filename: Optional[str] = None) -> str:
    """校验源 md 文件存在后，调用转换核心生成 PDF，返回 PDF 绝对路径。"""
    session_dir = _get_session_dir()
    md_path = path_utils.resolve_within(session_dir, md_filename)
    if not md_path.exists():
        return f"错误：文件不存在 {md_path}"
    if pdf_filename is None:
        pdf_filename = md_path.stem + ".pdf"
    pdf_path = path_utils.resolve_within(session_dir, pdf_filename)
    try:
        converter_utils.convert_markdown_to_pdf(
            md_path.read_text(encoding="utf-8"), pdf_path
        )
    except Exception as e:  # noqa: BLE001
        return f"PDF 转换失败：{e}"
    return str(pdf_path)


if __name__ == "__main__":
    session_dir = path_utils.get_session_dir("demo")
    session_dir.mkdir(parents=True, exist_ok=True)
    md_path = session_dir / "测试报告.md"
    md_path.write_text(
        "# 测试报告\n\n## 一、概述\n\n这是阶段 4 的 PDF 转换测试。\n\n"
        "| 药品 | 数量 |\n| --- | --- |\n| 阿莫西林 | 100 |\n",
        encoding="utf-8",
    )
    context.set_session("demo", str(session_dir))
    result = convert_md_to_pdf.invoke({"md_filename": "测试报告.md"})
    print("转换结果:", result)
    print("PDF 存在:", Path(result).exists() if not result.startswith("错误") else False)
