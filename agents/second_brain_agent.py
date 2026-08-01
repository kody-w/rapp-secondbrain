"""
SecondBrain — RAPP Brainstem agent implementing rapp-second-brain/1.0.

Drop this single file into any brainstem's `agents/` directory.

It follows the grail agent ABI exactly — one file, one class extending
`BasicAgent`, one `metadata` dict, one `perform(**kwargs) -> str` — and it does
all of its I/O through the storage shim, so the same file runs unmodified on
every tier: local brainstem, Azure Functions swarm, Copilot Studio, and the
Pyodide sphere in a browser. No subprocess, no filesystem paths, no network.

The log it writes is byte-compatible with the `rsb` CLI in this repo: both are
implementations of the same spec, over the same hash-chained event log, so a
phone agent, a browser sphere and a terminal all read one truth.

    https://github.com/kody-w/rapp-secondbrain
"""

import datetime
import hashlib
import json
import uuid

from agents.basic_agent import BasicAgent
from utils.azure_file_storage import AzureFileStorageManager

SPEC = "rapp-second-brain/1.0"
LOG_PATH = "second_brain/events.jsonl"
GENESIS = "sha256:" + "0" * 64


def _now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _new_id(prefix):
    return "%s_%s" % (prefix, uuid.uuid4().hex[:12])


def _normalize_phone(raw):
    """E.164-ish, matching the spec so one business is one contact."""
    if not raw:
        return None
    text = str(raw).strip()
    if text.lower().startswith("sim:"):
        return text
    plus = text.startswith("+")
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    if plus:
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return "+" + digits


