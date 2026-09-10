"""Dependency-light checks of the actual evidence functions.

Run directly with Python when the application test environment is unavailable.
These isolate the evidence rules, not the full graph or MCP transport.
"""
from __future__ import annotations

import ast
import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).resolve().parents[2]


def functions(path, names, namespace):
    tree = ast.parse(path.read_text())
    selected = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    assert len(selected) == len(names)
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *selected], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace


class EvidenceRegression(unittest.TestCase):
    def setUp(self):
        self.ns = functions(ROOT / "agent-core/app/graph/nodes/plan.py", {
            "_todo_intent", "_path_ext", "_is_image_path", "_turn_tool_names", "_written_paths",
            "_proof_is_relevant_to_todo", "_record_todo_evidence", "_todo_paths",
        }, {"Path": Path, "_IMAGE_EXTS": {".png", ".jpg"}})
        self.todo = {"text": "运行词云脚本并验证生成的词云图片"}

    def proof(self, tool="shell", valid=(), written=()):
        return SimpleNamespace(successful_tool_calls=1, tool_names=[tool],
                               written_paths=set(written), validated_image_paths=set(valid))

    def test_both_generation_routes_accept_decoded_images(self):
        for tool in ("shell", "wan_text2image", "filesystem"):
            with self.subTest(tool=tool):
                self.assertTrue(self.ns["_proof_is_relevant_to_todo"](self.todo, self.proof(tool, ["cloud.png"])))

    def test_filename_alone_is_not_evidence(self):
        for tool in ("shell", "wan_text2image"):
            self.assertFalse(self.ns["_proof_is_relevant_to_todo"](self.todo, self.proof(tool, written=["cloud.png"])))

    def test_failed_tool_is_not_evidence(self):
        proof = self.proof(valid=["cloud.png"])
        proof.successful_tool_calls = 0
        self.assertFalse(self.ns["_proof_is_relevant_to_todo"](self.todo, proof))

    def test_only_decoded_paths_are_persisted(self):
        self.ns["_record_todo_evidence"](self.todo, self.proof(valid=["good.png"], written=["bad.png"]))
        self.assertEqual(self.todo["evidence_paths"], ["good.png"])
        self.assertEqual(self.todo["validated_image_paths"], ["good.png"])

    def test_separate_calls_accumulate(self):
        for path in ("jobs.png", "gates.png"):
            self.ns["_record_todo_evidence"](self.todo, self.proof("filesystem", [path]))
        self.assertEqual(set(self.todo["validated_image_paths"]), {"jobs.png", "gates.png"})

    def test_repeated_calls_do_not_change_progress_key(self):
        ns = functions(ROOT / "agent-core/app/graph/nodes/execute.py", {"_plan_progress_key"}, {"json": json})
        plan = {"todos": [self.todo]}
        self.ns["_record_todo_evidence"](self.todo, self.proof(valid=["cloud.png"]))
        before = ns["_plan_progress_key"](plan)
        self.ns["_record_todo_evidence"](self.todo, self.proof(valid=["cloud.png"]))
        self.assertEqual(before, ns["_plan_progress_key"](plan))

    def test_probe_rejects_failed_decode(self):
        ns = functions(ROOT / "agent-core/app/graph/nodes/execute.py", {"_validate_image_paths"},
                       {"Path": Path, "_stat_rel_path": lambda p: p, "log": Mock()})
        mcp = SimpleNamespace(invoke=AsyncMock(return_value=SimpleNamespace(ok=False, output={}, error="corrupt")))
        self.assertEqual(asyncio.run(ns["_validate_image_paths"](mcp=mcp, sandbox_id="s", paths={"bad.png"})), set())
        self.assertEqual(mcp.invoke.call_args.kwargs["args"]["action"], "inspect_image")

    def test_probe_accepts_explicit_validation_only(self):
        ns = functions(ROOT / "agent-core/app/graph/nodes/execute.py", {"_validate_image_paths"},
                       {"Path": Path, "_stat_rel_path": lambda p: p, "log": Mock()})
        for body, expected in (({}, set()), ({"image_valid": True}, {"good.png"})):
            mcp = SimpleNamespace(invoke=AsyncMock(return_value=SimpleNamespace(ok=True, output=body, error=None)))
            self.assertEqual(asyncio.run(ns["_validate_image_paths"](mcp=mcp, sandbox_id="s", paths={"good.png"})), expected)

    def test_specific_budget_message(self):
        ns = functions(ROOT / "agent-core/app/graph/execution_budget.py", {"budget_reason"}, {})
        reason = ns["budget_reason"](tool_turns=40, total_turns=50, tokens=100,
                                     max_tools=40, max_turns=80, max_tokens=500000)
        self.assertEqual(reason, "已达到工具执行轮数上限（40/40）")


if __name__ == "__main__":
    unittest.main()
