"""会话路径与 HTTP 边界回归测试，全部文件操作限制在临时目录。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from api import context, server
from utils import path_utils as paths


INVALID_IDS = [
    "", "../outside", "x/../../outside", "/tmp/outside",
    "..\\outside", "C:\\outside", "\\\\host\\share",
    "%2e%2e%2foutside", "x%2F..%2F..%2Foutside", "%252e%252e%252foutside",
    "a b", "a.b", "a\n", "a\x00b", "会话", "a" * 129,
]


class SessionPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tanyuan-path-tests-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.output = self.base / "output"
        self.upload = self.base / "upload"
        self.outside = self.base / "outside"
        for directory in (self.output, self.upload, self.outside):
            directory.mkdir()
        for name, value in (("OUTPUT_DIR", self.output), ("UPLOAD_DIR", self.upload)):
            mock = patch.object(paths, name, value)
            mock.start()
            self.addCleanup(mock.stop)
        self.client = TestClient(server.app)
        self.addCleanup(self.client.close)
        self.addCleanup(context.reset_session)

    def snapshot(self):
        return {str(p.relative_to(self.base)): p.read_bytes() if p.is_file() else None
                for p in self.base.rglob("*") if not p.is_symlink()}

    def test_valid_ids_and_context(self):
        for sid in ("abc123", "demo", "direct_e2e", "proxy_fix_test", "a-b", "f" * 32,
                    "123e4567-e89b-12d3-a456-426614174000"):
            with self.subTest(sid=sid):
                self.assertEqual(paths.get_session_dir(sid), self.output / f"session_{sid}")
                self.assertEqual(paths.get_upload_dir(sid), self.upload / f"session_{sid}")
                context.set_session(sid, str(paths.get_session_dir(sid)))
                self.assertEqual(context.get_thread_id(), sid)
                self.assertEqual(context.get_session_dir(), str(paths.get_session_dir(sid)))

    def test_invalid_ids_rejected_before_filesystem_changes(self):
        before = self.snapshot()
        for sid in INVALID_IDS:
            for resolver in (paths.get_session_dir, paths.get_upload_dir):
                with self.subTest(sid=sid, resolver=resolver.__name__):
                    with self.assertRaises(paths.InvalidSessionIDError):
                        resolver(sid).mkdir(parents=True, exist_ok=True)
                    self.assertEqual(self.snapshot(), before)

    def test_invalid_ids_http_4xx_without_writes_or_agent(self):
        before = self.snapshot()
        with patch.object(server, "_run_task", new_callable=AsyncMock) as runner:
            for sid in INVALID_IDS:
                with self.subTest(sid=sid):
                    response = self.client.post("/api/task", json={"query": "test", "thread_id": sid})
                    self.assertEqual(response.status_code, 422)
                    response = self.client.post("/api/upload", data={"thread_id": sid},
                                                files={"files": ("test.txt", b"payload")})
                    self.assertTrue(400 <= response.status_code < 500, response.text)
                    self.assertEqual(self.snapshot(), before)
            runner.assert_not_called()

    def test_legal_task_and_upload_keep_api_contract(self):
        with patch.object(server, "_run_task", new_callable=AsyncMock):
            for sid in ("demo", None):
                response = self.client.post("/api/task", json={"query": "test", "thread_id": sid})
                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertEqual(body["status"], "started")
                self.assertTrue(paths.get_session_dir(body["thread_id"]).is_dir())
        response = self.client.post("/api/upload", data={"thread_id": "demo"},
                                    files=[("files", ("合法文件.txt", b"hello")),
                                           ("files", ("data.csv", b"a,b"))])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "uploaded", "files": ["合法文件.txt", "data.csv"]})
        self.assertEqual((self.upload / "session_demo" / "合法文件.txt").read_bytes(), b"hello")
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_symlink_session_and_root_denied_without_writes(self):
        for root in (self.output, self.upload):
            link = root / "session_link"
            link.symlink_to(self.outside, target_is_directory=True)
            before = self.snapshot()
            for resolver in (paths.get_session_dir, paths.get_upload_dir):
                if root == self.output and resolver == paths.get_upload_dir:
                    continue
                if root == self.upload and resolver == paths.get_session_dir:
                    continue
                with self.assertRaises(paths.PathOutOfBoundsError):
                    resolver("link").mkdir(exist_ok=True)
            with patch.object(server, "_run_task", new_callable=AsyncMock) as runner:
                self.assertEqual(self.client.post("/api/task", json={"query": "test", "thread_id": "link"}).status_code, 403)
                self.assertEqual(self.client.post("/api/upload", data={"thread_id": "link"},
                                                 files={"files": ("test.txt", b"bad")}).status_code, 403)
                runner.assert_not_called()
            self.assertEqual(self.snapshot(), before)
            link.unlink()
        link = self.base / "root_link"
        link.symlink_to(self.outside, target_is_directory=True)
        for name, resolver in (("OUTPUT_DIR", paths.get_session_dir), ("UPLOAD_DIR", paths.get_upload_dir)):
            with patch.object(paths, name, link), self.assertRaises(paths.PathOutOfBoundsError):
                resolver("demo").mkdir()
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_upload_filename_traversal_and_symlink_no_partial_writes(self):
        for filename in ("../outside", "/tmp/outside", "..\\outside", "%2e%2e%2foutside"):
            before = self.snapshot()
            response = self.client.post("/api/upload", data={"thread_id": "demo"},
                                        files=[("files", ("ok.txt", b"ok")), ("files", (filename, b"bad"))])
            self.assertEqual(response.status_code, 403)
            self.assertEqual(self.snapshot(), before)
        folder = paths.get_upload_dir("demo")
        folder.mkdir()
        victim = self.outside / "victim.txt"
        victim.write_bytes(b"original")
        (folder / "link.txt").symlink_to(victim)
        before = self.snapshot()
        response = self.client.post("/api/upload", data={"thread_id": "demo"},
                                    files={"files": ("link.txt", b"overwrite")})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(victim.read_bytes(), b"original")
        self.assertEqual(self.snapshot(), before)

    def test_context_rejects_invalid_id_and_mismatched_directory(self):
        for sid in INVALID_IDS:
            with self.assertRaises(paths.InvalidSessionIDError):
                context.set_session(sid, str(self.outside))
        with self.assertRaises(paths.PathOutOfBoundsError):
            context.set_session("demo", str(self.outside))
        self.assertIsNone(context.get_thread_id())
        folder = paths.get_session_dir("demo")
        context.set_session("demo", str(folder))
        folder.symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(paths.PathOutOfBoundsError):
            context.get_session_dir()

    def test_encoded_http_path_and_websocket_validation(self):
        before = self.snapshot()
        for encoded in ("%2e%2e%2foutside", "x%2f..%2f..%2foutside", "%252e%252e%252foutside"):
            response = self.client.post("/api/upload", content=f"thread_id={encoded}",
                                        headers={"content-type": "application/x-www-form-urlencoded"})
            self.assertTrue(400 <= response.status_code < 500)
        self.assertEqual(self.snapshot(), before)
        for path in ("/ws/%2e%2e%2foutside", "/ws/x%2f..%2f..%2foutside"):
            self.assertTrue(400 <= self.client.get(path).status_code < 500)
        with self.client.websocket_connect("/ws/demo") as ws:
            ws.send_text("ping")
            self.assertEqual(ws.receive_json()["type"], "pong")
        from starlette.websockets import WebSocketDisconnect
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect("/ws/%252e%252e%252foutside"):
                self.fail("非法 ID 不应建立 WebSocket")


if __name__ == "__main__":
    unittest.main()
