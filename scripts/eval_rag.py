"""RAG 质量评估（TDD §7.3 / §9.4）。

检索层：对每条 query 调 search_knowledge_base，判断是否命中 expected_source，
计算命中率；低于 0.7 提示调整分块参数或 embedding 模型。
生成层（进阶）：用 RAGAS 计算 Faithfulness / Answer Relevance /
Context Precision / Context Recall 四指标。
"""
from __future__ import annotations

import asyncio
import json
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
    """检查检索结果片段里是否包含期望的来源文件名。"""
    return f"[来源: {expected_source}]" in result


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


async def evaluate_ragas(questions: list[dict]) -> None:
    """生成层评估（进阶，TDD §9.4）：RAGAS 四指标。

    依赖 `pip install ragas`，用 LLM 生成答案并做裁判打分。
    """
    try:
        from ragas import evaluate
        from ragas.metrics import (
            answer_relevancy,
            context_precision,
            context_recall,
            faithfulness,
        )
        from datasets import Dataset
    except ImportError:
        print("\n[RAGAS] 未安装 ragas，跳过生成层评估（pip install ragas）")
        return

    from agent.llm import get_llm
    from tools.knowledge_base_tool import _get_embedding

    llm = get_llm()
    embeddings = _get_embedding()

    rows = {"question": [], "answer": [], "contexts": [], "ground_truth": []}
    for q in questions:
        query = q["query"]
        context = await search_knowledge_base.ainvoke({"query": query, "k": 4})
        prompt = f"根据以下资料回答问题：\n\n资料：\n{context}\n\n问题：{query}"
        resp = await llm.ainvoke(prompt)
        answer = resp.content or ""
        rows["question"].append(query)
        rows["answer"].append(answer)
        rows["contexts"].append([context])
        rows["ground_truth"].append(q["expected_source"])

    dataset = Dataset.from_dict(rows)
    result = evaluate(
        dataset,
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        llm=llm,
        embeddings=embeddings,
    )
    print("\n===== RAGAS 生成层指标 =====")
    print(result)


async def main() -> None:
    questions = _load_questions()
    await evaluate_retrieval(questions)
    await evaluate_ragas(questions)


if __name__ == "__main__":
    asyncio.run(main())
