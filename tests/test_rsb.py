#!/usr/bin/env python3
"""
Test suite for rsb — RAPP Second Brain.

Stdlib only. Run with:  python3 tests/test_rsb.py
"""

from __future__ import annotations

import datetime as _dt
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RSB = ROOT / "rsb"

# rsb has no .py suffix, so load it by explicit spec
_spec = importlib.util.spec_from_loader("rsb", importlib.machinery.SourceFileLoader("rsb", str(RSB)))
rsb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rsb)


class BrainTestCase(unittest.TestCase):
    """Base: every test gets a private, empty brain."""

    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="rsb-test-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)

    def run_cli(self, *args: str, expect: int = 0) -> dict:
        """Invoke rsb in-process with --json and return the parsed payload."""
        import io
        from contextlib import redirect_stdout

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = rsb.main(["--home", str(self.home), "--json", *args])
        self.assertEqual(code, expect, f"rsb {' '.join(args)} exited {code}\n{buffer.getvalue()}")
        text = buffer.getvalue().strip()
        return json.loads(text) if text else {}

    def init(self, owner: str = "Tester") -> None:
        self.run_cli("init", "--owner", owner)


# ---------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------


class TestMoney(unittest.TestCase):
    def test_parses_common_shapes(self) -> None:
        self.assertEqual(rsb.to_cents("150.00"), 15000)
        self.assertEqual(rsb.to_cents("1,250.50"), 125050)
        self.assertEqual(rsb.to_cents("$99.99"), 9999)
        self.assertEqual(rsb.to_cents(20), 2000)
        self.assertEqual(rsb.to_cents("4999c"), 4999)
        self.assertEqual(rsb.to_cents(""), 0)

    def test_no_float_drift(self) -> None:
        # 0.1 + 0.2 style drift must never reach a total
        total = sum(rsb.to_cents(x) for x in ["0.10", "0.20"])
        self.assertEqual(total, 30)

    def test_rejects_garbage(self) -> None:
        with self.assertRaises(rsb.BrainError):
            rsb.to_cents("twenty quid")

    def test_formats(self) -> None:
        self.assertEqual(rsb.money(125050), "$1,250.50")
        self.assertEqual(rsb.money(-500), "-$5.00")


class TestPhone(unittest.TestCase):
    def test_normalises_to_one_key(self) -> None:
        forms = ["(555) 123-4567", "555-123-4567", "5551234567", "+1 555 123 4567", "1-555-123-4567"]
        self.assertEqual({rsb.normalize_phone(f) for f in forms}, {"+15551234567"})

    def test_keeps_international(self) -> None:
        self.assertEqual(rsb.normalize_phone("+44 20 7946 0958"), "+442079460958")

    def test_passthrough_simulation_handles(self) -> None:
        self.assertEqual(rsb.normalize_phone("sim:bella-vista"), "sim:bella-vista")


class TestWhen(unittest.TestCase):
    def setUp(self) -> None:
        # Wednesday 2026-08-05, 10:00
        self.ref = _dt.datetime(2026, 8, 5, 10, 0)

    def test_iso_passthrough(self) -> None:
        self.assertEqual(rsb.parse_when("2026-08-07T19:00"), "2026-08-07T19:00:00")

    def test_tomorrow_with_time(self) -> None:
        self.assertEqual(rsb.parse_when("tomorrow 7pm", ref=self.ref), "2026-08-06T19:00:00")

    def test_weekday_rolls_forward(self) -> None:
        # From Wednesday, "friday" is 2 days out
        self.assertEqual(rsb.parse_when("friday 19:30", ref=self.ref), "2026-08-07T19:30:00")

    def test_same_weekday_goes_to_next_week(self) -> None:
        self.assertEqual(rsb.parse_when("wednesday 9am", ref=self.ref), "2026-08-12T09:00:00")

    def test_relative_offset(self) -> None:
        self.assertEqual(rsb.parse_when("+2h", ref=self.ref), "2026-08-05T12:00:00")

    def test_tonight_is_pm(self) -> None:
        self.assertEqual(rsb.parse_when("tonight 8", ref=self.ref), "2026-08-05T20:00:00")

    def test_refuses_garbage_rather_than_guessing(self) -> None:
        # A booking agent must never silently invent a time.
        with self.assertRaises(rsb.BrainError):
            rsb.parse_when("sometime next quarter maybe")

    def test_empty_is_none(self) -> None:
        self.assertIsNone(rsb.parse_when(None))
        self.assertIsNone(rsb.parse_when(""))


