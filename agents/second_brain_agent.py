"""
SecondBrain — RAPP Brainstem agent for the RAPP Second Brain.

Drop this file into any RAPP brainstem's `agents/` directory. It gives the twin
durable memory of the real world: who it called, what was agreed, what is still
waiting on you, and what you are owed.

The brain itself is `rsb` (a sibling single-file CLI). This agent shells out to
it, so the brainstem, Claude Code, Copilot CLI and a phone agent all read and
write exactly the same tamper-evident log.

    https://github.com/kody-w/rapp-secondbrain
"""

import json
import os
import shutil
import subprocess

from agents.basic_agent import BasicAgent


def _find_rsb():
    """Locate the rsb binary — PATH first, then the usual install spots."""
    found = shutil.which("rsb")
    if found:
        return found
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rsb"),
        os.path.expanduser("~/.rapp-second-brain/bin/rsb"),
        os.path.expanduser("~/.local/bin/rsb"),
        os.path.expanduser("~/rapp-secondbrain/rsb"),
    ]
    for candidate in candidates:
        resolved = os.path.abspath(candidate)
        if os.path.isfile(resolved):
            return resolved
    return None


class SecondBrainAgent(BasicAgent):
    def __init__(self):
        self.name = "SecondBrain"
        self.metadata = {
            "name": self.name,
            "description": (
                "The owner's second brain: real-world memory and records. Use it to recall who "
                "someone is, what was said on a past phone call, what is scheduled, what needs "
                "the owner's approval, and money owed. Also use it to remember new durable facts, "
                "log a call, propose an appointment, or request the owner's approval before "
                "committing to anything on their behalf."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "brief",
                            "recall",
                            "remember",
                            "contact_find",
                            "contact_add",
                            "appointments",
                            "propose_appointment",
                            "request_approval",
                            "pending_approvals",
                            "calls",
                            "call_show",
                            "leads",
                            "invoices",
                        ],
                        "description": "What to do. 'brief' is the best default when you need situational awareness.",
                    },
                    "query": {"type": "string", "description": "Search text, contact name/phone, or an id, depending on action."},
                    "text": {"type": "string", "description": "The fact to remember, or the detail of an approval request."},
                    "name": {"type": "string", "description": "Contact name (contact_add)."},
                    "phone": {"type": "string", "description": "Phone number in any format (contact_add)."},
                    "title": {"type": "string", "description": "Appointment title, or approval subject."},
                    "start": {"type": "string", "description": "When, e.g. '2026-08-07T19:45', 'friday 7:45pm', 'tomorrow 10am'."},
                    "with_whom": {"type": "string", "description": "Who the appointment is with."},
                },
                "required": ["action"],
            },
        }
        self.rsb = _find_rsb()
        super().__init__(name=self.name, metadata=self.metadata)

    # -- plumbing ---------------------------------------------------------

    def _run(self, *args):
        if not self.rsb:
            return {"ok": False, "error": "rsb not installed — see https://github.com/kody-w/rapp-secondbrain"}
        try:
            result = subprocess.run(
                [self.rsb, "--json", "--actor", "brainstem", *[str(a) for a in args]],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "second brain timed out"}
        except OSError as exc:
            return {"ok": False, "error": f"could not run rsb: {exc}"}

        raw = (result.stdout or "").strip()
        if not raw:
            return {"ok": result.returncode == 0, "error": (result.stderr or "").strip() or None}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"ok": False, "error": raw[:400]}

    # -- persistent context ------------------------------------------------

    def system_context(self):
        """Inject the brain's situational summary into every turn's system prompt."""
        payload = self._run("context")
        context = payload.get("context") if isinstance(payload, dict) else None
        if not context or context.strip() == "<second_brain>\n</second_brain>":
            return None
        return (
            f"{context}\n\n"
            "<second_brain_instructions>\n"
            "- The block above is durable, verified state from the owner's second brain.\n"
            "- Never commit to anything on the owner's behalf that is outside their stated\n"
            "  preferences without first calling SecondBrain with action='request_approval'.\n"
            "- After a phone call, log what was agreed so the next turn can see it.\n"
            "</second_brain_instructions>"
        )

    # -- dispatch ----------------------------------------------------------

    def perform(self, **kwargs):
        action = kwargs.get("action", "brief")

        handlers = {
            "brief": lambda: self._run("brief"),
            "recall": lambda: self._run("recall", kwargs.get("query", "")),
            "remember": lambda: self._run("remember", kwargs.get("text", "")),
            "contact_find": lambda: self._run("contact", "find", kwargs.get("query", "")),
            "appointments": lambda: self._run("appointment", "list"),
            "pending_approvals": lambda: self._run("approval", "list", "--pending"),
            "calls": lambda: self._run("call", "list"),
            "call_show": lambda: self._run("call", "show", kwargs.get("query", "")),
            "leads": lambda: self._run("lead", "list"),
            "invoices": lambda: self._run("invoice", "list"),
        }

        if action in handlers:
            return json.dumps(handlers[action](), indent=2)

        if action == "contact_add":
            args = ["contact", "add", "--name", kwargs.get("name", "Unknown")]
            if kwargs.get("phone"):
                args += ["--phone", kwargs["phone"]]
            return json.dumps(self._run(*args), indent=2)

        if action == "propose_appointment":
            args = ["appointment", "propose", "--title", kwargs.get("title", "Appointment")]
            if kwargs.get("start"):
                args += ["--start", kwargs["start"]]
            if kwargs.get("with_whom"):
                args += ["--with", kwargs["with_whom"]]
            return json.dumps(self._run(*args), indent=2)

        if action == "request_approval":
            args = ["approval", "request", "--subject", kwargs.get("title") or kwargs.get("text", "Approval needed")]
            if kwargs.get("text"):
                args += ["--detail", kwargs["text"]]
            return json.dumps(self._run(*args), indent=2)

        return json.dumps({"ok": False, "error": f"unknown action {action!r}"}, indent=2)
