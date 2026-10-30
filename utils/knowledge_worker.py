"""Internal BGE/Chroma worker. Called with a fresh empty cwd, never with secrets."""
from __future__ import annotations
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    payload = json.load(sys.stdin)
    # Library progress output cannot corrupt the JSON protocol.
    with redirect_stdout(sys.stderr):
        if sys.argv[1] == "build":
            from scripts.build_knowledge_base import _build_index
            result = _build_index()
        elif sys.argv[1] == "search":
            from tools.knowledge_base_tool import _search_sync
            result = _search_sync(payload["query"], payload["k"], Path(payload["directory"]))
        else:
            raise ValueError("Unknown operation")
    json.dump(result, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        from utils.logger_utils import get_logger
        get_logger("tanyuan.rag_worker").error("RAG worker failed: %s", type(exc).__name__)
        raise SystemExit(1)
