"""全网检索工具 internet_search（TDD §7.1）。"""
from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock
from typing import Literal
from urllib.parse import urlsplit

from langchain_core.tools import tool
from tavily import TavilyClient

@dataclass
class SearchUsage:
    queries: set[str] = field(default_factory=set)
    calls: int = 0
    lock: Lock = field(default_factory=Lock)


_usage: ContextVar[SearchUsage | None] = ContextVar("tanyuan_search_usage", default=None)


@contextmanager
def search_scope():
    """One task shares a thread-safe three-call budget across its sub-agents."""
    usage = SearchUsage()
    token = _usage.set(usage)
    try:
        yield usage
    finally:
        _usage.reset(token)


@tool
def internet_search(
    query: str,
    max_results: int = 5,
    topic: Literal["general", "news", "finance"] = "general",
    include_raw_content: bool = False,
) -> str:
    """从互联网检索公开信息，返回结构化检索结果。"""
    if not query.strip() or len(query) > 2000 or not 1 <= max_results <= 10:
        return "网络检索失败：问题或结果数量超出限制"
    if topic not in ("general", "news", "finance"):
        return "网络检索失败：不支持该主题"
    key = os.getenv("TAVILY_API_KEY", "").strip()
    if not key:
        return "网络检索失败：缺少 TAVILY_API_KEY"
    usage = _usage.get()
    if usage is None:
        usage = SearchUsage()
        _usage.set(usage)
    normalized = " ".join(query.split()).casefold()
    with usage.lock:
        if usage.calls >= 3 or normalized in usage.queries:
            return "网络检索失败：同一任务最多三次，且需使用不同检索角度"
        usage.calls += 1
        usage.queries.add(normalized)
    client = None
    try:
        if os.getenv("TANYUAN_CALL_BUDGET_FILE"):
            from utils.call_budget import reserve
            reserve("tavily")
        client = TavilyClient(api_key=key)
        response = client.search(
            query=query,
            max_results=max_results,
            topic=topic,
            include_raw_content=include_raw_content,
            search_depth="basic", timeout=20, auto_parameters=False,
        )
    except Exception:
        return "网络检索失败：外部服务异常或超时"
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass  # Cleanup must not expose SDK exceptions or credentials.
    if not isinstance(response, dict) or not isinstance(response.get("results", []), list):
        return "网络检索失败：外部服务返回格式异常"
    results = response.get("results", [])
    if not results:
        return "未检索到相关内容"
    blocks = []
    for i, r in enumerate(results[:max_results], 1):
        if not isinstance(r, dict):
            continue
        url = str(r.get("url", ""))[:2000]
        try:
            parsed = urlsplit(url)
        except ValueError:
            continue
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            continue
        title = str(r.get("title", ""))[:500]
        content = str(r.get("content", ""))[:6000]
        if include_raw_content and r.get("raw_content"):
            content = (content + "\n" + str(r["raw_content"]))[:6000]
        blocks.append(f"[{i}] {title}\nURL: {url}\n内容: {content}")
    return "\n\n".join(blocks) or "未检索到有效来源"
