"""LLM 初始化（TDD §4.2 / §2 / §12）。

使用 langchain 的 ``init_chat_model`` 通过 OpenAI 兼容协议接入 DeepSeek，
模型名 / 端点 / 密钥均从环境变量读取。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

# 加载项目根目录的 .env（保证直接运行本脚本也能读到配置）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


def get_llm():
    """初始化 LLM 实例（默认 deepseek-chat，OpenAI 兼容协议）。

    timeout / max_retries 按 TDD §13 设置，避免外部调用无限阻塞。
    """
    return init_chat_model(
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        model_provider="openai",
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        timeout=60,
        max_retries=2,
    )


if __name__ == "__main__":
    llm = get_llm()
    print("LLM 初始化成功")
    print("类型:", type(llm).__name__)
    print("模型:", getattr(llm, "model_name", None))
    print("端点:", getattr(llm, "openai_api_base", None))
