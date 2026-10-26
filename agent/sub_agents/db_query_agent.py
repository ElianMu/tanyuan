"""数据库查询子智能体（TDD §6.3）。"""
from __future__ import annotations

from agent.load_prompt import sub_agents_config
from tools.mysql_tools import execute_sql_query, get_table_data, list_sql_tables

_cfg = sub_agents_config["db_query"]

db_query_agent = {
    "name": _cfg["name"],
    "description": _cfg["description"],
    "system_prompt": _cfg["system_prompt"],
    "tools": [list_sql_tables, get_table_data, execute_sql_query],
}