class TestLineItems(unittest.TestCase):
    def test_parses_qty_and_unit(self) -> None:
        items, total = rsb.line_items(["Brake pads x2 @ 89.50", "Labour @ 120.00"])
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["amount_cents"], 17900)
        self.assertEqual(total, 17900 + 12000)

    def test_rejects_malformed(self) -> None:
        with self.assertRaises(rsb.BrainError):
            rsb.line_items(["just some words"])


# ---------------------------------------------------------------------------
# the log
# ---------------------------------------------------------------------------


class TestIntegrity(BrainTestCase):
    def test_chain_verifies(self) -> None:
        self.init()
        self.run_cli("remember", "Kody prefers evening appointments")
        self.run_cli("contact", "add", "--name", "Ada", "--phone", "5551110000")
        result = self.run_cli("verify")
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(result["events"], 3)

    def test_detects_edited_event(self) -> None:
        self.init()
        self.run_cli("remember", "original fact")
        log = self.home / "events.jsonl"
        lines = log.read_text().splitlines()
        tampered = json.loads(lines[-1])
        tampered["payload"]["text"] = "forged fact"
        lines[-1] = json.dumps(tampered, sort_keys=True, separators=(",", ":"))
        log.write_text("\n".join(lines) + "\n")

        result = self.run_cli("verify", expect=2)
        self.assertFalse(result["ok"])
        self.assertTrue(any("hash mismatch" in p for p in result["problems"]))

    def test_detects_deleted_event(self) -> None:
        self.init()
        self.run_cli("remember", "one")
        self.run_cli("remember", "two")
        log = self.home / "events.jsonl"
        lines = log.read_text().splitlines()
        del lines[1]  # drop a middle event
        log.write_text("\n".join(lines) + "\n")
        result = self.run_cli("verify", expect=2)
        self.assertFalse(result["ok"])

    def test_projection_is_pure_fold(self) -> None:
        """State must be reproducible from the log alone."""
        self.init()
        self.run_cli("contact", "add", "--name", "Ada", "--phone", "5551110000")
        self.run_cli("remember", "a fact")
        brain = rsb.Brain(self.home)
        first = brain.state()
        rebuilt = rsb.fold(brain.read())
        self.assertEqual(rsb.canon(first), rsb.canon(rebuilt))


# ---------------------------------------------------------------------------
# entities
# ---------------------------------------------------------------------------


class TestContacts(BrainTestCase):
    def test_upsert_merges_on_phone(self) -> None:
        self.init()
        self.run_cli("contact", "add", "--name", "Bella Vista", "--phone", "(555) 123-4567")
        self.run_cli("contact", "add", "--name", "Bella Vista", "--phone", "555-123-4567", "--email", "hi@bella.example")
        listing = self.run_cli("contact", "list")
        self.assertEqual(listing["count"], 1, "same phone in a different format must not create a duplicate")
        self.assertEqual(listing["contacts"][0]["email"], "hi@bella.example")

    def test_find_by_name_phone_and_id(self) -> None:
        self.init()
        added = self.run_cli("contact", "add", "--name", "Mike's Garage", "--phone", "5559998888")
        contact_id = added["contact"]["id"]
        for needle in ["Mike's Garage", "mike", "5559998888", "+15559998888", contact_id]:
            found = self.run_cli("contact", "find", needle)
            self.assertTrue(found["ok"], f"failed to resolve {needle!r}")
            self.assertEqual(found["contact"]["id"], contact_id)

    def test_missing_contact_exits_nonzero(self) -> None:
        self.init()
        self.run_cli("contact", "find", "nobody", expect=1)


