"""Stage 3 prompt contracts and least-privilege tool declarations, offline."""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent.load_prompt import load_prompts
from agent.sub_agents import SUBAGENTS


class PromptTests(unittest.TestCase):
    def load_text(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prompts.yaml"; path.write_text(text)
            return load_prompts(path)

    def test_real_configuration_and_answer_rules(self):
        config = load_prompts()
        self.assertEqual(len(config["sub_agents"]), 3)
        prompt = config["main_agent"]["system_prompt"]
        for rule in ["普通问答直接返回答案", "不可信数据", "禁止编造", "1000", "不额外生成 PDF"]:
            self.assertIn(rule, prompt)

    def test_duplicate_fields_rejected(self):
        with self.assertRaises(ValueError): self.load_text("main_agent: {}\nmain_agent: {}\nsub_agents: {}")

    def test_missing_or_unknown_assistant_rejected(self):
        for key in ["knowledge", "db_query", "web_search"]:
            config = load_prompts(); del config["sub_agents"][key]
            with self.assertRaises(ValueError): self.load_text(yaml.safe_dump(config))

    def test_nontext_empty_and_duplicate_names_rejected(self):
        for value in ["", None, ["invalid"], 1]:
            config = load_prompts(); config["sub_agents"]["web_search"]["system_prompt"] = value
            with self.assertRaises(ValueError): self.load_text(yaml.safe_dump(config))
        config = load_prompts(); config["sub_agents"]["knowledge"]["name"] = config["sub_agents"]["db_query"]["name"]
        with self.assertRaises(ValueError): self.load_text(yaml.safe_dump(config))

    def test_unsafe_yaml_and_oversized_configuration_rejected(self):
        with self.assertRaises(yaml.YAMLError): self.load_text("!!python/object/apply:os.system ['false']")
        with self.assertRaises(ValueError): self.load_text("x" * (128 * 1024 + 1))

    def test_subagent_tool_permissions_are_exact(self):
        expected = [{"internet_search"}, {"list_sql_tables", "get_table_data", "execute_sql_query"}, {"search_knowledge_base"}]
        for agent, names in zip(SUBAGENTS, expected):
            actual = {getattr(tool, "name", None) or tool.__name__ for tool in agent["tools"]}
            self.assertEqual(actual, names)
            self.assertIn("不创建文件", agent["system_prompt"])


if __name__ == "__main__": unittest.main(verbosity=2)