class SecondBrainAgent(BasicAgent):
    def __init__(self):
        self.name = "SecondBrain"
        self.metadata = {
            "name": self.name,
            "description": (
                "The owner's second brain: durable memory of the real world. Recall who someone "
                "is, what was said on a past phone call, what is scheduled, and what is waiting "
                "on the owner's approval. Also use it to remember a new fact, log a call, propose "
                "an appointment, or request approval BEFORE committing the owner to anything."
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
                            "contact_add",
                            "contact_find",
                            "contacts",
                            "call_start",
                            "call_turn",
                            "call_end",
                            "call_show",
                            "propose_appointment",
                            "confirm_appointment",
                            "appointments",
                            "request_approval",
                            "decide_approval",
                            "pending_approvals",
                            "set_preference",
                            "verify",
                        ],
                        "description": "What to do. Use 'brief' when you need situational awareness.",
                    },
                    "query": {"type": "string", "description": "Search text, a contact name/phone, or an id."},
                    "text": {"type": "string", "description": "Fact to remember, what was said, or approval detail."},
                    "name": {"type": "string", "description": "Contact name."},
                    "phone": {"type": "string", "description": "Phone number, any format."},
                    "title": {"type": "string", "description": "Appointment title or approval subject."},
                    "start": {"type": "string", "description": "ISO datetime, e.g. 2026-08-07T19:45."},
                    "with_whom": {"type": "string", "description": "Who an appointment is with."},
                    "role": {
                        "type": "string",
                        "enum": ["agent", "peer", "owner", "system"],
                        "description": "Who spoke, for call_turn.",
                    },
                    "objective": {"type": "string", "description": "What a call is trying to achieve."},
                    "outcome": {"type": "string", "description": "How a call ended."},
                    "success": {"type": "boolean", "description": "Whether a call met its objective."},
                    "decision": {
                        "type": "string",
                        "enum": ["approve", "deny"],
                        "description": "The owner's answer, for decide_approval.",
                    },
                    "key": {"type": "string", "description": "Preference key."},
                    "value": {"type": "string", "description": "Preference value."},
                    "user_guid": {"type": "string", "description": "Optional per-user memory scope."},
                },
                "required": ["action"],
            },
        }
        self.storage_manager = AzureFileStorageManager()
        super().__init__(name=self.name, metadata=self.metadata)

    # ── log ───────────────────────────────────────────────────────────────

    def _read_events(self):
        try:
            raw = self.storage_manager.read_file(LOG_PATH)
        except Exception:
            return []
        if not raw:
            return []
        events = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
        return events

    def _append(self, event_type, payload, actor="brainstem"):
        events = self._read_events()
        prev = events[-1]["hash"] if events else GENESIS
        seq = events[-1]["seq"] + 1 if events else 1

        body = {
            "seq": seq,
            "id": _new_id("ev"),
            "ts": _now(),
            "type": event_type,
            "actor": actor,
            "payload": payload,
            "prev": prev,
        }
        body["hash"] = _sha(_canon(body))

        existing = ""
        try:
            existing = self.storage_manager.read_file(LOG_PATH) or ""
        except Exception:
            existing = ""
        if existing and not existing.endswith("\n"):
            existing += "\n"
        self.storage_manager.write_file(LOG_PATH, existing + _canon(body) + "\n")
        return body

    def _verify(self):
        problems = []
        prev = GENESIS
        expected = 1
        for event in self._read_events():
            if event.get("prev") != prev:
                problems.append("seq %s: broken chain" % event.get("seq"))
            if event.get("seq") != expected:
                problems.append("expected seq %s, found %s" % (expected, event.get("seq")))
            body = dict((k, v) for k, v in event.items() if k != "hash")
            if _sha(_canon(body)) != event.get("hash"):
                problems.append("seq %s: hash mismatch (event was edited)" % event.get("seq"))
            prev = event.get("hash", prev)
            expected = (event.get("seq") or expected) + 1
        return problems

    # ── projection (a pure fold, identical to the spec) ───────────────────

    def _state(self):
        state = {
            "owner": None,
            "contacts": {},
            "calls": {},
            "appointments": {},
            "approvals": {},
            "leads": {},
            "notes": [],
            "prefs": {},
            "total": 0,
        }

        for event in self._read_events():
            kind = event.get("type", "")
            data = event.get("payload") or {}
            state["total"] += 1

            if kind == "brain.init":
                state["owner"] = data.get("owner")

            elif kind == "contact.upsert":
                merged = dict(state["contacts"].get(data["id"], {}))
                merged.update(dict((k, v) for k, v in data.items() if v is not None))
                merged["updated_at"] = event["ts"]
                state["contacts"][data["id"]] = merged

            elif kind == "note.add":
                note = dict(data)
                note["ts"] = event["ts"]
                state["notes"].append(note)

            elif kind == "pref.set":
                state["prefs"][data["key"]] = data["value"]

            elif kind == "call.start":
                call = dict(data)
                call["status"] = "in_progress"
                call["turns"] = []
                call["started_at"] = event["ts"]
                state["calls"][data["id"]] = call

            elif kind == "call.turn":
                call = state["calls"].get(data.get("call_id"))
                if call is not None:
                    call["turns"].append({"role": data["role"], "text": data["text"], "ts": event["ts"]})

            elif kind == "call.end":
                call = state["calls"].get(data.get("call_id"))
                if call is not None:
                    call["status"] = "ended"
                    call["outcome"] = data.get("outcome", "unknown")
                    call["success"] = bool(data.get("success"))
                    call["summary"] = data.get("summary", "")

            elif kind == "appointment.propose":
                appointment = dict(data)
                appointment["status"] = "proposed"
                state["appointments"][data["id"]] = appointment

            elif kind in ("appointment.confirm", "appointment.cancel"):
                appointment = state["appointments"].get(data.get("id"))
                if appointment is not None:
                    appointment["status"] = "confirmed" if kind.endswith("confirm") else "cancelled"
                    for key in ("start", "end", "location", "external_id", "reason"):
                        if data.get(key) is not None:
                            appointment[key] = data[key]

            elif kind == "approval.request":
                approval = dict(data)
                approval["status"] = "pending"
                approval["created_at"] = event["ts"]
                state["approvals"][data["id"]] = approval

            elif kind == "approval.decide":
                approval = state["approvals"].get(data.get("id"))
                if approval is not None:
                    approval["status"] = data["decision"]
                    approval["decided_via"] = data.get("via", "brainstem")

            elif kind == "lead.add":
                state["leads"][data["id"]] = dict(data)

        return state

    # ── helpers ───────────────────────────────────────────────────────────

    def _find_contact(self, state, needle):
        if not needle:
            return None
        contacts = state["contacts"]
        if needle in contacts:
            return contacts[needle]

        phone = _normalize_phone(needle)
        if phone:
            for contact in contacts.values():
                if contact.get("phone") == phone:
                    return contact

        low = str(needle).lower()
        for contact in contacts.values():
            if (contact.get("name") or "").lower() == low:
                return contact
        for contact in contacts.values():
            if low in (contact.get("name") or "").lower():
                return contact
        return None

    def _resolve(self, bucket, key):
        if not key:
            return None
        if key in bucket:
            return bucket[key]
        hits = [v for k, v in bucket.items() if k.endswith(key) or k.startswith(key)]
        return hits[0] if len(hits) == 1 else None

    def _brief(self, state):
        today = datetime.date.today().isoformat()
        upcoming = sorted(
            [
                a
                for a in state["appointments"].values()
                if a.get("status") == "confirmed" and (a.get("start") or "") >= today
            ],
            key=lambda a: a.get("start") or "",
        )
        pending = [a for a in state["approvals"].values() if a.get("status") == "pending"]
        recent_calls = sorted(state["calls"].values(), key=lambda c: c.get("started_at", ""), reverse=True)[:5]

        return {
            "owner": state.get("owner"),
            "generated_at": _now(),
            "upcoming_appointments": upcoming[:10],
            "pending_approvals": pending,
            "recent_calls": [
                {
                    "id": c.get("id"),
                    "peer": c.get("contact_name") or c.get("peer"),
                    "outcome": c.get("outcome"),
                    "objective": c.get("objective"),
                }
                for c in recent_calls
            ],
            "recent_notes": state["notes"][-8:],
            "preferences": state["prefs"],
            "totals": {
                "contacts": len(state["contacts"]),
                "calls": len(state["calls"]),
                "appointments": len(state["appointments"]),
                "events": state["total"],
            },
        }

    # ── per-turn context injection (grail hook) ───────────────────────────

    def system_context(self):
        try:
            state = self._state()
        except Exception:
            return None
        if not state["total"]:
            return None

        brief = self._brief(state)
        lines = ["<second_brain>"]
        if brief["owner"]:
            lines.append("owner: %s" % brief["owner"])
        if brief["preferences"]:
            lines.append("preferences:")
            for key in sorted(brief["preferences"]):
                lines.append("  - %s: %s" % (key, brief["preferences"][key]))
        if brief["upcoming_appointments"]:
            lines.append("upcoming:")
            for appointment in brief["upcoming_appointments"][:5]:
                lines.append(
                    "  - %s — %s with %s"
                    % (appointment.get("start"), appointment.get("title"), appointment.get("with"))
                )
        if brief["pending_approvals"]:
            lines.append("awaiting the owner's approval:")
            for approval in brief["pending_approvals"]:
                lines.append("  - [%s] %s" % (approval.get("id"), approval.get("subject")))
        if brief["recent_notes"]:
            lines.append("remembered:")
            for note in brief["recent_notes"]:
                lines.append("  - %s" % note.get("text"))
        lines.append("</second_brain>")

        if len(lines) <= 2:
            return None

        return "\n".join(lines) + (
            "\n\n<second_brain_instructions>\n"
            "- The block above is durable, hash-chained state, not conversation history.\n"
            "- Never commit the owner to anything outside their stated preferences without\n"
            "  first calling SecondBrain with action='request_approval'.\n"
            "- A pending approval is not a yes.\n"
            "- After a phone call, log what was agreed with action='call_end'.\n"
            "</second_brain_instructions>"
        )

    # ── dispatch ──────────────────────────────────────────────────────────

    def perform(self, **kwargs):
        action = kwargs.get("action", "brief")

        try:
            self.storage_manager.set_memory_context(kwargs.get("user_guid"))
        except Exception:
            pass

        try:
            return self._dispatch(action, kwargs)
        except Exception as exc:
            return json.dumps({"ok": False, "action": action, "error": str(exc)}, indent=2)

    def _dispatch(self, action, kwargs):
        state = self._state()

        if not state["total"] and action not in ("verify",):
            self._append("brain.init", {"owner": kwargs.get("name") or "owner", "spec": SPEC})
            state = self._state()

        if action == "brief":
            return json.dumps({"ok": True, "brief": self._brief(state)}, indent=2)

        if action == "verify":
            problems = self._verify()
            return json.dumps(
                {"ok": not problems, "events": state["total"], "problems": problems}, indent=2
            )

        if action == "remember":
            text = kwargs.get("text") or kwargs.get("query") or ""
            if not text:
                return json.dumps({"ok": False, "error": "nothing to remember"}, indent=2)
            event = self._append("note.add", {"text": text, "tags": [], "source": "brainstem"})
            return json.dumps({"ok": True, "seq": event["seq"], "remembered": text}, indent=2)

        if action == "recall":
            query = (kwargs.get("query") or kwargs.get("text") or "").lower()
            if not query:
                return json.dumps({"ok": False, "error": "no query"}, indent=2)
            hits = []
            for note in state["notes"]:
                if query in note.get("text", "").lower():
                    hits.append({"kind": "note", "text": note["text"], "ts": note.get("ts")})
            for contact in state["contacts"].values():
                if query in _canon(contact).lower():
                    hits.append({"kind": "contact", "contact": contact})
            for call in state["calls"].values():
                blob = _canon(call).lower()
                if query in blob:
                    hits.append(
                        {
                            "kind": "call",
                            "id": call.get("id"),
                            "peer": call.get("contact_name") or call.get("peer"),
                            "objective": call.get("objective"),
                            "outcome": call.get("outcome"),
                            "turns": call.get("turns", []),
                        }
                    )
            for appointment in state["appointments"].values():
                if query in _canon(appointment).lower():
                    hits.append({"kind": "appointment", "appointment": appointment})
            return json.dumps({"ok": True, "query": query, "count": len(hits), "hits": hits}, indent=2)

        if action == "contacts":
            return json.dumps({"ok": True, "contacts": list(state["contacts"].values())}, indent=2)

        if action == "contact_find":
            contact = self._find_contact(state, kwargs.get("query") or kwargs.get("name") or kwargs.get("phone"))
            return json.dumps({"ok": bool(contact), "contact": contact}, indent=2)

        if action == "contact_add":
            name = kwargs.get("name")
            if not name:
                return json.dumps({"ok": False, "error": "name required"}, indent=2)
            phone = _normalize_phone(kwargs.get("phone"))
            existing = self._find_contact(state, phone or name)
            payload = {"id": existing["id"] if existing else _new_id("ct"), "name": name}
            if phone:
                payload["phone"] = phone
            self._append("contact.upsert", payload)
            return json.dumps({"ok": True, "contact": payload, "updated": bool(existing)}, indent=2)

        if action == "call_start":
            target = kwargs.get("phone") or kwargs.get("query") or kwargs.get("name")
            contact = self._find_contact(state, target)
            peer = _normalize_phone(target) or (contact or {}).get("phone") or target
            if not peer:
                return json.dumps({"ok": False, "error": "no number to call"}, indent=2)
            payload = {
                "id": _new_id("call"),
                "direction": "outbound",
                "peer": peer,
                "contact_id": (contact or {}).get("id"),
                "contact_name": (contact or {}).get("name"),
                "objective": kwargs.get("objective") or kwargs.get("text"),
                "constraints": [],
                "provider": "brainstem",
            }
            self._append("call.start", payload)
            return json.dumps({"ok": True, "call": payload, "data_slush": {"call_id": payload["id"]}}, indent=2)

        if action == "call_turn":
            call = self._resolve(state["calls"], kwargs.get("query"))
            if not call:
                return json.dumps({"ok": False, "error": "unknown call"}, indent=2)
            payload = {
                "call_id": call["id"],
                "role": kwargs.get("role") or "peer",
                "text": kwargs.get("text") or "",
            }
            event = self._append("call.turn", payload)
            return json.dumps({"ok": True, "seq": event["seq"], "turn": payload}, indent=2)

        if action == "call_end":
            call = self._resolve(state["calls"], kwargs.get("query"))
            if not call:
                return json.dumps({"ok": False, "error": "unknown call"}, indent=2)
            payload = {
                "call_id": call["id"],
                "outcome": kwargs.get("outcome") or "unknown",
                "success": bool(kwargs.get("success")),
                "summary": kwargs.get("text") or "",
            }
            self._append("call.end", payload)
            return json.dumps({"ok": True, "call_id": call["id"], "outcome": payload["outcome"]}, indent=2)

        if action == "call_show":
            call = self._resolve(state["calls"], kwargs.get("query"))
            return json.dumps({"ok": bool(call), "call": call}, indent=2)

        if action == "propose_appointment":
            payload = {
                "id": _new_id("appt"),
                "title": kwargs.get("title") or "Appointment",
                "with": kwargs.get("with_whom"),
                "start": kwargs.get("start"),
                "call_id": kwargs.get("query"),
            }
            self._append("appointment.propose", payload)
            return json.dumps(
                {
                    "ok": True,
                    "appointment": payload,
                    "note": "Proposed, NOT booked. Confirm only after the owner approves.",
                    "data_slush": {"appointment_id": payload["id"]},
                },
                indent=2,
            )

        if action == "confirm_appointment":
            appointment = self._resolve(state["appointments"], kwargs.get("query"))
            if not appointment:
                return json.dumps({"ok": False, "error": "unknown appointment"}, indent=2)

            # The gate: refuse to book anything that was escalated but not approved.
            blocking = [
                a
                for a in state["approvals"].values()
                if a.get("ref") == appointment["id"] and a.get("status") != "approve"
            ]
            if blocking:
                return json.dumps(
                    {
                        "ok": False,
                        "error": "this appointment is waiting on the owner's approval",
                        "approvals": [{"id": a.get("id"), "status": a.get("status"), "subject": a.get("subject")} for a in blocking],
                    },
                    indent=2,
                )

            self._append("appointment.confirm", {"id": appointment["id"], "start": kwargs.get("start")})
            return json.dumps({"ok": True, "appointment_id": appointment["id"], "status": "confirmed"}, indent=2)

        if action == "appointments":
            return json.dumps({"ok": True, "appointments": list(state["appointments"].values())}, indent=2)

        if action == "request_approval":
            payload = {
                "id": _new_id("apr"),
                "subject": kwargs.get("title") or kwargs.get("text") or "Approval needed",
                "detail": kwargs.get("text") or "",
                "options": ["approve", "deny"],
                "ref": kwargs.get("query"),
                "channel": "brainstem",
            }
            self._append("approval.request", payload)
            return json.dumps(
                {"ok": True, "approval": payload, "data_slush": {"approval_id": payload["id"]}}, indent=2
            )

        if action == "decide_approval":
            approval = self._resolve(state["approvals"], kwargs.get("query"))
            if not approval:
                return json.dumps({"ok": False, "error": "unknown approval"}, indent=2)
            if approval.get("status") != "pending":
                return json.dumps(
                    {"ok": False, "error": "already %s" % approval.get("status")}, indent=2
                )
            decision = kwargs.get("decision")
            if decision not in ("approve", "deny"):
                return json.dumps({"ok": False, "error": "decision must be approve or deny"}, indent=2)
            self._append(
                "approval.decide",
                {"id": approval["id"], "decision": decision, "via": "brainstem", "note": kwargs.get("text") or ""},
            )
            return json.dumps({"ok": True, "approval_id": approval["id"], "decision": decision}, indent=2)

        if action == "pending_approvals":
            pending = [a for a in state["approvals"].values() if a.get("status") == "pending"]
            return json.dumps({"ok": True, "count": len(pending), "approvals": pending}, indent=2)

        if action == "set_preference":
            key = kwargs.get("key") or kwargs.get("title")
            value = kwargs.get("value") or kwargs.get("text")
            if not key or value is None:
                return json.dumps({"ok": False, "error": "key and value required"}, indent=2)
            self._append("pref.set", {"key": key, "value": value})
            return json.dumps({"ok": True, "key": key, "value": value}, indent=2)

        return json.dumps({"ok": False, "error": "unknown action '%s'" % action}, indent=2)
