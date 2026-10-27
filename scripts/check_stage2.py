"""Real stage 2 acceptance. Local BGE by default; external calls require explicit flag.

External mode sends one short public prompt to DeepSeek (512 output tokens,
no retries), and one basic Tavily search. It never prints keys or raw exceptions.
"""
from __future__ import annotations
import argparse
import asyncio
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def local_check():
    from scripts.build_knowledge_base import build_index
    from tools.knowledge_base_tool import search_knowledge_base
    base = ROOT / "output" / ("stage2-acceptance-" + uuid.uuid4().hex)
    corpus = base / "corpus"
    corpus.mkdir(parents=True, exist_ok=False)
    fixtures = {
        "antibiotics.md": "阿莫西林是一种青霉素类抗生素。抗生素用于治疗敏感细菌引起的感染，不用于治疗病毒感冒。",
        "inventory.md": "库存管理采用批次管理和先进先出原则。药品入库时登记批号、生产日期和有效期，定期检查库存数量和仓库位置。",
        "sales.md": "销售分析统计销售数量、销售单价和销售总额，按客户、区域和销售代表汇总收入，比较销售趋势。",
    }
    for name, text in fixtures.items():
        (corpus / name).write_text(text, encoding="utf-8")
    os.environ.update(KNOWLEDGE_BASE_DIR=str(corpus), VECTOR_STORE_DIR=str(base / "index"))
    count = build_index()
    assert count == 3, "Unexpected fixture chunk count"
    print("PASS: real local BGE embedding + Chroma persistence (3 public test documents)", flush=True)
    for query, source in [("阿莫西林属于哪类抗生素？", "antibiotics.md"),
                          ("如何管理药品库存批号和有效期？", "inventory.md"),
                          ("按区域和销售代表汇总销售总额", "sales.md")]:
        result = asyncio.run(search_knowledge_base.ainvoke({"query": query, "k": 1}))
        assert f"[来源: {source}]" in result, "Real retrieval source mismatch or model failure"
        print("PASS: real semantic retrieval source", source, flush=True)
    try:
        build_index()
    except FileExistsError:
        print("PASS: existing index preserved; repeated build refused", flush=True)
    else:
        raise AssertionError("Existing index was not protected")
    print("ARTIFACTS:", base.relative_to(ROOT), flush=True)


def external_check(provider="both"):
    required = {"deepseek": "DEEPSEEK_API_KEY", "tavily": "TAVILY_API_KEY"}
    selected = list(required) if provider == "both" else [provider]
    if not all(os.environ.get(required[name]) for name in selected):
        print("BLOCKED: configure selected provider credentials locally; keys are never printed")
        return 2
    from agent.llm import get_llm
    from tools.internet_search_tool import internet_search, search_scope
    if "deepseek" in selected:
        llm = get_llm(max_tokens=512, max_retries=0)
        response = llm.invoke("请只回复：探渊连接正常。不要调用工具。")
        assert response.content, "Empty DeepSeek response"
        print("PASS: real DeepSeek response (one request; content omitted)", flush=True)
    if "tavily" in selected:
        with search_scope():
            result = internet_search.invoke({"query": "MySQL 8.4 official SELECT documentation", "max_results": 2})
        assert "URL: https://" in result and not result.startswith("网络检索失败"), "Tavily failed or returned no valid sources"
        print("PASS: real Tavily basic search (one request; source URLs validated)", flush=True)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--external", action="store_true")
    parser.add_argument("--provider", choices=("both", "deepseek", "tavily"), default="both")
    parser.add_argument("--env-file", type=Path, help="Explicitly authorized credential file; external mode only")
    args = parser.parse_args()
    try:
        if args.env_file:
            if not args.external:
                raise ValueError("Credential files are only accepted for explicit external acceptance")
            from dotenv import dotenv_values
            # Do not override DB/local data paths, and do not print file values.
            values = dotenv_values(args.env_file)
            for key in ("DEEPSEEK_API_KEY", "TAVILY_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"):
                if values.get(key):
                    os.environ[key] = values[key]
        raise SystemExit(external_check(args.provider) if args.external else local_check())
    except Exception as exc:
        print("FAIL: stage 2 acceptance:", type(exc).__name__, "(sensitive exception details suppressed)")
        raise SystemExit(1)
