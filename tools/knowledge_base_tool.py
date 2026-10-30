"""内部知识库检索工具 search_knowledge_base（TDD §7.3）。

对本地 Chroma 向量库做相似度检索，返回 top-k 个最相关分块（含来源文件名）。
"""
from __future__ import annotations

import asyncio
import os
import json
import subprocess
from pathlib import Path

from langchain_core.tools import tool
from utils.knowledge_utils import COLLECTION_NAME, MANIFEST, get_embedding, model_name, resolve_path, run_worker


def _search_sync(query: str, k: int, vs_dir: Path) -> str:
    """同步执行嵌入 + 检索（放到独立线程跑，避免 torch 在事件循环里崩溃）。"""
    from langchain_chroma import Chroma
    from chromadb.config import Settings
    store = Chroma(
        persist_directory=str(vs_dir),
        embedding_function=get_embedding(),
        collection_name=COLLECTION_NAME,
        create_collection_if_not_exists=False,
        client_settings=Settings(anonymized_telemetry=False),
    )
    docs = store.similarity_search(query, k=k)
    if not docs:
        return "知识库中未检索到相关内容"
    blocks = []
    for doc in docs:
        source = str(doc.metadata.get("source", "未知文件"))[:300]
        blocks.append(f"[来源: {source}]\n{doc.page_content[:4000]}")
    return "\n\n".join(blocks)


@tool
async def search_knowledge_base(query: str, k: int = 4) -> str:
    """对企业内部知识库文档做语义检索，返回最相关的原文片段。"""
    if not query.strip() or len(query) > 2000 or not 1 <= k <= 10:
        return "知识库检索失败：问题或检索数量超出限制"
    vs_dir = resolve_path(os.getenv("VECTOR_STORE_DIR", "./vector_store"))
    if not vs_dir.exists() or not (vs_dir / MANIFEST).is_file():
        return "知识库索引尚未构建，请先运行 scripts/build_knowledge_base.py"
    try:
        manifest = json.loads((vs_dir / MANIFEST).read_text())
        if manifest.get("model") != model_name() or manifest.get("collection") != COLLECTION_NAME:
            return "知识库检索失败：索引与当前 Embedding 模型不匹配"
        return await asyncio.to_thread(run_worker, "search", {"query": query, "k": k, "directory": str(vs_dir)}, 60)
    except (TimeoutError, subprocess.TimeoutExpired):
        return "知识库检索失败：检索超时"
    except Exception:
        return "知识库检索失败：索引或模型不可用"
