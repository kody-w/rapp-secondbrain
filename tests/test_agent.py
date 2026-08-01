#!/usr/bin/env python3
"""
Tests for the RAPP Brainstem agent.

Two things are proven here:

  1. The agent satisfies the grail agent ABI — one class extending BasicAgent,
     a metadata dict that becomes a tool definition, perform(**kwargs) -> str,
     and all I/O through the storage shim (so the same file runs on Tier 1,
     Tier 2, Tier 3 and Pyodide unmodified).

  2. The log it writes is byte-compatible with the `rsb` CLI. That is the whole
     compatibility claim: a browser sphere, a phone agent and a terminal are
     reading and writing ONE brain, not three that look similar.

Run:  python3 tests/test_agent.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RSB = ROOT / "rsb"
AGENT_FILE = ROOT / "agents" / "second_brain_agent.py"


# ---------------------------------------------------------------------------
# The brainstem's module interception, reproduced.
#
# brainstem.py injects `agents.basic_agent` and `utils.azure_file_storage` into
# sys.modules before importing an agent file. We do exactly the same, against
# the documented ABI, so the agent under test is imported the way the kernel
# imports it — no modifications, no shims of our own inside the agent.
# ---------------------------------------------------------------------------


class BasicAgent:
    """Mirror of the grail agent ABI (rapp_brainstem/agents/basic_agent.py)."""

    def __init__(self, name=None, metadata=None):
        if name is not None:
            self.name = name
        elif not hasattr(self, "name"):
            self.name = "BasicAgent"
        if metadata is not None:
            self.metadata = metadata
        elif not hasattr(self, "metadata"):
            self.metadata = {
                "name": self.name,
                "description": "Base agent -- override this.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            }

    def perform(self, **kwargs):
        return "Not implemented."

    def system_context(self):
        return None

    def to_tool(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.metadata.get("description", ""),
                "parameters": self.metadata.get("parameters", {"type": "object", "properties": {}}),
            },
        }


def make_storage_module(data_dir: Path):
    """Mirror of rapp_brainstem/local_storage.py, rooted at a temp dir."""

    class AzureFileStorageManager:
        DEFAULT_MARKER_GUID = "c0p110t0-aaaa-bbbb-cccc-123456789abc"

        def __init__(self, share_name=None, **kwargs):
            self.current_guid = None
            self.shared_memory_path = "shared_memories"
            self.default_file_name = "memory.json"
            self.current_memory_path = self.shared_memory_path
            os.makedirs(data_dir, exist_ok=True)

        def set_memory_context(self, user_guid=None):
            if not user_guid or user_guid == self.DEFAULT_MARKER_GUID:
                self.current_guid = None
                self.current_memory_path = self.shared_memory_path
                return True
            self.current_guid = user_guid
            self.current_memory_path = f"memory/{user_guid}"
            return True

        def read_file(self, file_path):
            full = data_dir / file_path
            return full.read_text() if full.exists() else None

        def write_file(self, file_path, content):
            full = data_dir / file_path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content)
            return True

        def list_files(self, directory=""):
            full = data_dir / directory
            return os.listdir(full) if full.exists() else []

        def delete_file(self, file_path):
            full = data_dir / file_path
            if full.exists():
                full.unlink()
                return True
            return False

        def file_exists(self, file_path):
            return (data_dir / file_path).exists()

        def read_json(self, file_path=None):
            raw = self.read_file(file_path or "shared_memories/memory.json")
            return json.loads(raw) if raw else {}

        def write_json(self, data, file_path=None):
            return self.write_file(file_path or "shared_memories/memory.json", json.dumps(data, indent=2))

    module = types.ModuleType("utils.azure_file_storage")
    module.AzureFileStorageManager = AzureFileStorageManager
    return module


def load_agent(data_dir: Path):
    """Import the agent exactly as the brainstem does."""
    import importlib.util

    agents_pkg = types.ModuleType("agents")
    agents_pkg.__path__ = []
    basic = types.ModuleType("agents.basic_agent")
    basic.BasicAgent = BasicAgent

    utils_pkg = types.ModuleType("utils")
    utils_pkg.__path__ = []
    storage = make_storage_module(data_dir)

    for name, module in [
        ("agents", agents_pkg),
        ("agents.basic_agent", basic),
        ("utils", utils_pkg),
        ("utils.azure_file_storage", storage),
    ]:
        sys.modules[name] = module

    spec = importlib.util.spec_from_file_location("second_brain_agent", AGENT_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SecondBrainAgent()


class AgentTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.data_dir = Path(tempfile.mkdtemp(prefix="rsb-agent-"))
        self.addCleanup(shutil.rmtree, self.data_dir, ignore_errors=True)
        self.agent = load_agent(self.data_dir)

    def act(self, **kwargs) -> dict:
        raw = self.agent.perform(**kwargs)
        self.assertIsInstance(raw, str, "perform() must return a string — the grail ABI requires it")
        return json.loads(raw)


# ---------------------------------------------------------------------------
# ABI conformance
# ---------------------------------------------------------------------------


class TestGrailAbi(AgentTestCase):
    def test_extends_basic_agent(self) -> None:
        self.assertIsInstance(self.agent, BasicAgent)

    def test_metadata_becomes_a_tool_definition(self) -> None:
        tool = self.agent.to_tool()
        self.assertEqual(tool["type"], "function")
        self.assertEqual(tool["function"]["name"], "SecondBrain")
        self.assertTrue(tool["function"]["description"])
        params = tool["function"]["parameters"]
        self.assertEqual(params["type"], "object")
        self.assertIn("action", params["properties"])
        self.assertEqual(params["required"], ["action"])

    def test_every_advertised_action_is_implemented(self) -> None:
        """No action may appear in the enum that perform() does not handle."""
        actions = self.agent.metadata["parameters"]["properties"]["action"]["enum"]
        for action in actions:
            result = self.act(action=action, query="x", name="x", text="x", key="k", value="v")
            self.assertNotIn(
                "unknown action",
                json.dumps(result),
                f"action {action!r} is advertised but not handled",
            )

    def test_unknown_action_fails_cleanly(self) -> None:
        result = self.act(action="definitely_not_a_thing")
        self.assertFalse(result["ok"])

    def test_perform_never_raises(self) -> None:
        """The kernel calls perform() directly; an exception would break the turn."""
        for kwargs in [{}, {"action": None}, {"action": "recall"}, {"action": "call_turn", "query": "nope"}]:
            raw = self.agent.perform(**kwargs)
            self.assertIsInstance(raw, str)
            json.loads(raw)

    def test_system_context_hook(self) -> None:
        self.assertIsNone(self.agent.system_context(), "an empty brain must not pollute the prompt")
        self.act(action="remember", text="Kody prefers evening appointments")
        self.act(action="set_preference", key="tone", value="concise")
        context = self.agent.system_context()
        self.assertIn("<second_brain>", context)
        self.assertIn("Kody prefers evening appointments", context)
        self.assertIn("tone: concise", context)
        self.assertIn("A pending approval is not a yes", context)

    def test_tier_portability(self) -> None:
        """
        The same file must run on Tier 1, Tier 2, Tier 3 and Pyodide (Article VII).

        That rules out subprocesses, sockets, HTTP clients and direct filesystem
        access — all I/O has to go through the storage shim. Checked against the
        parsed AST rather than the raw text, so prose in a docstring can't pass
        or fail it.
        """
        import ast

        tree = ast.parse(AGENT_FILE.read_text())

        allowed_modules = {
            "datetime",
            "hashlib",
            "json",
            "uuid",
            "agents.basic_agent",
            "utils.azure_file_storage",
        }

        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        forbidden = imported - allowed_modules
        self.assertEqual(
            forbidden,
            set(),
            f"these imports break tier portability: {sorted(forbidden)}. "
            "A Pyodide sphere and an Azure Function have no subprocesses, sockets or local paths.",
        )

        # No direct filesystem or process access, whatever it is imported as.
        banned_calls = {"open", "exec", "eval", "compile", "__import__"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in banned_calls:
                    self.fail(f"{node.func.id}() breaks tier portability — use the storage shim")
                if isinstance(node.func, ast.Attribute):
                    root = node.func
                    while isinstance(root, ast.Attribute):
                        root = root.value
                    if isinstance(root, ast.Name) and root.id in {"os", "sys", "subprocess", "socket", "urllib"}:
                        self.fail(f"{root.id}.* breaks tier portability — use the storage shim")

    def test_is_a_single_self_contained_file(self) -> None:
        """The grail contract is one agent, one file, discovered by glob."""
        self.assertTrue(AGENT_FILE.name.endswith("_agent.py"), "must match agents/*_agent.py")
        classes = [
            node
            for node in __import__("ast").parse(AGENT_FILE.read_text()).body
            if isinstance(node, __import__("ast").ClassDef)
        ]
        self.assertEqual(len(classes), 1, "exactly one agent class per file")
        self.assertEqual(classes[0].name, "SecondBrainAgent")

    def test_all_writes_go_through_the_shim(self) -> None:
        self.act(action="remember", text="written through the shim")
        self.assertTrue((self.data_dir / "second_brain" / "events.jsonl").exists())

    def test_data_slush_is_emitted_for_chained_agents(self) -> None:
        result = self.act(action="call_start", phone="5551234567", objective="test")
        self.assertIn("data_slush", result)
        self.assertEqual(result["data_slush"]["call_id"], result["call"]["id"])


# ---------------------------------------------------------------------------
# Behaviour
# ---------------------------------------------------------------------------


class TestAgentBehaviour(AgentTestCase):
    def test_chain_is_verifiable(self) -> None:
        self.act(action="remember", text="one")
        self.act(action="contact_add", name="Ada", phone="5551110000")
        result = self.act(action="verify")
        self.assertTrue(result["ok"], result.get("problems"))
        self.assertGreaterEqual(result["events"], 3)

    def test_detects_tampering(self) -> None:
        self.act(action="remember", text="original")
        log = self.data_dir / "second_brain" / "events.jsonl"
        lines = log.read_text().splitlines()
        event = json.loads(lines[-1])
        event["payload"]["text"] = "forged"
        lines[-1] = json.dumps(event, sort_keys=True, separators=(",", ":"))
        log.write_text("\n".join(lines) + "\n")

        result = self.act(action="verify")
        self.assertFalse(result["ok"])
        self.assertTrue(any("hash mismatch" in p for p in result["problems"]))

    def test_contacts_dedupe_on_phone(self) -> None:
        self.act(action="contact_add", name="Bella Vista", phone="(555) 123-4567")
        self.act(action="contact_add", name="Bella Vista", phone="555-123-4567")
        self.assertEqual(len(self.act(action="contacts")["contacts"]), 1)

    def test_approval_gate_blocks_confirmation(self) -> None:
        """The behaviour the whole design exists for."""
        appointment = self.act(
            action="propose_appointment", title="Dinner", with_whom="Bella Vista", start="2026-08-07T19:45"
        )["appointment"]

        approval = self.act(
            action="request_approval", title="They offered 7:45 instead of 7:00. Take it?", query=appointment["id"]
        )["approval"]

        blocked = self.act(action="confirm_appointment", query=appointment["id"])
        self.assertFalse(blocked["ok"], "an unapproved appointment must not be confirmable")
        self.assertIn("approval", blocked["error"])

        self.act(action="decide_approval", query=approval["id"], decision="approve")

        allowed = self.act(action="confirm_appointment", query=appointment["id"])
        self.assertTrue(allowed["ok"])
        self.assertEqual(allowed["status"], "confirmed")

    def test_denied_approval_keeps_the_gate_shut(self) -> None:
        appointment = self.act(action="propose_appointment", title="Risky", start="2026-08-07T23:00")["appointment"]
        approval = self.act(action="request_approval", title="Book it?", query=appointment["id"])["approval"]
        self.act(action="decide_approval", query=approval["id"], decision="deny")
        self.assertFalse(self.act(action="confirm_appointment", query=appointment["id"])["ok"])

    def test_cannot_decide_an_approval_twice(self) -> None:
        approval = self.act(action="request_approval", title="Once")["approval"]
        self.act(action="decide_approval", query=approval["id"], decision="approve")
        self.assertFalse(self.act(action="decide_approval", query=approval["id"], decision="deny")["ok"])

    def test_full_call_is_recoverable(self) -> None:
        self.act(action="contact_add", name="Bella Vista", phone="5551234567")
        call = self.act(action="call_start", phone="5551234567", objective="Book a table for 2")["call"]
        self.assertEqual(call["contact_name"], "Bella Vista")

        self.act(action="call_turn", query=call["id"], role="agent", text="Table for two at seven?")
        self.act(action="call_turn", query=call["id"], role="peer", text="Seven is booked, I could do 7:45.")
        self.act(action="call_end", query=call["id"], outcome="counter_offer", text="7:45 offered")

        shown = self.act(action="call_show", query=call["id"])["call"]
        self.assertEqual(len(shown["turns"]), 2)
        self.assertEqual(shown["outcome"], "counter_offer")

        found = self.act(action="recall", query="7:45")
        self.assertGreaterEqual(found["count"], 1)

    def test_brief_surfaces_what_matters(self) -> None:
        self.act(action="request_approval", title="Needs a decision")
        brief = self.act(action="brief")["brief"]
        self.assertEqual(len(brief["pending_approvals"]), 1)


# ---------------------------------------------------------------------------
# The compatibility claim
# ---------------------------------------------------------------------------


@unittest.skipUnless(RSB.exists(), "rsb not present")
class TestOneBrainTwoImplementations(AgentTestCase):
    """
    The brainstem agent and the rsb CLI must be interchangeable readers and
    writers of the same log. If this suite passes, "compatible with the RAPP
    brainstem" is a demonstrated fact rather than a claim.
    """

    def brain_home(self) -> Path:
        return self.data_dir / "second_brain"

    def rsb(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(RSB), "--home", str(self.brain_home()), "--json", *args],
            capture_output=True,
            text=True,
        )

    def test_rsb_can_verify_a_log_the_agent_wrote(self) -> None:
        self.act(action="remember", text="written by the brainstem agent")
        self.act(action="contact_add", name="Bella Vista", phone="5551234567")

        result = self.rsb("verify")
        self.assertEqual(result.returncode, 0, f"rsb rejected the agent's log: {result.stdout}{result.stderr}")
        self.assertTrue(json.loads(result.stdout)["ok"])

    def test_rsb_reads_what_the_agent_wrote(self) -> None:
        self.act(action="contact_add", name="Mike's Garage", phone="5559998888")
        self.act(action="remember", text="Mike closes at five")

        found = json.loads(self.rsb("contact", "find", "mike").stdout)
        self.assertTrue(found["ok"])
        self.assertEqual(found["contact"]["name"], "Mike's Garage")

        recalled = json.loads(self.rsb("recall", "closes at five").stdout)
        self.assertEqual(recalled["count"], 1)

    def test_agent_reads_what_rsb_wrote(self) -> None:
        self.rsb("init", "--owner", "Kody")
        self.rsb("contact", "add", "--name", "Riverside Cafe", "--phone", "5552223333")
        self.rsb("remember", "Riverside pays by bank transfer")

        found = self.act(action="contact_find", query="riverside")
        self.assertTrue(found["ok"])
        self.assertEqual(found["contact"]["name"], "Riverside Cafe")

        recalled = self.act(action="recall", query="bank transfer")
        self.assertGreaterEqual(recalled["count"], 1)

    def test_interleaved_writes_keep_one_unbroken_chain(self) -> None:
        """The real test: both implementations writing to the same log, in turn."""
        self.act(action="remember", text="agent first")
        self.rsb("remember", "then the cli")
        self.act(action="remember", text="agent again")
        self.rsb("remember", "and the cli again")

        cli_verify = self.rsb("verify")
        self.assertEqual(cli_verify.returncode, 0, cli_verify.stdout)

        agent_verify = self.act(action="verify")
        self.assertTrue(agent_verify["ok"], agent_verify.get("problems"))
        self.assertEqual(agent_verify["events"], json.loads(cli_verify.stdout)["events"])

    def test_the_approval_gate_agrees_across_both(self) -> None:
        """An approval granted on the phone must unlock the booking in the sphere."""
        appointment = self.act(
            action="propose_appointment", title="Brake inspection", with_whom="Mike's Garage", start="2026-08-07T09:00"
        )["appointment"]
        approval = self.act(action="request_approval", title="Friday 9am instead of Thursday?", query=appointment["id"])[
            "approval"
        ]

        # the CLI (say, a phone callback) records the owner's yes
        granted = self.rsb("approval", "approve", approval["id"], "--via", "phone")
        self.assertEqual(granted.returncode, 0, granted.stdout + granted.stderr)

        # the brainstem agent sees it and lets the booking through
        confirmed = self.act(action="confirm_appointment", query=appointment["id"])
        self.assertTrue(confirmed["ok"], confirmed.get("error"))

        # and the CLI agrees the appointment is now real
        listed = json.loads(self.rsb("appointment", "list", "--status", "confirmed").stdout)
        self.assertEqual(listed["count"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