class TestApprovals(BrainTestCase):
    def test_request_then_approve(self) -> None:
        self.init()
        requested = self.run_cli("approval", "request", "--subject", "Book 7:30pm instead of 7pm", "--detail", "only slot left")
        approval_id = requested["approval"]["id"]

        pending = self.run_cli("approval", "list", "--pending")
        self.assertEqual(pending["count"], 1)

        self.run_cli("approval", "check", approval_id, expect=1)  # not yet granted
        self.run_cli("approval", "approve", approval_id, "--via", "phone")
        self.run_cli("approval", "check", approval_id, expect=0)  # granted

        self.assertEqual(self.run_cli("approval", "list", "--pending")["count"], 0)

    def test_cannot_decide_twice(self) -> None:
        self.init()
        approval_id = self.run_cli("approval", "request", "--subject", "x")["approval"]["id"]
        self.run_cli("approval", "approve", approval_id)
        self.run_cli("approval", "deny", approval_id, expect=1)  # already decided

    def test_short_id_prefix_resolves(self) -> None:
        self.init()
        approval_id = self.run_cli("approval", "request", "--subject", "x")["approval"]["id"]
        self.run_cli("approval", "approve", approval_id[-6:])


class TestDocuments(BrainTestCase):
    def test_quote_totals_and_tax(self) -> None:
        self.init()
        quote = self.run_cli(
            "quote", "create", "--to", "Acme", "--item", "Consulting x10 @ 150.00", "--item", "Setup @ 500.00", "--tax", "8.25"
        )["quote"]
        self.assertEqual(quote["subtotal_cents"], 150000 + 50000)
        self.assertEqual(quote["tax_cents"], round(200000 * 8.25 / 100))
        self.assertEqual(quote["total_cents"], quote["subtotal_cents"] + quote["tax_cents"])

    def test_renders_html_and_markdown(self) -> None:
        self.init()
        for fmt, suffix in (("html", ".html"), ("md", ".md")):
            result = self.run_cli("invoice", "create", "--to", "Acme", "--item", "Work @ 100.00", "--render", fmt)
            path = Path(result["document"])
            self.assertTrue(path.exists(), f"{fmt} document was not written")
            self.assertTrue(path.name.endswith(suffix))
            body = path.read_text()
            self.assertIn("$100.00", body)

    def test_html_escapes_injection(self) -> None:
        self.init()
        result = self.run_cli(
            "invoice", "create", "--to", "<script>alert(1)</script>", "--item", "Work @ 10.00", "--render", "html"
        )
        body = Path(result["document"]).read_text()
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;script&gt;", body)

    def test_invoice_numbers_increment(self) -> None:
        self.init()
        first = self.run_cli("invoice", "create", "--to", "A", "--item", "x @ 1.00")["invoice"]["number"]
        second = self.run_cli("invoice", "create", "--to", "B", "--item", "x @ 1.00")["invoice"]["number"]
        self.assertNotEqual(first, second)

    def test_unpaid_filter_and_payment(self) -> None:
        self.init()
        invoice_id = self.run_cli("invoice", "create", "--to", "A", "--item", "x @ 42.00")["invoice"]["id"]
        self.assertEqual(self.run_cli("invoice", "list", "--unpaid")["count"], 1)
        self.run_cli("invoice", "pay", invoice_id, "--via", "bank transfer")
        self.assertEqual(self.run_cli("invoice", "list", "--unpaid")["count"], 0)


