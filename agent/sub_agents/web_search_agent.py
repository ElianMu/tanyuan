"""网络检索子智能体（TDD §6.2）。"""
from __future__ import annotations

from agent.load_prompt import sub_agents_config
from tools.internet_search_tool import internet_search

_cfg = sub_agents_config["web_search"]

web_search_agent = {
    "name": _cfg["name"],
    "description": _cfg["description"],
    "system_prompt": _cfg["system_prompt"],
    "tools": [internet_search],
}
