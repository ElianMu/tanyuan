"""Offline stage 2 boundaries; never loads credentials or calls paid APIs."""
from __future__ import annotations
import asyncio
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import subprocess
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def guard(event, args):
    if event == "socket.connect":
        raise RuntimeError("offline tests prohibit network")
    if event == "open" and isinstance(args[0], (str, bytes)) and Path(args[0]).name.startswith(".env"):
        raise RuntimeError("offline tests prohibit credential files")


sys.addaudithook(guard)
from agent import llm
from tools import internet_search_tool as web
from tools import knowledge_base_tool as kb
from scripts import build_knowledge_base as build
from scripts import check_stage2 as acceptance
from utils import knowledge_utils as config


class Stage2Tests(unittest.TestCase):
    def test_provider_selection_never_retries_other_provider(self):
        fake_tool = Mock()
        fake_tool.invoke.return_value = "URL: https://example.com"
        with patch.dict(os.environ, {"TAVILY_API_KEY": "fake-offline"}, clear=True), patch.object(llm, "get_llm") as init, patch.object(web, "internet_search", fake_tool), redirect_stdout(io.StringIO()):
            self.assertEqual(acceptance.external_check("tavily"), 0)
            init.assert_not_called()
            fake_tool.invoke.assert_called_once()
        with patch.dict(os.environ, {}, clear=True), patch.object(llm, "get_llm") as init, redirect_stdout(io.StringIO()):
            self.assertEqual(acceptance.external_check("deepseek"), 2)
            init.assert_not_called()

    def test_llm_requires_configuration_without_initializing(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(llm, "init_chat_model") as init:
            with self.assertRaisesRegex(ValueError, "DEEPSEEK_API_KEY"):
                llm.get_llm()
            init.assert_not_called()

    def test_llm_limits_and_secret_not_in_errors(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fake-offline"}, clear=True), patch.object(llm, "init_chat_model") as init:
            llm.get_llm(max_tokens=512, max_retries=0)
            self.assertEqual(init.call_args.kwargs["max_tokens"], 512)
            self.assertEqual(init.call_args.kwargs["timeout"], 60)
            self.assertEqual(init.call_args.kwargs["max_retries"], 0)
            for url in ["http://api.deepseek.com", "https://user:password@example.com", "https://example.com/?key=x"]:
                with patch.dict(os.environ, {"DEEPSEEK_BASE_URL": url}), self.assertRaises(ValueError):
                    llm.get_llm()

    def test_search_configuration_and_input_validation(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(web, "TavilyClient") as client:
            self.assertIn("缺少", web.internet_search.invoke({"query": "question"}))
            self.assertIn("限制", web.internet_search.invoke({"query": "", "max_results": 11}))
            client.assert_not_called()

    def test_search_budget_is_task_scoped_and_enforced(self):
        client = Mock()
        client.search.return_value = {"results": [{"title": "Title", "url": "https://example.com", "content": "Evidence"}]}
        with patch.dict(os.environ, {"TAVILY_API_KEY": "fake-offline"}), patch.object(web, "TavilyClient", return_value=client):
            with web.search_scope() as usage:
                for query in ["angle one", "angle two", "angle three"]:
                    self.assertIn("https://example.com", web.internet_search.invoke({"query": query}))
                self.assertIn("最多三次", web.internet_search.invoke({"query": "angle four"}))
                self.assertEqual(usage.calls, 3)
            with web.search_scope():
                self.assertIn("Evidence", web.internet_search.invoke({"query": "angle one"}))
                self.assertIn("不同检索角度", web.internet_search.invoke({"query": "ANGLE ONE"}))
            self.assertEqual(client.search.call_count, 4)
            self.assertEqual(client.close.call_count, 4)
            self.assertEqual(client.search.call_args.kwargs["timeout"], 20)
            self.assertEqual(client.search.call_args.kwargs["search_depth"], "basic")

    def test_search_sanitizes_exception_and_sources_and_caps_content(self):
        client = Mock()
        with patch.dict(os.environ, {"TAVILY_API_KEY": "fake-offline"}), patch.object(web, "TavilyClient", return_value=client):
            with web.search_scope():
                client.search.side_effect = RuntimeError("private-token-and-host")
                self.assertNotIn("private-token", web.internet_search.invoke({"query": "failure"}))
            client.search.side_effect = None
            client.search.return_value = {"results": [{"url": "javascript:alert(1)"}, {"url": "https://["}, {"url": "https://example.com", "content": "x" * 10000}]}
            with web.search_scope():
                result = web.internet_search.invoke({"query": "bounded"})
                self.assertNotIn("javascript", result)
                self.assertLess(len(result), 6600)
            client.search.return_value = {"results": "malformed"}
            with web.search_scope():
                self.assertIn("格式异常", web.internet_search.invoke({"query": "bad response"}))

    def test_kb_missing_index_does_not_load_model(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"VECTOR_STORE_DIR": tmp}), patch.object(kb, "get_embedding") as embed:
            self.assertIn("尚未构建", asyncio.run(kb.search_knowledge_base.ainvoke({"query": "question"})))
            self.assertIn("限制", asyncio.run(kb.search_knowledge_base.ainvoke({"query": "question", "k": 11})))
            embed.assert_not_called()

    def test_kb_manifest_mismatch_and_valid_search(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"VECTOR_STORE_DIR": tmp}, clear=True):
            manifest = Path(tmp) / config.MANIFEST
            manifest.write_text(json.dumps({"model": "wrong"}))
            with patch.object(kb, "run_worker", return_value="[来源: test.md]\nsource text") as search:
                self.assertIn("不匹配", asyncio.run(kb.search_knowledge_base.ainvoke({"query": "question"})))
                search.assert_not_called()
                manifest.write_text(json.dumps({"model": config.model_name(), "collection": config.COLLECTION_NAME}))
                self.assertIn("source text", asyncio.run(kb.search_knowledge_base.ainvoke({"query": "question"})))

    def test_builder_never_overwrites_existing_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); target = root / "index"; target.mkdir()
            (target / "historical.txt").write_text("keep me")
            with patch.dict(os.environ, {"VECTOR_STORE_DIR": str(target), "KNOWLEDGE_BASE_DIR": str(root / "corpus")}):
                with self.assertRaises(FileExistsError): build.build_index()
            self.assertEqual((target / "historical.txt").read_text(), "keep me")

    def test_loader_preserves_relative_sources_and_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); corpus = root / "corpus"; corpus.mkdir()
            (corpus / "nested").mkdir()
            (corpus / "nested" / "same.txt").write_text("有效语料", encoding="utf-8")
            docs = build.load_documents(corpus)
            self.assertEqual(docs[0].metadata["source"], "nested/same.txt")
            self.assertEqual(len(docs[0].metadata["sha256"]), 64)
            outside = root / "private.txt"; outside.write_text("private")
            (corpus / "escape.txt").symlink_to(outside)
            with self.assertRaises(ValueError): build.load_documents(corpus)

    def test_worker_receives_no_secrets_and_has_clean_working_directory(self):
        def child(*args, **kwargs):
            self.assertFalse((Path(kwargs["cwd"]) / ".env").exists())
            self.assertNotIn("DEEPSEEK_API_KEY", kwargs["env"])
            self.assertNotIn("MYSQL_PASSWORD", kwargs["env"])
            self.assertTrue(Path(kwargs["env"]["VECTOR_STORE_DIR"]).is_absolute())
            self.assertEqual(kwargs["timeout"], 12)
            return subprocess.CompletedProcess(args[0], 0, stdout='"evidence"', stderr="")
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fake", "MYSQL_PASSWORD": "fake"}), patch.object(config.subprocess, "run", side_effect=child):
            self.assertEqual(config.run_worker("search", {"query": "text"}, timeout=12), "evidence")

    def test_worker_timeout_and_malformed_response_fail_closed(self):
        with patch.object(config.subprocess, "run", side_effect=subprocess.TimeoutExpired("worker", 1)):
            with self.assertRaises(subprocess.TimeoutExpired): config.run_worker("search", {}, 1)
        for status, output in [(1, "private error"), (0, "not json")]:
            with patch.object(config.subprocess, "run", return_value=subprocess.CompletedProcess([], status, output, "private")):
                with self.assertRaises(RuntimeError) as error: config.run_worker("search", {})
                self.assertNotIn("private", str(error.exception))

    def test_builder_rejects_nested_corpus_index_without_loading_model(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "KNOWLEDGE_BASE_DIR": tmp, "VECTOR_STORE_DIR": str(Path(tmp) / "nested-index")
        }), patch.object(build, "get_embedding") as embed:
            with self.assertRaises(ValueError): build._build_index()
            embed.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