class TestPdf(BrainTestCase):
    """An invoice you cannot send is not an invoice."""

    def make(self, **kwargs) -> Path:
        args = ["invoice", "create", "--to", kwargs.pop("to", "Acme Ltd"), "--render", "pdf"]
        for item in kwargs.pop("items", ["Consulting x10 @ 150.00"]):
            args += ["--item", item]
        for flag, value in kwargs.items():
            args += [f"--{flag.replace('_', '-')}", str(value)]
        return Path(self.run_cli(*args)["document"])

    def read_text_runs(self, path: Path) -> list[str]:
        raw = path.read_bytes().decode("latin-1")
        stream = raw.split("stream", 1)[1].rsplit("endstream", 1)[0]
        return re.findall(r"\((.*?)\) Tj", stream)

    def test_produces_a_real_pdf(self) -> None:
        self.init()
        path = self.make()
        data = path.read_bytes()
        self.assertTrue(path.name.endswith(".pdf"))
        self.assertTrue(data.startswith(b"%PDF-1.4"))
        self.assertTrue(data.rstrip().endswith(b"%%EOF"))
        self.assertIn(b"/Type /Catalog", data)
        self.assertIn(b"xref", data)

    def test_xref_offsets_are_correct(self) -> None:
        """A wrong offset produces a file that opens in nothing."""
        self.init()
        data = self.make().read_bytes()

        start = int(data.rsplit(b"startxref", 1)[1].split(b"%%EOF")[0].strip())
        self.assertEqual(data[start : start + 4], b"xref")

        table = data[start:].split(b"\n")[2:]
        for index, row in enumerate(table, start=1):
            if not re.match(rb"^\d{10} \d{5} n", row):
                break
            offset = int(row.split(b" ")[0])
            self.assertEqual(data[offset : offset + len(str(index)) + 6], f"{index} 0 obj".encode())

    def test_contains_the_numbers(self) -> None:
        self.init()
        path = self.make(items=["Deep clean x4 @ 300.00", "Call-out @ 175.50"], tax=8.25)
        runs = self.read_text_runs(path)
        self.assertIn("$1,200.00", runs)
        self.assertIn("$175.50", runs)
        self.assertIn("Total", runs)
        # subtotal 1375.50 + 8.25% tax (113.48) = 1488.98
        self.assertIn("$1,375.50", runs)
        self.assertIn("$1,488.98", runs)

    def test_handles_non_ascii_without_corrupting_the_file(self) -> None:
        self.init()
        path = self.make(to="Riverside Caf\u00e9", items=["Nettoyage \u00e0 sec @ 50.00"])
        self.assertTrue(path.read_bytes().startswith(b"%PDF"))
        self.assertIn("Riverside Caf\u00e9", self.read_text_runs(path))

    def test_escapes_pdf_syntax_in_user_text(self) -> None:
        """Unescaped parens would terminate the string and corrupt the page."""
        self.init()
        path = self.make(to="Acme (Holdings) \\ Co")
        data = path.read_bytes()
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertIn(r"Acme \(Holdings\) \\ Co", self.read_text_runs(path))

    def test_text_width_matches_helvetica_metrics(self) -> None:
        # 'i' is narrow, 'W' is wide — a right-aligned column depends on this.
        self.assertLess(rsb.text_width("iiii", 12), rsb.text_width("WWWW", 12))
        self.assertAlmostEqual(rsb.text_width("A", 1000), 667, places=0)
        self.assertAlmostEqual(rsb.text_width("A", 1000, bold=True), 722, places=0)

    def test_quotes_render_too(self) -> None:
        self.init()
        result = self.run_cli("quote", "create", "--to", "Acme", "--item", "Work @ 10.00", "--render", "pdf")
        self.assertTrue(Path(result["document"]).exists())


