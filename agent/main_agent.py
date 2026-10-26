"""主智能体组装 + 反思循环执行（TDD §4.2 / §5.2）。

- create_deep_agent：用 DeepAgents 组装主智能体（三个子智能体 + 三个文件工具）；
- run_deep_agent：执行「规划-执行-反思」循环，重试上限 MAX_ROUNDS=3，
  先硬校验再软校验，不通过则注入纠错指令重试。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from langchain_core.messages import HumanMessage

from agent.llm import get_llm
from agent.load_prompt import main_agent_config, sub_agents_config
from agent.sub_agents import SUBAGENTS
from api import context
from api.monitor import MonitorCallbackHandler
from tools import reflect_check
from tools.markdown_tools import generate_markdown
from tools.pdf_tools import convert_md_to_pdf
from tools.upload_file_read_tool import read_file_content
from utils import path_utils

# 反思循环重试上限（TDD §5.2）
MAX_ROUNDS = 3

# 主智能体可用的三个文件工具（TDD §7.4）
_FILE_TOOLS = [generate_markdown, convert_md_to_pdf, read_file_content]

# 子智能体「中文名 → key」映射，供 H4 把 task 调用的 subagent_type 还原为数据源 key
_SOURCE_NAME_TO_KEY = {cfg["name"]: key for key, cfg in sub_agents_config.items()}


def create_deep_agent():
    """组装主智能体：三个子智能体 + 三个文件工具（TDD §4.2）。"""
    from deepagents import create_deep_agent as _create

    return _create(
        model=get_llm(),
        tools=_FILE_TOOLS,
        system_prompt=main_agent_config["system_prompt"],
        subagents=SUBAGENTS,
    )


def _track_called_sources(messages) -> list[str]:
    """编排层记录本轮实际调用的子智能体 key（TDD §5.2「数据源调用记录」）。

    主智能体调度子智能体用的是 `task` 工具，其参数 `subagent_type` 即子智能体
    的中文名，据此映射回数据源 key。
    """
    called: set[str] = set()
    for msg in messages:
        for tool_call in getattr(msg, "tool_calls", None) or []:
            if tool_call.get("name") == "task":
                subtype = (tool_call.get("args") or {}).get("subagent_type", "")
                key = _SOURCE_NAME_TO_KEY.get(subtype)
                if key:
                    called.add(key)
    return sorted(called)


def _declare_required_sources(messages) -> list[str]:
    """提取主智能体在规划阶段声明的数据源 key（TDD §5.2 H4）。

    主智能体规划时会声明要用到哪些子智能体（如「我将调用内部知识库助手」），
    据此扫描 AI 消息正文中的子智能体中文名，映射回数据源 key。
    """
    declared: set[str] = set()
    for msg in messages:
        content = getattr(msg, "content", None)
        if not isinstance(content, str):
            continue
        for name, key in _SOURCE_NAME_TO_KEY.items():
            if name in content:
                declared.add(key)
    return sorted(declared)


def _extract_report(session_dir: Path, messages) -> str:
    """提取报告正文：优先取 generate_markdown 调用的 content，其次最新 .md，最后末条消息。"""
    # 1. generate_markdown 工具调用的 content 参数（最准确）
    for msg in reversed(messages):
        for tool_call in getattr(msg, "tool_calls", None) or []:
            if tool_call.get("name") == "generate_markdown":
                content = (tool_call.get("args") or {}).get("content", "")
                if content:
                    return content
    # 2. 会话目录中最新的 .md 文件
    md_files = sorted(
        session_dir.glob("*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if md_files:
        return md_files[0].read_text(encoding="utf-8")
    # 3. 最后一条消息文本
    for msg in reversed(messages):
        content = getattr(msg, "content", None)
        if isinstance(content, str) and content.strip():
            return content
    return ""


def _prepare_session(thread_id: str, session_dir: Path) -> None:
    """创建工作目录 + 复制上传文件（TDD §5.2 步骤 2）。

    工作环境指令（相对路径规则）已由 system_prompt 的「工作目录规则」承载，
    此处补充上传文件的装配。
    """
    session_dir.mkdir(parents=True, exist_ok=True)
    upload_dir = path_utils.get_upload_dir(thread_id)
    if upload_dir.exists():
        for f in upload_dir.iterdir():
            if f.is_file():
                shutil.copy2(f, session_dir / f.name)


async def main_agent_run(query: str, round_num: int) -> dict:
    """执行一轮主智能体（规划 + 执行），返回 ``result``（供 hard_check / soft_review）。

    ``round_num`` 为当前轮次（1 起），可用于注入轮次相关指令，当前未使用。
    """
    agent = create_deep_agent()
    # 通过回调实时上报 tool_start / assistant_call 事件（TDD §8.2）
    callback = MonitorCallbackHandler(context.get_thread_id() or "unknown")
    resp = await agent.ainvoke(
        {"messages": [HumanMessage(content=query)]},
        config={"callbacks": [callback]},
    )
    messages = resp.get("messages", [])
    session_dir = Path(context.get_session_dir() or ".")

    return {
        "report": _extract_report(session_dir, messages),
        "called_sources": _track_called_sources(messages),
        "required_sources": _declare_required_sources(messages),
        "require_pdf": "pdf" in query.lower(),
        "pdf_produced": any(session_dir.glob("*.pdf")),
    }


async def soft_review(result: dict) -> str | None:
    """软校验（提示词驱动）：主智能体以审稿人视角审阅报告，返回问题描述或 None。"""
    review_prompt = (
        "你是一名严格的内容审稿人，请审阅下面的报告，判断它是否：\n"
        "1. 完整回答了用户的问题；\n2. 数据能够支撑结论；\n3. 逻辑一致。\n"
        "如果全部满足，只回复两个字「通过」；否则回复「不通过：<问题描述>」。\n\n"
        f"报告内容：\n---\n{result['report']}\n---\n"
    )
    resp = await get_llm().ainvoke(review_prompt)
    text = (resp.content or "").strip()
    return None if text == "通过" else text


def build_correction_prompt(
    query: str, hard_issues: list[str], soft_issue: str | None
) -> str:
    """把未通过项拼成明确纠错指令，注入下一轮 query（TDD §5.2 3.5）。"""
    parts = [f"- {issue}" for issue in hard_issues]
    if soft_issue:
        parts.append(f"- {soft_issue}")
    if not parts:
        return query
    return f"{query}\n\n【上一轮结果未通过校验，请修正后重新生成】\n" + "\n".join(parts)


async def run_deep_agent(query: str, thread_id: str) -> str:
    """执行「规划-执行-反思」循环，返回最终报告（严格照抄 TDD §5.2 伪代码）。"""
    # 1. 会话上下文：设置 thread_id 与 session 目录
    session_dir = path_utils.get_session_dir(thread_id)
    context.set_session(thread_id, str(session_dir))
    # 2. 创建工作目录 + 复制上传文件 + 生成工作环境指令
    _prepare_session(thread_id, session_dir)

    # 3. 反思循环
    round_num = 0
    result: dict | None = None
    last_hard_issues: list[str] = []

    while round_num < MAX_ROUNDS:
        round_num += 1

        # 3.1 规划 + 执行（由主智能体完成，提示词驱动）
        result = await main_agent_run(query, round_num)

        # 3.2 硬校验（程序级）
        hard_issues = reflect_check.hard_check(result)
        last_hard_issues = hard_issues

        # 3.3 软校验（提示词驱动）
        soft_issue = None
        if not hard_issues:
            soft_issue = await soft_review(result)

        # 3.4 判定
        if not hard_issues and not soft_issue:
            return result["report"]

        # 3.5 生成纠错指令，注入下一轮
        query = build_correction_prompt(query, hard_issues, soft_issue)

    # 4. 达到上限仍未通过：返回最优结果 + 未达标原因
    tail = ("\n\n【未达标说明】" + "；".join(last_hard_issues)) if last_hard_issues else ""
    return (result["report"] if result else "") + tail


if __name__ == "__main__":
    import asyncio

    async def _demo():
        print(await run_deep_agent("列出所有抗生素类药品", "demo"))

    asyncio.run(_demo())
