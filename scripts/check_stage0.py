"""阶段 0 离线验收：不加载 .env、不执行 Agent、不连接外部服务。"""
from __future__ import annotations

import json
import runpy
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def assert_local_port_available(port=8000):
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", port))
        probe.listen(1)


def guard(event, args):
    if event == "open" and isinstance(args[0], (str, bytes)):
        name = Path(args[0]).name
        if name == ".env" or name.startswith(".env."):
            raise RuntimeError("验收禁止读取环境密钥文件")
    if event == "socket.connect":
        address = args[1]
        if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1"):
            raise RuntimeError("验收禁止外部网络连接")


def main():
    sys.addaudithook(guard)
    if "--serve-child" in sys.argv:
        runpy.run_path(str(ROOT / "main.py"), run_name="__main__")
        return 0

    import main as entry
    from utils import path_utils
    from utils.logger_utils import get_logger

    assert callable(entry.start_server)
    assert "agent.main_agent" not in sys.modules
    print("PASS: main / logger / path 模块导入，未加载 Agent", flush=True)
    logger = get_logger("tanyuan.stage0")
    count = len(logger.handlers)
    assert get_logger("tanyuan.stage0") is logger
    assert len(logger.handlers) == count == 1
    logger.info("阶段 0 日志初始化验证")
    print("PASS: 日志初始化且无重复 handler", flush=True)

    assert path_utils.validate_path(str(ROOT / "main.py"), path_utils.OUTPUT_DIR) is None
    assert path_utils.validate_path(str(path_utils.OUTPUT_DIR / ".." / "main.py"), path_utils.OUTPUT_DIR) is None
    with tempfile.TemporaryDirectory(prefix="tanyuan-stage0-") as tmp:
        base = Path(tmp)
        allowed = base / "allowed"
        allowed.mkdir()
        (allowed / "escape").symlink_to(base, target_is_directory=True)
        try:
            path_utils.resolve_within(allowed, "escape/outside.txt")
        except path_utils.PathOutOfBoundsError:
            pass
        else:
            raise AssertionError("未拒绝符号链接越界")
    print("PASS: validate_path 拒绝越界；resolve_within 拒绝符号链接逃逸", flush=True)
    # 回归断言：非法 ID 必须被拒绝，不能仅把逃逸结果记录为已知问题。
    try:
        path_utils.get_session_dir("x/../../outside")
    except path_utils.InvalidSessionIDError:
        pass
    else:
        raise AssertionError("会话 ID 目录逃逸未被拒绝")
    import unittest
    from test_session_paths import SessionPathTests
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(SessionPathTests)
    )
    if not result.wasSuccessful():
        return 1
    print("PASS: 会话 ID / HTTP / 上传 / 符号链接安全回归测试", flush=True)

    # 先确认端口空闲，绝不终止用户已有服务。
    assert_local_port_available()
    with tempfile.TemporaryFile(mode="w+") as logs:
        child = subprocess.Popen(
            [sys.executable, "-B", str(Path(__file__).resolve()), "--serve-child"],
            cwd=ROOT, stdout=logs, stderr=logs,
        )
        try:
            opener = build_opener(ProxyHandler({}))
            deadline = time.monotonic() + 15
            while True:
                if child.poll() is not None:
                    raise RuntimeError("FastAPI 启动进程提前退出")
                try:
                    with opener.open("http://127.0.0.1:8000/", timeout=1) as response:
                        assert response.status == 200
                        data = json.load(response)
                        assert data["status"] == "ok"
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.1)
            print("PASS: python main.py 在 127.0.0.1:8000 启动，GET / 返回 HTTP 200", flush=True)
        finally:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            logs.seek(0)
            print(logs.read(), end="", flush=True)
    print("阶段 0 验收 PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
