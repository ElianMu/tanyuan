"""RAG 生成层评估：仅用 RAGAS 四指标（TDD §9.4 进阶）。

对每条 query：检索上下文 + 生成答案，再用 RAGAS 计算
Faithfulness / Answer Relevance / Context Precision / Context Recall。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# 允许以 `python scripts/eval_ragas.py` 直接运行时导入项目内模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datasets import Dataset
from ragas import evaluate
from ragas.metrics import (
    answer_relevancy,
    context_precision,
    context_recall,
    faithfulness,
)

from agent.llm import get_llm
from tools.knowledge_base_tool import _get_embedding, search_knowledge_base

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_EVAL_FILE = _PROJECT_ROOT / "eval" / "eval_questions_ragas.json"


def _load_questions() -> list[dict]:
    return json.loads(_EVAL_FILE.read_text(encoding="utf-8"))


async def _generate_answer(llm, query: str, context: str) -> str:
    """用检索到的上下文生成答案。"""
    prompt = f"根据以下资料回答问题：\n\n资料：\n{context}\n\n问题：{query}\n\n回答："
    resp = await llm.ainvoke(prompt)
    return resp.content or ""


async def _build_rows(llm) -> dict:
    """检索 + 生成答案（异步阶段）。"""
    questions = _load_questions()
    rows = {"question": [], "answer": [], "contexts": [], "ground_truth": []}
    for q in questions:
        query = q["question"]
        context = await search_knowledge_base.ainvoke({"query": query, "k": 4})
        answer = await _generate_answer(llm, query, context)
        rows["question"].append(query)
        rows["answer"].append(answer)
        rows["contexts"].append([context])
        rows["ground_truth"].append(q["ground_truth"])
        print(f"已生成答案：{query}")
    return rows


def main() -> None:
    llm = get_llm()

    # 异步阶段：检索 + 生成（此时有自己的事件循环）
    rows = asyncio.run(_build_rows(llm))

    print("\n开始 RAGAS 评估（LLM-as-judge，耗时较长）...")
    dataset = Dataset.from_dict(rows)

    # 同步阶段：ragas.evaluate 内部自行用 asyncio.run 跑异步裁判，
    # 避免与已运行的事件循环冲突（nest_asyncio 在 Py3.14 上不可靠）。
    result = evaluate(
        dataset,
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        llm=llm,
        embeddings=_get_embedding(),
        allow_nest_asyncio=False,
    )
    print("\n===== RAGAS 生成层指标 =====")
    print(result)


if __name__ == "__main__":
    main()
