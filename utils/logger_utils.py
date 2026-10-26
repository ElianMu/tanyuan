"""全局彩色日志（TDD §4.4）。

统一日志格式：时间 / 文件 / 行号 / 级别 / 消息，按级别着色。
使用 colorlog 实现彩色输出，供全项目各模块通过 ``get_logger`` 复用。
"""
from __future__ import annotations

import logging

import colorlog

# 日志格式：时间 + 彩色级别 + 文件名:行号 + 消息
LOG_FORMAT = (
    "%(asctime)s "
    "%(log_color)s%(levelname)-8s%(reset)s "
    "%(cyan)s%(filename)s:%(lineno)d%(reset)s - "
    "%(message)s"
)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 各日志级别对应颜色
LOG_COLORS = {
    "DEBUG": "cyan",
    "INFO": "green",
    "WARNING": "yellow",
    "ERROR": "red",
    "CRITICAL": "red,bg_white",
}


def get_logger(name: str = "tanyuan") -> logging.Logger:
    """返回配置好彩色格式的日志记录器（幂等，重复调用不叠加 handler）。"""
    logger = colorlog.getLogger(name)
    if not logger.handlers:
        handler = colorlog.StreamHandler()
        handler.setFormatter(
            colorlog.ColoredFormatter(
                LOG_FORMAT,
                datefmt=DATE_FORMAT,
                log_colors=LOG_COLORS,
            )
        )
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
    return logger


if __name__ == "__main__":
    log = get_logger("tanyuan.demo")
    log.debug("这是一条 DEBUG 日志")
    log.info("这是一条 INFO 日志")
    log.warning("这是一条 WARNING 日志")
    log.error("这是一条 ERROR 日志")
    log.critical("这是一条 CRITICAL 日志")
