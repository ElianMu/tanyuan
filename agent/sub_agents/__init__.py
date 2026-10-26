"""子智能体配置导出（TDD §4.2）。"""
from __future__ import annotations

from agent.sub_agents.web_search_agent import web_search_agent
from agent.sub_agents.db_query_agent import db_query_agent
from agent.sub_agents.knowledge_agent import knowledge_agent

# 三个子智能体的完整声明，供 create_deep_agent 组装主智能体
SUBAGENTS = [web_search_agent, db_query_agent, knowledge_agent]

__all__ = ["web_search_agent", "db_query_agent", "knowledge_agent", "SUBAGENTS"]
