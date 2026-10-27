"""Explicit local acceptance budget; reserve before HTTP, never retry or reset."""
from __future__ import annotations
import fcntl
import json
import os
import stat
from langchain_core.callbacks import BaseCallbackHandler


def reserve(provider: str) -> None:
    path = os.getenv("TANYUAN_CALL_BUDGET_FILE", "")
    if not path:
        raise RuntimeError("未配置已授权的外部调用额度")
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    with os.fdopen(fd, "r+") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            raise RuntimeError("调用额度文件必须为权限 0600 的普通文件")
        fcntl.flock(stream, fcntl.LOCK_EX)
        data = json.load(stream)
        entry = data[provider]
        if not (type(entry["used"]) is int and type(entry["limit"]) is int
                and 0 <= entry["used"] < entry["limit"] <= 1000):
            raise RuntimeError("已达到外部调用额度或额度配置非法")
        entry["used"] += 1
        stream.seek(0); json.dump(data, stream); stream.truncate(); stream.flush(); os.fsync(stream.fileno())


class BudgetCallback(BaseCallbackHandler):
    raise_error = True

    def on_chat_model_start(self, serialized, messages, **kwargs):
        reserve("deepseek")
