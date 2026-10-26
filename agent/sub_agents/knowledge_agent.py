"""内部知识库检索子智能体（TDD §6.4）。"""
from __future__ import annotations

from agent.load_prompt import sub_agents_config
from tools.knowledge_base_tool import search_knowledge_base

_cfg = sub_agents_config["knowledge"]

knowledge_agent = {
    "name": _cfg["name"],
    "description": _cfg["description"],
    "system_prompt": _cfg["system_prompt"],
    "tools": [search_knowledge_base],
}
