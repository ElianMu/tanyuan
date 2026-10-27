"""LLM 初始化（TDD §4.2 / §2 / §12）。

使用 langchain 的 ``init_chat_model`` 通过 OpenAI 兼容协议接入 DeepSeek，
模型名 / 端点 / 密钥均从环境变量读取。
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit
from langchain.chat_models import init_chat_model

def get_llm(*, max_tokens: int = 4096, max_retries: int = 2):
    """初始化 LLM 实例（默认 deepseek-chat，OpenAI 兼容协议）。

    timeout / max_retries 按 TDD §13 设置，避免外部调用无限阻塞。
    """
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise ValueError("缺少 DEEPSEEK_API_KEY，请通过进程环境注入")
    endpoint = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("DEEPSEEK_BASE_URL 必须为无凭据的 HTTPS 地址")
    if not 1 <= max_tokens <= 8192 or not 0 <= max_retries <= 2:
        raise ValueError("LLM token/retry 限制不合法")
    return init_chat_model(
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        model_provider="openai",
        base_url=endpoint,
        api_key=key,
        timeout=60,
        max_retries=max_retries,
        max_tokens=max_tokens,
    )


if __name__ == "__main__":
    llm = get_llm()
    print("LLM 初始化成功")
    print("类型:", type(llm).__name__)
    print("模型:", getattr(llm, "model_name", None))
    print("端点:", getattr(llm, "openai_api_base", None))
