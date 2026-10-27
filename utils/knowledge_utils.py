"""Shared local BGE configuration; no .env, model loading or network on import."""
from __future__ import annotations

from functools import lru_cache
import os
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from threading import Lock

ROOT = Path(__file__).resolve().parent.parent
COLLECTION_NAME = "tanyuan_kb"
MANIFEST = "tanyuan-index.json"
_lock = Lock()


def resolve_path(value: str) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else ROOT / path).resolve()


def model_name() -> str:
    local = os.getenv("EMBEDDING_MODEL_PATH", "").strip()
    if local:
        path = resolve_path(local)
        if not (path / "config.json").is_file() or not (path / "modules.json").is_file():
            raise ValueError("本地 BGE 模型目录不完整")
        return str(path)
    return os.getenv("EMBEDDING_MODEL", "BAAI/bge-base-zh-v1.5")


@lru_cache(maxsize=1)
def _embedding(name: str):
    from langchain_huggingface import HuggingFaceEmbeddings
    return HuggingFaceEmbeddings(
        model_name=name, cache_folder=str(ROOT / "models"),
        model_kwargs={"device": "cpu", "trust_remote_code": False},
        encode_kwargs={"normalize_embeddings": True, "batch_size": 16},
        query_encode_kwargs={"normalize_embeddings": True},
    )


def get_embedding():
    with _lock:
        return _embedding(model_name())


def run_worker(operation: str, payload: dict, timeout: int = 60):
    """Isolate model/Chroma lifecycle and implicit dotenv loading from the server."""
    env = {key: value for key, value in os.environ.items()
           if not any(word in key.upper() for word in ("PASSWORD", "TOKEN", "API_KEY", "SECRET"))}
    env["KNOWLEDGE_BASE_DIR"] = str(resolve_path(os.getenv("KNOWLEDGE_BASE_DIR", "./knowledge_base")))
    env["VECTOR_STORE_DIR"] = str(resolve_path(os.getenv("VECTOR_STORE_DIR", "./vector_store")))
    if os.getenv("EMBEDDING_MODEL_PATH"):
        env["EMBEDDING_MODEL_PATH"] = str(resolve_path(os.environ["EMBEDDING_MODEL_PATH"]))
    env["ANONYMIZED_TELEMETRY"] = "False"
    with tempfile.TemporaryDirectory(prefix="tanyuan-rag-worker-") as cwd:
        child = subprocess.run(
            [sys.executable, "-B", str(ROOT / "utils/knowledge_worker.py"), operation],
            input=json.dumps(payload), text=True, capture_output=True,
            cwd=cwd, env=env, timeout=timeout,
        )
    if child.returncode:
        raise RuntimeError("本地 RAG 子进程失败，已有产物已保留")
    try:
        return json.loads(child.stdout)
    except (ValueError, TypeError):
        raise RuntimeError("本地 RAG 子进程返回格式异常") from None
