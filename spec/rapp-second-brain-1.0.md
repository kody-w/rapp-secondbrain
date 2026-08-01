# rapp-second-brain/1.0

Wire format and behavioural contract for a RAPP Second Brain. Any implementation that
satisfies this document is interchangeable with `rsb`.

## 1. Storage

A brain is a directory (default `~/.rapp-second-brain/`, overridable with
`$RAPP_SECOND_BRAIN_HOME`):

```
events.jsonl     the log — the only source of truth
artifacts/       rendered documents (quotes, invoices)
.cache/          disposable; MUST be safe to delete at any time
```

Everything outside `events.jsonl` MUST be reconstructible from it.

## 2. Events

One JSON object per line, canonically encoded: UTF-8, keys sorted, separators `,` and
`:`, no trailing whitespace.

```json
{"actor":"cli","hash":"sha256:…","id":"ev_9f2c…","payload":{…},"prev":"sha256:…","seq":3,"ts":"2026-08-01T18:22:04Z","type":"call.end"}
```

| field | rule |
|---|---|
| `seq` | 1-based, contiguous, strictly increasing |
| `id` | `ev_` + 12 hex chars |
| `ts` | RFC 3339 UTC, second resolution, `Z` suffix |
| `type` | `<entity>.<verb>` (§4) |
| `actor` | who wrote it — `cli`, `mcp`, `http`, or a channel name such as `hotline` |
| `payload` | type-specific object |
| `prev` | `hash` of event `seq-1`; for `seq == 1` it is `sha256:` + 64 zeros |
| `hash` | `sha256` of the canonical encoding of the event **without** `hash` |

### 2.1 Verification

An implementation MUST provide a verify operation that recomputes every hash and
checks `prev` linkage and `seq` contiguity, and MUST exit non-zero on any failure.
`rsb` uses exit code `2` to distinguish tampering from ordinary errors (`1`).

### 2.2 Immutability

Events are append-only. Correction is expressed as a **new** event, never by editing
or deleting an existing line. There is no `delete` verb in this spec.

## 3. Projections

State is a pure left fold over the log. Given the same log, two implementations MUST
produce equal state. Unknown event types MUST be counted and otherwise ignored, so
that a newer writer never corrupts an older reader.

## 4. Event types

| type | payload (required keys in **bold**) |
|---|---|
| `brain.init` | **owner**, spec, version |
| `note.add` | **text**, tags[], source |
| `pref.set` | **key**, **value** |
| `contact.upsert` | **id**, name, phone, email, org, role, notes, tags[] |
| `call.start` | **id**, **direction** (`inbound`\|`outbound`), **peer**, contact_id, objective, constraints[], provider |
| `call.turn` | **call_id**, **role** (`agent`\|`peer`\|`owner`\|`system`), **text** |
| `call.end` | **call_id**, **outcome**, success, summary |
| `appointment.propose` | **id**, **title**, with, contact_id, start, end, location, call_id, notes |
| `appointment.confirm` | **id**, start, end, external_id |
| `appointment.cancel` | **id**, reason |
| `approval.request` | **id**, **subject**, detail, options[], ref, channel, expires_at |
| `approval.decide` | **id**, **decision** (`approve`\|`deny`), via, note |
| `lead.add` | **id**, name, contact_id, source, need, value_cents, currency |
| `lead.status` | **id**, **status** (`new`\|`contacted`\|`quoted`\|`won`\|`lost`) |
| `quote.create` | **id**, **number**, **items[]**, **total_cents**, currency, bill_to, lead_id, tax_percent, tax_cents, subtotal_cents, valid_until |
| `quote.status` | **id**, **status** (`draft`\|`sent`\|`accepted`\|`rejected`) |
| `invoice.create` | as `quote.create`, plus `due` |
| `invoice.pay` | **id**, via |
| `doc.render` | **ref**, **path**, **format** |

A line item is `{description, qty, unit_cents, amount_cents}`.

## 5. Normalisation

These rules exist so that the same real-world thing is one record, not three.

- **Phone** — E.164-ish: strip formatting, keep a leading `+`. A bare 10-digit number
  is assumed `+1`; 11 digits starting `1` becomes `+1…`. Handles beginning `sim:` are
  opaque and pass through unchanged (they are test/simulation identities).
- **Money** — integer **cents** in an `int`. Floats MUST NOT appear in a payload.
- **Time** — appointment times are ISO-8601 local; event `ts` is always UTC `Z`.
  A time expression that cannot be parsed MUST raise an error. An implementation MUST
  NOT guess: silently booking the wrong time is worse than failing.

## 6. Approval semantics

The normative behaviour of the whole spec:

1. `appointment.propose` records an intent. It is **not** a commitment.
2. An agent MUST NOT emit `appointment.confirm` for a proposal that violates a stated
   `pref.set` value unless a matching `approval.decide` with `decision = "approve"`
   exists whose `ref` is that appointment id.
3. `approval.decide` MUST be rejected when the approval is already decided.
4. A pending approval is not consent. Implementations MUST expose approval status
   through an **exit code** (`0` approved, non-zero otherwise) and not only as text,
   so that callers cannot be argued into proceeding.

## 7. Interfaces

An implementation SHOULD provide:

- a CLI where every command accepts `--json` and returns meaningful exit codes;
- an MCP server over stdio (JSON-RPC 2.0: `initialize`, `tools/list`, `tools/call`)
  that never terminates on a bad tool call;
- an HTTP surface with `GET /brief`, `GET /state`, `GET /health`, `POST /event`,
  `POST /remember`, gated by a bearer token when one is configured;
- a `context` operation returning a `<second_brain>…</second_brain>` block suitable
  for direct injection into an LLM system prompt.

## 8. Privacy

A brain is local by default. An implementation MUST NOT transmit brain contents
anywhere without explicit configuration, MUST NOT require an API key to function,
and SHOULD default `serve` to `127.0.0.1` and warn when started without a token.
