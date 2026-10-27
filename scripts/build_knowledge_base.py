"""离线构建内部知识库向量索引（TDD §9.2）。

加载 knowledge_base/ 语料 → 分块 → BGE 向量化 → 持久化到 vector_store/。
"""
from __future__ import annotations

import os
import json
import hashlib
import sys
from pathlib import Path

# 允许以 `python scripts/build_knowledge_base.py` 直接运行时导入项目内模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from utils.logger_utils import get_logger
from utils.knowledge_utils import COLLECTION_NAME, MANIFEST, get_embedding, model_name, resolve_path, run_worker

logger = get_logger("tanyuan.build")

# 与 tools/knowledge_base_tool.py 保持一致
_CHUNK_SIZE = 500
_CHUNK_OVERLAP = 50


# ---- 多格式文档解析（TDD §2：python-docx / pypdf / openpyxl / pandas）----

def _load_text(path: Path) -> str:
    """读取文本文件，自动兼容 UTF-8 与 GB18030（覆盖 GBK/GB2312）。"""
    raw = path.read_bytes()
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _load_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _load_docx(path: Path) -> str:
    import docx

    d = docx.Document(str(path))
    return "\n".join(p.text for p in d.paragraphs)


def _load_xlsx(path: Path) -> str:
    import pandas as pd

    sheets = pd.read_excel(path, sheet_name=None)
    parts = [f"[Sheet: {name}]\n{df.to_csv(index=False)}" for name, df in sheets.items()]
    return "\n\n".join(parts)


_LOADERS = {
    ".txt": _load_text,
    ".md": _load_text,
    ".pdf": _load_pdf,
    ".docx": _load_docx,
    ".xlsx": _load_xlsx,
    ".xls": _load_xlsx,
}


def load_documents(kb_dir: Path) -> list[Document]:
    """递归加载 kb_dir 下的所有支持文档，来源元数据用文件名。"""
    docs: list[Document] = []
    for path in sorted(kb_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(kb_dir.resolve()):
            raise ValueError("语料目录不允许符号链接或越界文件")
        loader = _LOADERS.get(path.suffix.lower())
        if loader is None:
            logger.warning("跳过不支持的类型: %s", path.name)
            continue
        if path.stat().st_size > 10 * 1024 * 1024 or len(docs) >= 500:
            raise ValueError("语料文件或文档数量超出限制")
        text = loader(path)
        if len(text) > 2_000_000:
            raise ValueError("语料解析文本超出限制")
        if not text.strip():
            logger.warning("跳过空内容: %s", path.name)
            continue
        docs.append(Document(page_content=text, metadata={
            "source": path.relative_to(kb_dir).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }))
        logger.info("加载 %s（%d 字符）", path.name, len(text))
    return docs


def build_index() -> int:
    target = resolve_path(os.getenv("VECTOR_STORE_DIR", "./vector_store"))
    if target.exists():
        raise FileExistsError("索引目标已经存在，禁止覆盖")
    return run_worker("build", {}, timeout=600)


def _build_index() -> int:
    """构建向量索引，返回分块总数。"""
    kb_dir = resolve_path(os.getenv("KNOWLEDGE_BASE_DIR", "./knowledge_base"))
    raw_target = Path(os.getenv("VECTOR_STORE_DIR", "./vector_store")).expanduser()
    vs_dir = resolve_path(str(raw_target))
    if raw_target.is_symlink() or vs_dir.exists():
        raise FileExistsError("索引目标已经存在；请选择新目录，禁止覆盖或删除既有索引")
    if vs_dir.is_relative_to(kb_dir) or kb_dir.is_relative_to(vs_dir):
        raise ValueError("语料与索引目录必须互相独立")

    if not kb_dir.exists():
        logger.error("语料目录不存在: %s", kb_dir)
        return 0

    logger.info("语料目录: %s", kb_dir)
    documents = load_documents(kb_dir)
    if not documents:
        logger.error("未加载到任何文档，构建终止")
        return 0

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=_CHUNK_SIZE,
        chunk_overlap=_CHUNK_OVERLAP,
    )
    chunks = splitter.split_documents(documents)
    logger.info("分块完成：%d 个文档 → %d 个分块", len(documents), len(chunks))

    if len(chunks) > 10000:
        raise ValueError("语料分块数量超出限制")
    embedding = get_embedding()
    # Never replace historical artifacts. No ready marker is published on failure.
    vs_dir.mkdir(parents=True, exist_ok=False)

    from langchain_chroma import Chroma
    from chromadb.config import Settings

    vector_store = Chroma.from_documents(
        chunks,
        embedding,
        persist_directory=str(vs_dir),
        collection_name=COLLECTION_NAME,
        client_settings=Settings(anonymized_telemetry=False),
    )
    # 显式关闭 chromadb 系统，强制把 HNSW 索引 .bin 文件刷到磁盘。
    # chromadb 的 Rust 后端若在进程退出前不 stop，.bin 文件可能未落盘，
    # 导致后续检索时报 "Error loading hnsw index"。
    vector_store._client._system.stop()
    (vs_dir / MANIFEST).write_text(json.dumps({
        "version": 1, "model": model_name(), "collection": COLLECTION_NAME,
        "chunks": len(chunks), "chunk_size": _CHUNK_SIZE, "chunk_overlap": _CHUNK_OVERLAP,
        "sources": [doc.metadata for doc in documents],
    }, ensure_ascii=False), encoding="utf-8")
    logger.info("向量索引已持久化到: %s", vs_dir)
    return len(chunks)


if __name__ == "__main__":
    try:
        count = build_index()
        logger.info("构建完成，共 %d 个分块", count)
        raise SystemExit(0 if count else 1)
    except Exception as exc:
        logger.error("构建失败（%s），保留所有已有产物", type(exc).__name__)
        raise SystemExit(1)
