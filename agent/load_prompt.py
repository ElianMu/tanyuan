"""提示词加载（TDD §4.2 / §6）。

读取 prompt/prompts.yaml，导出主智能体配置 main_agent_config 与
三个子智能体配置 sub_agents_config。
"""
from __future__ import annotations

from pathlib import Path

import yaml

# 项目根目录 + 提示词文件路径
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PROMPT_FILE = _PROJECT_ROOT / "prompt" / "prompts.yaml"


class PromptLoader(yaml.SafeLoader):
    """Reject ambiguous duplicate configuration keys."""


def _mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise ValueError("提示词配置包含非法或重复字段")
        result[key] = loader.construct_object(value_node)
    return result


PromptLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_prompts(path: Path | None = None) -> dict:
    """读取 prompts.yaml，返回完整配置字典。"""
    text = (path or _PROMPT_FILE).read_text(encoding="utf-8")
    if len(text.encode("utf-8")) > 128 * 1024:
        raise ValueError("提示词配置超过 128 KiB")
    data = yaml.load(text, Loader=PromptLoader)
    if not isinstance(data, dict) or set(data) != {"main_agent", "sub_agents"}:
        raise ValueError("提示词必须包含 main_agent 和 sub_agents")
    main = data["main_agent"]
    agents = data["sub_agents"]
    if not isinstance(main, dict) or set(main) != {"system_prompt"} or not isinstance(main["system_prompt"], str) or not main["system_prompt"].strip():
        raise ValueError("主智能体提示词缺失或格式错误")
    if not isinstance(agents, dict) or set(agents) != {"web_search", "db_query", "knowledge"}:
        raise ValueError("必须配置三个指定的子智能体")
    names = []
    for config in agents.values():
        if not isinstance(config, dict) or set(config) != {"name", "description", "system_prompt"}:
            raise ValueError("子智能体字段错误")
        if any(not isinstance(value, str) or not value.strip() for value in config.values()):
            raise ValueError("子智能体字段必须为非空文本")
        names.append(config["name"])
    if len(set(names)) != 3:
        raise ValueError("子智能体名称必须唯一")
    return data


# 模块级导出：主智能体配置 + 子智能体配置
_data = load_prompts()
main_agent_config: dict = _data["main_agent"]
sub_agents_config: dict = _data["sub_agents"]
