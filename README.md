# Anchor ⚓ — pins first, compress second

**When Hermes compresses a long session, it forgets your decisions. Anchor pins them first.**

The built-in context compressor summarises *everything* with a model call —
so a price you agreed, a date you fixed, or a vendor you approved can come
back paraphrased, rounded, or gone. Anchor is a context engine built on the
opposite policy:

1. **Pin first.** Decisions, amounts, dates and deadlines are detected
   deterministically as the conversation happens — no model call, no
   embeddings — and stored as *pins*. You (or the agent) can also pin any
   fact explicitly.
2. **Compress second.** When compression fires, every pin is re-issued
   **verbatim** in a carrier message, the head and tail of the conversation
   stay untouched, and only the unpinned middle folds — into an honest,
   size-capped extractive digest that calls itself a map, not a summary.
3. **Prove it.** The test suite compresses the same conversation **100
   times** and diffs the pins: not one character drifts, and the carrier
   appears exactly once.

Anchor makes **zero model calls**. Compression costs nothing, is fully
deterministic (same input → same output), and works offline.

> LCM-style engines keep *everything* and let the agent dig it back up.
> Anchor keeps almost *nothing* — except what must never be lost.

## Install

```bash
hermes plugins install <this-repo>   # or copy this directory to $HERMES_HOME/plugins/anchor/
```

Then activate it in `config.yaml` (context engines are never auto-activated):

```yaml
context:
  engine: "anchor"
```

## Do X, see Y

| Do this | See this |
|---|---|
| Chat until a decision appears: *"Decision: we launch on 20 Nov 2026."* | Nothing intrusive — Anchor silently pins it (check with `/anchor pins`) |
| Say *"The budget is $4,200 for the venue."* | It is pinned as a `money` fact |
| Run `/anchor pin Final: the venue is Marina Hall.` | `📌 Pinned […]: Final: the venue is Marina Hall.` |
| Let the session grow past the threshold (75% of the context window) or run `/compress` | Context shrinks; a carrier message `[Anchor — pinned facts, kept verbatim]` now holds every pin word-for-word; the middle became one capped digest |
| Ask the agent *"What did we decide?"* after several compressions | The exact decision, amount and date — not a paraphrase |
| Run `/anchor status` | `⚓ Anchor: N pin(s) protected · C compression(s) · ~T tokens saved (est.) · model calls made: 0` |
| Run `/anchor unpin <id or exact text>` | That fact stops being protected from the next pass on |

## Tools (agent-callable)

| Tool | What it does |
|---|---|
| `anchor_pin` | Pin a fact verbatim (`text`, optional `kind`: decision / money / date / fact) |
| `anchor_unpin` | Remove a pin by id or exact text |
| `anchor_pins` | List active pins |
| `anchor_status` | Pins, compression count, estimated tokens saved, model calls made (always 0) |

## How it works

- **Detection** (`pins.py`) — sentence-level, deterministic patterns:
  explicit markers (*Decision:, Decided, Approved, Deadline:, Remember:…*),
  money (₹ / $ / € / £ / Rs / INR / USD… with amounts), and dates
  (ISO, "20 Nov 2026", "by Friday"). A marked sentence is kept as one
  verbatim pin.
- **Storage** — a small, lock-guarded JSON store at
  `$HERMES_HOME/plugin-data/anchor/pins.json`. Pins are content-addressed,
  so re-scanning a conversation never duplicates one. The engine is safe
  under Hermes's threaded compression (`clone_for_agent` shares the store
  file, never a connection).
- **Compression** (`engine.py`) — protects the system prompt, the first 3
  and last 6 messages (the ABC defaults), any message matching a manual
  `/compress <focus>` topic, and Anchor's own earlier digests (never
  re-digested). The carrier is rebuilt fresh each pass, so pins can never
  duplicate or drift.
- **Digest** (`digest.py`) — one capped block (1,200 chars max) listing
  role + first line of each folded message. It is deliberately *not* a
  summary: anything that matters is a pin, and pins are not in the digest.

## Disclosure (what Anchor touches)

- **Network:** none. **Model calls:** none. **Subprocesses:** none.
- **Reads:** the conversation messages the host hands the engine (its
  documented job), and its own pin store.
- **Writes:** only its own pin store
  (`$HERMES_HOME/plugin-data/anchor/pins.json`).
- **Credentials:** none requested, none read.
- Token-saved figures are **estimates** (~4 chars/token), labelled as such
  everywhere they appear; they never gate behaviour.

## Tests

```bash
python -m pytest tests/ -q   # 44 tests
```

Covering: pin detection (kinds, verbatim, de-dup), the store (persistence,
idempotency, removal), threshold behaviour, and the core promise — pins
survive 100 compression passes byte-for-byte, exactly one carrier, head
and tail untouched, focus-topic protection, no re-digestion, tools and the
`/anchor` command.

Admission self-check, run against this tree with Hermes's own validator
(the same `validate_plugin_dir` the catalog CI runs): **15/15 checks pass,
0 warnings**, security scan *safe*, isolation verdict *host-ready*.

## Layout

```
__init__.py    exports AnchorEngine + register() (ctx.register_context_engine)
engine.py      the ContextEngine: lifecycle, compression, tools, /anchor command
pins.py        deterministic pin detection + thread-safe JSON pin store
digest.py      pin carrier + capped extractive digest builders
plugin.yaml    manifest (name must match the directory: anchor)
tests/         pytest suite
```

## License

MIT