class TestCalendar(BrainTestCase):
    """A booking that never reaches a calendar has not really happened."""

    def confirmed_appointment(self, **kwargs) -> str:
        args = ["appointment", "propose", "--title", kwargs.pop("title", "Dinner")]
        for flag, value in kwargs.items():
            args += [f"--{flag}", str(value)]
        appointment_id = self.run_cli(*args)["appointment"]["id"]
        self.run_cli("appointment", "confirm", appointment_id)
        return appointment_id

    def ics(self) -> str:
        path = Path(self.run_cli("calendar")["path"])
        return path.read_bytes().decode("utf-8")

    def test_emits_rfc5545(self) -> None:
        self.init()
        self.confirmed_appointment(start="2026-08-07T19:45")
        body = self.ics()

        self.assertTrue(body.startswith("BEGIN:VCALENDAR\r\n"))
        self.assertTrue(body.rstrip().endswith("END:VCALENDAR"))
        self.assertIn("VERSION:2.0", body)
        self.assertIn("DTSTART:20260807T194500", body)
        self.assertIn("STATUS:CONFIRMED", body)

    def test_uses_crlf_everywhere(self) -> None:
        """Bare LF is the classic reason a feed silently fails to import."""
        self.init()
        self.confirmed_appointment(start="2026-08-07T19:45")
        raw = Path(self.run_cli("calendar")["path"]).read_bytes()
        self.assertEqual(raw.count(b"\n") - raw.count(b"\r\n"), 0)

    def test_escapes_separators(self) -> None:
        self.init()
        self.confirmed_appointment(
            title="Dinner; table for 2, window", location="12 High St, Anytown", start="2026-08-07T19:45"
        )
        body = self.ics()
        self.assertIn(r"SUMMARY:Dinner\; table for 2\, window", body)
        self.assertIn(r"LOCATION:12 High St\, Anytown", body)

    def test_folds_long_lines_to_75_octets(self) -> None:
        self.init()
        self.confirmed_appointment(title="A " + "very " * 40 + "long title", start="2026-08-07T19:45")
        raw = Path(self.run_cli("calendar")["path"]).read_bytes()
        for line in raw.split(b"\r\n"):
            self.assertLessEqual(len(line), 75, f"unfolded line: {line[:90]!r}")
        # and it must still be readable once unfolded
        self.assertIn("very very", raw.decode("utf-8").replace("\r\n ", ""))

    def test_only_confirmed_by_default(self) -> None:
        self.init()
        self.confirmed_appointment(start="2026-08-07T19:45")
        self.run_cli("appointment", "propose", "--title", "Just a hold", "--start", "2026-08-09T10:00")

        self.assertEqual(self.ics().count("BEGIN:VEVENT"), 1)
        self.assertNotIn("Just a hold", self.ics())

        path = Path(self.run_cli("calendar", "--include-proposed")["path"])
        body = path.read_bytes().decode("utf-8")
        self.assertEqual(body.count("BEGIN:VEVENT"), 2)
        self.assertIn("STATUS:TENTATIVE", body)

    def test_cancelled_appointments_disappear(self) -> None:
        self.init()
        appointment_id = self.confirmed_appointment(start="2026-08-07T19:45")
        self.run_cli("appointment", "cancel", appointment_id)
        self.assertEqual(self.ics().count("BEGIN:VEVENT"), 0)

    def test_default_duration_is_an_hour(self) -> None:
        self.init()
        self.confirmed_appointment(start="2026-08-07T19:45")
        body = self.ics()
        self.assertIn("DTSTART:20260807T194500", body)
        self.assertIn("DTEND:20260807T204500", body)

    def test_uid_is_stable_across_regeneration(self) -> None:
        """Regenerating must update the event, not duplicate it."""
        self.init()
        appointment_id = self.confirmed_appointment(start="2026-08-07T19:45")
        first, second = self.ics(), self.ics()
        self.assertIn(f"UID:{appointment_id}@rapp-second-brain", first)
        self.assertEqual(
            [line for line in first.split("\r\n") if line.startswith("UID:")],
            [line for line in second.split("\r\n") if line.startswith("UID:")],
        )

    def test_appointment_without_a_time_is_skipped(self) -> None:
        self.init()
        self.confirmed_appointment(title="No time given")
        self.assertEqual(self.ics().count("BEGIN:VEVENT"), 0)


