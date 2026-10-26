"""内部知识库检索工具 search_knowledge_base（TDD §7.3）。

对本地 Chroma 向量库做相似度检索，返回 top-k 个最相关分块（含来源文件名）。
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_core.tools import tool
from langchain_huggingface import HuggingFaceEmbeddings

# 加载项目根目录的 .env
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

# 与 scripts/build_knowledge_base.py 保持一致
_COLLECTION_NAME = "tanyuan_kb"


def _resolve_path(p: str) -> Path:
    """把 .env 里的（可能是相对的）路径解析为绝对路径。"""
    path = Path(p)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return path.resolve()


_embedding: HuggingFaceEmbeddings | None = None


def _get_embedding() -> HuggingFaceEmbeddings:
    """按 TDD §12 读取 Embedding 模型：优先本地路径，否则用模型名（懒加载 + 缓存）。"""
    global _embedding
    if _embedding is None:
        model_path = os.getenv("EMBEDDING_MODEL_PATH", "").strip()
        model_name = (
            str(_resolve_path(model_path))
            if model_path
            else os.getenv("EMBEDDING_MODEL", "BAAI/bge-base-zh-v1.5")
        )
        _embedding = HuggingFaceEmbeddings(model_name=model_name)
    return _embedding


def _search_sync(query: str, k: int, vs_dir: Path) -> str:
    """同步执行嵌入 + 检索（放到独立线程跑，避免 torch 在事件循环里崩溃）。"""
    store = Chroma(
        persist_directory=str(vs_dir),
        embedding_function=_get_embedding(),
        collection_name=_COLLECTION_NAME,
    )
    docs = store.similarity_search(query, k=k)
    if not docs:
        return "知识库中未检索到相关内容"
    blocks = []
    for doc in docs:
        source = doc.metadata.get("source", "未知文件")
        blocks.append(f"[来源: {source}]\n{doc.page_content}")
    return "\n\n".join(blocks)


@tool
async def search_knowledge_base(query: str, k: int = 4) -> str:
    """对企业内部知识库文档做语义检索，返回最相关的原文片段。"""
    vs_dir = _resolve_path(os.getenv("VECTOR_STORE_DIR", "./vector_store"))
    if not vs_dir.exists():
        return "知识库索引尚未构建，请先运行 scripts/build_knowledge_base.py"
    try:
        return await asyncio.to_thread(_search_sync, query, k, vs_dir)
    except Exception as e:  # noqa: BLE001
        return f"知识库检索失败：{e}"


if __name__ == "__main__":
    import asyncio

    print(
        asyncio.run(
            search_knowledge_base.ainvoke({"query": "企业内部控制 风险管理", "k": 2})
        )
    )
