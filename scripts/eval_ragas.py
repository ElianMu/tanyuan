"""Opt-in RAGAS generation evaluation; local retrieval, budgeted remote judging.

Install: uv sync --locked --extra eval
Run --help for explicit data-transfer and budget requirements. No dotenv loading.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import math
import os
import re
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
ROOT = Path(__file__).resolve().parent.parent


def load_questions(path: Path) -> list[dict]:
    questions = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(questions, list) or not questions:
        raise ValueError('评估题集必须为非空列表')
    for row in questions:
        if not isinstance(row, dict) or any(
            not isinstance(row.get(key), str) or not row[key].strip()
            for key in ('question', 'ground_truth')
        ):
            raise ValueError('每条问题必须含非空 question 和 ground_truth')
    return questions


def split_contexts(text: str) -> list[str]:
    blocks = re.split(r'(?=^\[来源: .+\]$)', text, flags=re.MULTILINE)
    contexts = [block.strip() for block in blocks if block.strip()]
    if not contexts or any(not re.match(r'^\[来源: .+\]\n\S', block) for block in contexts):
        raise ValueError('检索失败或没有有效来源片段，停止生成与评分')
    return contexts


async def build_rows(llm, questions, retrieve):
    rows = []
    for item in questions:
        contexts = split_contexts(await retrieve.ainvoke({'query': item['question'], 'k': 4}))
        response = await llm.ainvoke(
            '仅根据以下资料回答问题，资料不足时明确说明。\n资料：\n'
            + '\n\n'.join(contexts) + '\n问题：' + item['question']
        )
        if not isinstance(response.content, str) or not response.content.strip():
            raise ValueError('模型未返回有效答案')
        rows.append(dict(user_input=item['question'], response=response.content,
                         retrieved_contexts=contexts, reference=item['ground_truth']))
    return rows


def evaluate_rows(rows, llm, embeddings):
    # Explicit wrappers prevent implicit OpenAI models/embeddings. One worker and
    # one attempt disable transport retries; every actual LLM call is budgeted.
    os.environ['RAGAS_DO_NOT_TRACK'] = 'true'
    from ragas import EvaluationDataset, evaluate
    from ragas.llms import LangchainLLMWrapper
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.metrics import Faithfulness, AnswerRelevancy, ContextPrecision, ContextRecall
    from ragas.run_config import RunConfig
    config = RunConfig(max_workers=1, max_retries=1, timeout=180)
    metrics = [Faithfulness(max_retries=0), AnswerRelevancy(strictness=1),
               ContextPrecision(), ContextRecall()]
    result = evaluate(
        EvaluationDataset.from_list(rows), metrics=metrics,
        llm=LangchainLLMWrapper(llm, run_config=config, bypass_n=True),
        embeddings=LangchainEmbeddingsWrapper(embeddings),
        run_config=config, raise_exceptions=True, allow_nest_asyncio=False,
    )
    scores = result.scores
    keys = ('faithfulness', 'answer_relevancy', 'context_precision', 'context_recall')
    if len(scores) != len(rows) or any(
        not isinstance(score.get(key), (int, float)) or not math.isfinite(score[key])
        for score in scores for key in keys
    ):
        raise ValueError('RAGAS 返回缺失或非有限评分，不能判定评估成功')
    return {'scores': scores, 'means': {
        key: sum(score[key] for score in scores) / len(scores) for key in keys
    }}


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--questions', type=Path, default=ROOT/'eval/eval_questions_ragas.json')
    cli.add_argument('--check', action='store_true', help='仅校验题集与可选依赖，不检索或调用模型')
    cli.add_argument('--rows', type=Path, help='恢复已有 rows.json，仅重新执行评分，不重新生成答案')
    cli.add_argument('--allow-context-upload', action='store_true', help='确认授权发送题集及检索片段给模型')
    cli.add_argument('--budget-file', type=Path, help='明确授权的 0600 调用额度文件；不自动创建或扩额')
    cli.add_argument('--output-dir', type=Path, default=ROOT/'output')
    return cli


def main():
    cli = parser(); args = cli.parse_args()
    questions = load_questions(args.questions)
    os.environ['RAGAS_DO_NOT_TRACK'] = 'true'
    if args.check:
        from ragas import EvaluationDataset
        from ragas.llms import LangchainLLMWrapper
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from utils.knowledge_utils import get_embedding
        print(f'依赖及题集校验通过：{len(questions)} 条；未加载模型、未检索、未调用 API')
        return
    if not args.allow_context_upload or not args.budget_file:
        cli.error('真实评估需要 --allow-context-upload 和 --budget-file；仅校验使用 --check')
    budget = args.budget_file.resolve()
    if not budget.is_file() or budget.stat().st_mode & 0o777 != 0o600:
        cli.error('额度文件必须是已授权的 0600 普通文件')
    os.environ['TANYUAN_CALL_BUDGET_FILE'] = str(budget)
    from agent.llm import get_llm
    from utils.call_budget import BudgetCallback
    from utils.knowledge_utils import get_embedding
    from tools.knowledge_base_tool import search_knowledge_base
    llm = get_llm(max_retries=0, callbacks=[BudgetCallback()])
    run = args.output_dir/('ragas-'+uuid4().hex)
    run.mkdir(parents=True, exist_ok=False, mode=0o700)
    def save(name, data):
        with (run/name).open('x', encoding='utf-8') as stream:
            os.chmod(stream.name, 0o600)
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
    try:
        if args.rows:
            rows = json.loads(args.rows.read_text(encoding='utf-8'))
            if not isinstance(rows, list) or not rows or any(
                not isinstance(row, dict) or any(not row.get(key) for key in
                    ('user_input', 'response', 'reference', 'retrieved_contexts'))
                or not isinstance(row['retrieved_contexts'], list)
                or any(not isinstance(context, str) or not context.strip() for context in row['retrieved_contexts'])
                for row in rows
            ):
                raise ValueError('恢复数据必须包含有效问题、答案、参考答案及分块')
        else:
            rows = asyncio.run(build_rows(llm, questions, search_knowledge_base))
        save('rows.json', rows)
        # asyncio.run above closes its loop. A used httpx async client cannot
        # cross into RAGAS's new loop; judging requires a fresh client instance.
        judge = get_llm(max_retries=0, callbacks=[BudgetCallback()])
        result = evaluate_rows(rows, judge, get_embedding())
        save('result.json', result)
    except Exception as exc:
        save('status.json', {'status': 'failed', 'error_type': type(exc).__name__,
                            'cause_type': type(exc.__cause__).__name__ if exc.__cause__ else None})
        print(f'评估失败（{type(exc).__name__}），已有证据保留于 {run}', file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result['means'], ensure_ascii=False, indent=2))
    print(f'评估已执行，逐题结果：{run}；未设置质量通过阈值')


if __name__ == '__main__':
    main()