class TestRecall(BrainTestCase):
    def test_searches_across_kinds(self) -> None:
        self.init()
        self.run_cli("contact", "add", "--name", "Bella Vista", "--phone", "5551234567")
        self.run_cli("remember", "Bella Vista does the best carbonara")
        call_id = self.run_cli("call", "start", "--to", "5551234567", "--objective", "book a table")["call"]["id"]
        self.run_cli("call", "turn", "--call", call_id, "--role", "peer", "--text", "we have a table at 7:30")

        hits = self.run_cli("recall", "bella")
        kinds = {h["kind"] for h in hits["hits"]}
        self.assertIn("contact", kinds)
        self.assertIn("note", kinds)

        transcript_hit = self.run_cli("recall", "carbonara")
        self.assertEqual(transcript_hit["count"], 1)

    def test_no_match_exits_nonzero(self) -> None:
        self.init()
        self.run_cli("recall", "zzzz-nothing", expect=1)


# ---------------------------------------------------------------------------
# the JARVIS scenario — the whole point
# ---------------------------------------------------------------------------


class TestJarvisScenario(BrainTestCase):
    """
    Reproduces the flow from the JARVIS demo end to end, against the brain:

      1. The owner sets a standing preference.
      2. The agent calls a business with an objective and hard constraints.
      3. The business counter-offers a time outside the ideal.
      4. The agent records the negotiation and does NOT commit unilaterally.
      5. The agent calls the owner back and requests approval.
      6. The owner approves.
      7. Only then is the appointment confirmed and calendar-ready.
      8. The brief surfaces it, and the whole chain is tamper-evident.
    """

    def test_full_flow(self) -> None:
        self.init("Kody")
        self.run_cli("pref", "set", "preferred_dinner_time", "19:00")
        self.run_cli("contact", "add", "--name", "Bella Vista", "--phone", "(555) 123-4567", "--org", "Restaurant")

        # 2. outbound call with an objective and constraints
        call = self.run_cli(
            "call", "start",
            "--to", "Bella Vista",
            "--objective", "Book a table for 2 on Friday at 7pm",
            "--constraint", "party size exactly 2",
            "--constraint", "no later than 20:00",
        )["call"]
        call_id = call["id"]
        self.assertEqual(call["contact_name"], "Bella Vista")
        self.assertEqual(call["peer"], "+15551234567")

        # 3. the negotiation
        turns = [
            ("agent", "Hi, I'd like to book a table for two this Friday at 7pm."),
            ("peer", "Seven is fully booked. I could do 7:45."),
            ("agent", "7:45 works within my constraints — let me confirm with the owner and call you back."),
        ]
        for role, text in turns:
            self.run_cli("call", "turn", "--call", call_id, "--role", role, "--text", text)

        self.run_cli(
            "call", "end", "--call", call_id,
            "--outcome", "counter_offer",
            "--summary", "7pm unavailable; 7:45pm offered and held",
        )

        # 4. a proposal, explicitly NOT confirmed
        appointment = self.run_cli(
            "appointment", "propose",
            "--title", "Dinner at Bella Vista (2)",
            "--with", "Bella Vista",
            "--start", "friday 19:45",
            "--call", call_id,
        )["appointment"]
        appointment_id = appointment["id"]
        self.assertEqual(
            self.run_cli("appointment", "list", "--status", "confirmed")["count"], 0,
            "the agent must not confirm before the owner approves",
        )

        # 5. callback for approval
        approval = self.run_cli(
            "approval", "request",
            "--subject", "Bella Vista offered 7:45pm instead of 7pm",
            "--detail", "Shall I take it?",
            "--ref", appointment_id,
        )["approval"]
        self.assertEqual(approval["ref"], appointment_id)

        callback = self.run_cli(
            "call", "start", "--to", "Bella Vista", "--direction", "outbound",
            "--objective", "Call the owner back for approval",
        )["call"]
        self.run_cli("call", "turn", "--call", callback["id"], "--role", "owner", "--text", "Yes, book it.")
        self.run_cli("call", "end", "--call", callback["id"], "--outcome", "approved", "--success")

        # 6 + 7. approve, then and only then confirm
        self.run_cli("approval", "approve", approval["id"], "--via", "phone")
        self.run_cli("approval", "check", approval["id"], expect=0)
        self.run_cli("appointment", "confirm", appointment_id, "--external-id", "gcal_abc123")

        confirmed = self.run_cli("appointment", "list", "--status", "confirmed")
        self.assertEqual(confirmed["count"], 1)
        booked = confirmed["appointments"][0]
        self.assertEqual(booked["external_id"], "gcal_abc123")
        self.assertTrue(booked["start"].endswith("19:45:00"))

        # 8. the brief tells the owner, and nothing was forged
        brief = self.run_cli("brief")
        self.assertEqual(len(brief["pending_approvals"]), 0)
        self.assertEqual(len(brief["upcoming_appointments"]), 1)
        self.assertEqual(brief["totals"]["calls"], 2)

        self.assertTrue(self.run_cli("verify")["ok"])

        # the transcript is recoverable and attributable
        shown = self.run_cli("call", "show", call_id)["call"]
        self.assertEqual(len(shown["turns"]), 3)
        self.assertIn("7:45", shown["turns"][1]["text"])

    def test_denied_approval_leaves_nothing_booked(self) -> None:
        self.init("Kody")
        appointment_id = self.run_cli(
            "appointment", "propose", "--title", "Risky booking", "--start", "tomorrow 15:00"
        )["appointment"]["id"]
        approval_id = self.run_cli("approval", "request", "--subject", "Book it?", "--ref", appointment_id)["approval"]["id"]

        self.run_cli("approval", "deny", approval_id, "--note", "too expensive")
        self.run_cli("approval", "check", approval_id, expect=1)
        self.run_cli("appointment", "cancel", appointment_id, "--reason", "owner declined")

        self.assertEqual(self.run_cli("appointment", "list", "--status", "confirmed")["count"], 0)

    def test_business_flow_lead_to_paid_invoice(self) -> None:
        """The second half of the demo: voice note -> lead -> quote -> invoice -> paid."""
        self.init("Kody")
        lead = self.run_cli(
            "lead", "add", "--name", "Riverside Cafe", "--phone", "5552223333",
            "--source", "telegram voice note", "--need", "Weekly deep clean", "--value", "1200.00",
        )["lead"]
        self.assertEqual(lead["value_cents"], 120000)
        # adding a lead for an unknown person creates the contact too
        self.assertTrue(self.run_cli("contact", "find", "Riverside Cafe")["ok"])

        quote = self.run_cli(
            "quote", "create", "--lead", lead["id"], "--item", "Deep clean x4 @ 300.00", "--render", "html"
        )["quote"]
        self.assertEqual(quote["total_cents"], 120000)
        self.run_cli("quote", "status", quote["id"], "accepted")
        self.run_cli("lead", "status", lead["id"], "won")

        invoice = self.run_cli(
            "invoice", "create", "--lead", lead["id"], "--item", "Deep clean x4 @ 300.00", "--due", "+30d", "--render", "html"
        )["invoice"]
        self.assertEqual(invoice["bill_to"], "Riverside Cafe")

        brief = self.run_cli("brief")
        self.assertEqual(brief["unpaid_total_cents"], 120000)

        self.run_cli("invoice", "pay", invoice["id"], "--via", "card")
        self.assertEqual(self.run_cli("brief")["unpaid_total_cents"], 0)


