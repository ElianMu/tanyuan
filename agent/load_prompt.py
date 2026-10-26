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


def load_prompts() -> dict:
    """读取 prompts.yaml，返回完整配置字典。"""
    with open(_PROMPT_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


# 模块级导出：主智能体配置 + 子智能体配置
_data = load_prompts()
main_agent_config: dict = _data["main_agent"]
sub_agents_config: dict = _data["sub_agents"]


if __name__ == "__main__":
    print("=== main_agent 配置 ===")
    print("system_prompt:")
    print(main_agent_config["system_prompt"])
    print()

    print("=== sub_agents 配置 ===")
    for key, cfg in sub_agents_config.items():
        print(f"[{key}]")
        print(f"  name        : {cfg['name']}")
        print(f"  description : {cfg['description'].strip()}")
        print(f"  system_prompt 前 60 字: {cfg['system_prompt'].strip()[:60]} ...")
        print()
