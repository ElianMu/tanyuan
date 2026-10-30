"""RAG 质量评估（TDD §7.3 / §9.4）。

检索层：对每条 query 调 search_knowledge_base，判断是否命中 expected_source，
计算命中率；低于 0.7 提示调整分块参数或 embedding 模型。
生成层（进阶）见 scripts/eval_ragas.py：需把私有片段发往 LLM 裁判并消耗额度，
不在本脚本中自动执行。
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

# 允许以 `python scripts/eval_rag.py` 直接运行时导入项目内模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.knowledge_base_tool import search_knowledge_base

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_EVAL_FILE = _PROJECT_ROOT / "eval" / "eval_questions.json"
HIT_RATE_THRESHOLD = 0.7


def _load_questions() -> list[dict]:
    return json.loads(_EVAL_FILE.read_text(encoding="utf-8"))


def _check_hit(result: str, expected_source: str) -> bool:
    """检查检索结果片段的来源文件名是否包含期望来源（索引记录的是相对路径）。"""
    sources = re.findall(r"^\[来源: (.+)\]$", result, flags=re.MULTILINE)
    return any(Path(source).name == expected_source for source in sources)


async def evaluate_retrieval(questions: list[dict]) -> float:
    """检索层评估：返回命中率。"""
    hits = 0
    print("===== 检索层命中情况 =====")
    for q in questions:
        query = q["query"]
        expected = q["expected_source"]
        result = await search_knowledge_base.ainvoke({"query": query, "k": 4})
        hit = _check_hit(result, expected)
        hits += 1 if hit else 0
        print(f"{'[命中]' if hit else '[未命中]'} {query}  ->  {expected}")

    total = len(questions)
    hit_rate = hits / total
    print(f"\n总体命中率：{hits}/{total} = {hit_rate:.2f}")
    if hit_rate < HIT_RATE_THRESHOLD:
        print("建议调整分块参数或 embedding 模型")
    return hit_rate


async def main() -> None:
    questions = _load_questions()
    hit_rate = await evaluate_retrieval(questions)
    raise SystemExit(0 if hit_rate >= HIT_RATE_THRESHOLD else 1)


if __name__ == "__main__":
    asyncio.run(main())
