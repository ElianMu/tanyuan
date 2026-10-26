"""全网检索工具 internet_search（TDD §7.1）。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from langchain_core.tools import tool
from tavily import TavilyClient

# 加载项目根目录的 .env
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY", ""))


@tool
def internet_search(
    query: str,
    max_results: int = 5,
    topic: Literal["general", "news", "finance"] = "general",
    include_raw_content: bool = False,
) -> str:
    """从互联网检索公开信息，返回结构化检索结果。"""
    try:
        response = _client.search(
            query=query,
            max_results=max_results,
            topic=topic,
            include_raw_content=include_raw_content,
        )
    except Exception as e:  # noqa: BLE001
        return f"网络检索失败：{e}"
    results = response.get("results", [])
    if not results:
        return "未检索到相关内容"
    blocks = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        url = r.get("url", "")
        content = r.get("content", "")
        blocks.append(f"[{i}] {title}\nURL: {url}\n内容: {content}")
    return "\n\n".join(blocks)


if __name__ == "__main__":
    print(internet_search.invoke({"query": "人工智能 最新进展", "max_results": 2}))
