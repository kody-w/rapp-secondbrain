<div align="center">

# 🧠 RAPP Second Brain

**The memory an AI assistant needs before you let it talk to real people.**

One file. No dependencies. No API keys. No server. Your disk.

[![tests](https://github.com/kody-w/rapp-secondbrain/actions/workflows/test.yml/badge.svg)](https://github.com/kody-w/rapp-secondbrain/actions/workflows/test.yml)
&nbsp;·&nbsp; `rapp-second-brain/1.0` &nbsp;·&nbsp; Python 3.9+ stdlib only &nbsp;·&nbsp; MIT

</div>

---

## The problem

Give an AI a phone number and it can call a restaurant for you. That demo is easy.

The hard part is everything around the call:

- It needs to know **who** it's calling and what you already agreed with them.
- When the restaurant counter-offers 7:45pm instead of 7pm, it needs to know whether
  that breaks one of **your** standing rules — and if it does, it must **stop and ask you**.
- When you say yes, that yes has to be **recorded**, not held in a context window that
  evaporates when the session ends.
- Next week, when you ask "what did they say?", it has to be able to **show you the transcript**.
- And you need to be able to **prove** none of it was quietly rewritten.

That's not a chat log. That's a ledger. This is the ledger.

```
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│  phone   │  │ telegram │  │  email   │  │   chat   │   ← channels come and go
└────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘
     └─────────────┴──────┬──────┴─────────────┘
                   ┌──────▼──────┐
                   │     rsb     │   ← one brain, one truth, one audit trail
                   └──────┬──────┘
              events.jsonl │ hash-chained, append-only, yours
```

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/kody-w/rapp-secondbrain/main/install.sh | bash
```

Or just take the file — it's a single stdlib Python script with no install step:

```bash
curl -fsSLO https://raw.githubusercontent.com/kody-w/rapp-secondbrain/main/rsb && chmod +x rsb
./rsb init
```

## Ninety seconds

```bash
rsb init --owner Kody
rsb pref set preferred_dinner_time "19:00"
rsb contact add --name "Bella Vista" --phone "(555) 123-4567" --org Restaurant

# your agent makes a call and logs it as it goes
CALL=$(rsb --json call start --to "Bella Vista" \
        --objective "Table for 2, Friday 7pm" \
        --constraint "no later than 20:00" | jq -r .call.id)

rsb call turn --call $CALL --role agent --text "Table for two Friday at seven?"
rsb call turn --call $CALL --role peer  --text "Seven's booked. I could do 7:45."
rsb call end  --call $CALL --outcome counter_offer --summary "7:45pm held"

# 7:45 isn't 7:00 — so the agent proposes, it does not book
APPT=$(rsb --json appointment propose --title "Dinner at Bella Vista" \
        --with "Bella Vista" --start "friday 19:45" --call $CALL | jq -r .appointment.id)

APR=$(rsb --json approval request --ref $APPT \
        --subject "Bella Vista offered 7:45pm instead of 7pm" | jq -r .approval.id)

# ...it calls you back, you say yes...
rsb approval approve $APR --via phone
rsb appointment confirm $APPT --external-id gcal_abc123

rsb brief
rsb verify        # Chain intact — 12 events verified.
```

```
RAPP Second Brain — brief for Kody  (2026-08-01T18:22:04Z)

Upcoming
  Fri 07 Aug 2026 at 7:45 PM  Dinner at Bella Vista with Bella Vista

Recent calls
  2026-08-01T18:2  Bella Vista — counter_offer
```

## What it holds

| | |
|---|---|
| **contacts** | people and businesses, de-duplicated by normalised phone number |
| **calls** | direction, objective, constraints, full turn-by-turn transcript, outcome |
| **appointments** | `proposed` → `confirmed`/`cancelled`, linked to the call that created them |
| **approvals** | the human-in-the-loop gate, with an exit-code API |
| **leads / quotes / invoices** | a working pipeline with money as integer cents |
| **notes** | durable facts, tagged and searchable |
| **preferences** | your standing orders, injected into the agent's prompt |

## It produces things you can actually send

```bash
rsb invoice create --to "Riverside Cafe" \
  --item "Deep clean x4 @ 300.00" --item "Call-out @ 175.50" \
  --tax 8.25 --due "+30d" --render pdf
```

A real PDF, written by a ~150-line writer using the base-14 fonts with the actual
Helvetica metrics — so right-aligned money columns line up. No headless browser, no
LaTeX, no dependency. `--render html` and `--render md` are also there; Markdown is
what you paste into Google Docs when you need an editable version.

```bash
rsb calendar     # artifacts/calendar.ics — RFC 5545, folded, CRLF, escaped
```

Only **confirmed** appointments are exported: a proposal the owner has not approved
must never show up as a commitment. Subscribe live over HTTP and it lands in Google
Calendar, Apple Calendar or Outlook with no OAuth and no vendor at all:

```bash
rsb serve --token "$TOKEN"
# http://127.0.0.1:7431/$TOKEN/calendar.ics
```

## Why an event log

`events.jsonl` is append-only and hash-chained. Every record is a fold over that log,
so state is never authored directly — it's *derived*, and always reproducible.

```bash
rsb log --limit 5 --type call.end   # exactly which events caused a belief
rsb verify                          # exit 2 if a single byte of history changed
rsb export -o backup.json --include-log
```

Edit one character of one past event and `rsb verify` says so:

```
TAMPERING DETECTED
  seq 4: hash mismatch (event was edited)
  seq 5: broken chain (prev mismatch)
```

An assistant that acts in the real world on your behalf should be auditable by
construction. Nothing is ever deleted — a cancellation is a new event, not a rewrite.

## Wire it to your agent

**MCP** — works with any MCP host (Claude Code, Copilot CLI, Cursor):

```json
{ "mcpServers": { "second-brain": { "command": "rsb", "args": ["mcp"] } } }
```

**Skill** — hand [`SKILL.md`](SKILL.md) to any harness that reads skills and it knows
the whole CLI, including the rules about when *not* to act.

**RAPP brainstem** — drop [`agents/second_brain_agent.py`](agents/second_brain_agent.py) into
your `agents/` folder. It follows the grail agent ABI exactly, so it is discovered
automatically and needs no wiring. See [Runs on the grail kernel](#runs-on-the-grail-kernel).

**HTTP** — for telephony/Telegram webhooks:

```bash
rsb serve --port 7431 --token "$RAPP_SECOND_BRAIN_TOKEN"
# GET /brief /state /health   ·   POST /event /remember
```

**Anything else** — every command speaks `--json` and returns meaningful exit codes.

## Runs on the grail kernel

`agents/second_brain_agent.py` is a **second, independent implementation of the same
spec** — one file, one class extending `BasicAgent`, one `metadata` dict, one
`perform(**kwargs) -> str`, and every byte of I/O through the storage shim.

That last part is what makes it portable. It imports nothing but `json`, `hashlib`,
`datetime` and `uuid` — no subprocess, no sockets, no filesystem paths — so the same
file runs unmodified across every tier the kernel supports:

| | |
|---|---|
| **Tier 1** | local brainstem (`rapp_brainstem/`) |
| **Tier 2** | Azure Functions swarm |
| **Tier 3** | Copilot Studio |
| **Sphere** | Pyodide, in a browser tab |

A test enforces this by parsing the agent's AST and rejecting any import outside that
allowlist, so it cannot quietly regress.

It also implements `system_context()`, so the brain's state — preferences, what's
scheduled, what's awaiting your approval — is injected into **every turn's** system
prompt without the model having to think to ask.

### One brain, two implementations

`rsb` and the brainstem agent write the *same hash-chained log*, in the same canonical
encoding. They are interchangeable readers and writers:

```
your phone agent  ──┐
the browser sphere ─┼──►  events.jsonl  ◄── your terminal (rsb)
a cron job        ──┘
```

This is tested, not asserted — the suite interleaves writes from both implementations
and requires the chain to verify from either side, and requires an approval granted by
one to unlock a booking in the other:

```
test_interleaved_writes_keep_one_unbroken_chain ... ok
test_rsb_can_verify_a_log_the_agent_wrote       ... ok
test_agent_reads_what_rsb_wrote                 ... ok
test_the_approval_gate_agrees_across_both       ... ok
```

## The design rule

> **Propose is not book. Pending is not yes.**

`appointment propose` and `appointment confirm` are separate verbs, and
`approval check` communicates through its **exit code** so the gate can't be
talked around by a persuasive-sounding sentence:

```bash
if rsb approval check "$APR"; then
    rsb appointment confirm "$APPT"
else
    echo "not approved — do nothing"
fi
```

An agent with your phone and your calendar needs a place where "I asked and they
said yes" is a fact rather than a claim. That's the whole idea.

## Tests

```bash
python3 tests/run.py
```

86 tests, stdlib `unittest`, nothing to install. They cover the hash chain (including
tamper and deletion detection), phone/time/money parsing, HTML escaping, the MCP
protocol, the grail agent ABI, tier-portability enforcement, cross-implementation
interop, PDF structure (including xref offsets), RFC 5545 line folding, and a full
end-to-end reproduction of the call → negotiate → call back → approve → confirm flow.

```bash
./examples/jarvis-restaurant-call.sh    # watch the whole flow run
```

## Not to be confused with

[`kody-w/rapp-second-brain`](https://github.com/kody-w/rapp-second-brain) is a
different project: a brain that knows the **RAPP ecosystem itself**. This repo is a
**personal** second brain — your contacts, your calls, your calendar, your money.

## License

MIT