class TestContextBlock(BrainTestCase):
    def test_context_is_prompt_ready(self) -> None:
        self.init("Kody")
        self.run_cli("pref", "set", "tone", "concise")
        self.run_cli("remember", "Allergic to shellfish")
        self.run_cli("approval", "request", "--subject", "Approve the 7:45 booking")

        context = self.run_cli("context")["context"]
        self.assertTrue(context.startswith("<second_brain>"))
        self.assertTrue(context.rstrip().endswith("</second_brain>"))
        self.assertIn("Allergic to shellfish", context)
        self.assertIn("tone: concise", context)
        self.assertIn("Approve the 7:45 booking", context)


# ---------------------------------------------------------------------------
# machine surfaces
# ---------------------------------------------------------------------------


class TestMcpServer(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="rsb-mcp-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.env = {**os.environ, "RAPP_SECOND_BRAIN_HOME": str(self.home)}
        subprocess.run([sys.executable, str(RSB), "init"], env=self.env, check=True, capture_output=True)
        subprocess.run(
            [sys.executable, str(RSB), "remember", "The garage is called Mike's"],
            env=self.env, check=True, capture_output=True,
        )

    def _talk(self, requests: list[dict]) -> list[dict]:
        payload = "\n".join(json.dumps(r) for r in requests) + "\n"
        result = subprocess.run(
            [sys.executable, str(RSB), "mcp"], input=payload, env=self.env,
            capture_output=True, text=True, timeout=30,
        )
        return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]

    def test_initialize_list_and_call(self) -> None:
        responses = self._talk([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "second_brain_recall", "arguments": {"query": "garage"}}},
        ])
        self.assertEqual(len(responses), 3)
        self.assertEqual(responses[0]["result"]["serverInfo"]["name"], "rapp-second-brain")
        names = {t["name"] for t in responses[1]["result"]["tools"]}
        self.assertIn("second_brain_brief", names)
        self.assertIn("second_brain_recall", names)
        body = json.loads(responses[2]["result"]["content"][0]["text"])
        self.assertEqual(len(body["notes"]), 1)

    def test_unknown_tool_is_an_error_not_a_crash(self) -> None:
        responses = self._talk([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "nope", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 2, "method": "initialize"},
        ])
        self.assertTrue(responses[0]["result"].get("isError"))
        self.assertEqual(responses[1]["id"], 2, "server must stay alive after a bad tool call")

    def test_write_through_mcp_lands_in_the_log(self) -> None:
        self._talk([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "second_brain_remember", "arguments": {"text": "written by an agent"}}},
        ])
        result = subprocess.run(
            [sys.executable, str(RSB), "--json", "recall", "written by an agent"],
            env=self.env, capture_output=True, text=True,
        )
        self.assertEqual(json.loads(result.stdout)["count"], 1)


