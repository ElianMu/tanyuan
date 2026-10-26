"""Markdown 生成工具（TDD §7.4）。"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许以 `python tools/markdown_tools.py` 直接运行时导入项目内模块
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


@tool
def generate_markdown(content: str, filename: str, path: str = "") -> str:
    """将 Markdown 内容写入会话工作目录，自动补 .md 后缀，返回绝对路径。"""
    if not filename.lower().endswith(".md"):
        filename += ".md"
    virtual = f"{path}/{filename}" if path else filename
    target = path_utils.resolve_within(_get_session_dir(), virtual)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return str(target)


if __name__ == "__main__":
    context.set_session("demo", str(path_utils.get_session_dir("demo")))
    result = generate_markdown.invoke(
        {"content": "# 测试报告\n\n这是阶段 4 的 Markdown 落盘测试。", "filename": "测试报告"}
    )
    print("已生成:", result)
    print("文件存在:", Path(result).exists())
