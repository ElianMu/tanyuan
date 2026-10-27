"""Markdown 生成工具（TDD §7.4）。"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许以 `python tools/markdown_tools.py` 直接运行时导入项目内模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.tools import tool  # noqa: E402

from api import context  # noqa: E402
from utils import path_utils  # noqa: E402
from utils.file_utils import resolve_file


def _get_session_dir() -> Path:
    """返回会话工作目录；未设置会话上下文时回退到默认会话目录。"""
    d = context.get_session_dir()
    if d:
        return Path(d)
    raise ValueError("文件工具必须在有效会话内调用")


@tool
def generate_markdown(content: str, filename: str, path: str = "") -> str:
    """将 Markdown 内容写入会话工作目录，自动补 .md 后缀，返回绝对路径。"""
    if len(content.encode("utf-8")) > 2 * 1024 * 1024:
        raise ValueError("Markdown 超过 2 MiB")
    if not filename.lower().endswith(".md"):
        filename += ".md"
    virtual = f"{path}/{filename}" if path else filename
    target = resolve_file(_get_session_dir(), virtual)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as stream:
        stream.write(content)
    return str(target)


if __name__ == "__main__":
    context.set_session("demo", str(path_utils.get_session_dir("demo")))
    result = generate_markdown.invoke(
        {"content": "# 测试报告\n\n这是阶段 4 的 Markdown 落盘测试。", "filename": "测试报告"}
    )
    print("已生成:", result)
    print("文件存在:", Path(result).exists())