class TestCliContract(unittest.TestCase):
    """The CLI is a public API — these guard its shape."""

    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="rsb-cli-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.env = {**os.environ, "RAPP_SECOND_BRAIN_HOME": str(self.home)}

    def sh(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(RSB), *args], env=self.env, capture_output=True, text=True)

    def test_runs_as_a_script(self) -> None:
        self.assertEqual(self.sh("--version").returncode, 0)

    def test_errors_before_init(self) -> None:
        result = self.sh("brief")
        self.assertEqual(result.returncode, 1)
        self.assertIn("rsb init", result.stderr)

    def test_json_errors_are_json(self) -> None:
        result = self.sh("--json", "brief")
        self.assertEqual(json.loads(result.stdout)["ok"], False)

    def test_doctor_reports_health(self) -> None:
        self.sh("init")
        result = self.sh("--json", "doctor")
        self.assertEqual(result.returncode, 0)
        self.assertTrue(json.loads(result.stdout)["ok"])

    def test_export_round_trips(self) -> None:
        self.sh("init")
        self.sh("remember", "exportable")
        result = self.sh("--json", "export", "--include-log")
        bundle = json.loads(result.stdout)
        self.assertEqual(bundle["spec"], rsb.SPEC)
        self.assertGreaterEqual(len(bundle["events"]), 2)

    def test_no_third_party_imports(self) -> None:
        """The whole promise is zero dependencies — keep it true."""
        source = RSB.read_text()
        banned = ["import requests", "import httpx", "from flask", "import flask", "import pydantic", "import openai"]
        for needle in banned:
            self.assertNotIn(needle, source, f"rsb must stay stdlib-only, found {needle!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
