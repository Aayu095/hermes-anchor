"""Anchor — a context engine that pins facts before it compresses.

The built-in compressor summarises *everything* with a model call, so a
decision made an hour ago can come back paraphrased, rounded, or gone.
Anchor runs the opposite policy:

1. **Pin first.** Decisions, amounts, dates and deadlines are detected
   deterministically (zero model calls) and kept in a pin store.
2. **Compress second.** On compression, every pin is re-issued verbatim
   in a carrier message, head and tail stay untouched, and the unpinned
   middle folds into an honest extractive digest.
3. **Prove it.** Pins survive any number of compression passes
   byte-for-byte; the test-suite compresses the same conversation 100
   times and diffs them.

Anchor makes no model calls, no network requests, and reads nothing
outside its own pin store.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

try:  # Inside Hermes the real ABC is present and required.
    from agent.context_engine import ContextEngine
except ImportError:  # Standalone (tests, tooling): minimal local contract.
    from abc import ABC

    class ContextEngine(ABC):  # type: ignore[no-redef]
        threshold_percent = 0.75
        protect_first_n = 3
        protect_last_n = 6
        emit_automatic_compaction_status = True
        last_prompt_tokens = 0
        last_completion_tokens = 0
        last_total_tokens = 0
        threshold_tokens = 0
        context_length = 0
        compression_count = 0

        def update_model(self, model=None, context_length=0, **kwargs):
            if context_length:
                self.context_length = context_length
            self.threshold_tokens = int(self.context_length * self.threshold_percent)

        def on_session_start(self, session_id, **kwargs):
            pass

        def on_session_end(self, session_id, messages):
            pass

        def on_session_reset(self):
            self.last_prompt_tokens = 0
            self.last_completion_tokens = 0
            self.last_total_tokens = 0

        def get_status(self):
            return {
                "name": self.name,
                "last_prompt_tokens": self.last_prompt_tokens,
                "last_completion_tokens": self.last_completion_tokens,
                "last_total_tokens": self.last_total_tokens,
                "threshold_tokens": self.threshold_tokens,
                "context_length": self.context_length,
                "compression_count": self.compression_count,
            }


try:
    from .digest import build_digest, build_pin_carrier, estimate_tokens, is_anchor_row, message_text
    from .pins import PinStore, detect_pins
except ImportError:  # Loaded as a flat module (plugin host isolation).
    from digest import build_digest, build_pin_carrier, estimate_tokens, is_anchor_row, message_text
    from pins import PinStore, detect_pins


class AnchorEngine(ContextEngine):
    """Context engine: pins verbatim, digests the rest, zero model calls."""

    def __init__(self, context_length: int = 0, store_path: Path | str | None = None):
        self.context_length = context_length
        self.threshold_tokens = int(context_length * self.threshold_percent) if context_length else 0
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self.compression_count = 0
        self.tokens_saved_total = 0
        self.store = PinStore(store_path)
        self._session_id = ""

    @property
    def name(self) -> str:
        return "anchor"

    # -- lifecycle ---------------------------------------------------------
    def on_session_start(self, session_id, **kwargs):
        self._session_id = session_id or ""
        self.store.reload()

    def on_session_end(self, session_id, messages):
        if messages:
            self._scan(messages)

    def on_session_reset(self):
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0

    def on_turn_complete(self, messages, usage=None, **kwargs):
        """Best-effort observation: learn pins as turns finish, so they
        are already stored before the first compression ever fires."""
        if messages:
            self._scan(messages)

    def clone_for_agent(self):
        """Fresh engine per agent, sharing the durable pin store file
        (the store itself is lock-guarded and re-read on session start)."""
        clone = AnchorEngine(context_length=self.context_length, store_path=self.store.path)
        clone.threshold_percent = self.threshold_percent
        clone.tokens_saved_total = self.tokens_saved_total
        return clone

    # -- token tracking ------------------------------------------------------
    def update_from_response(self, usage: dict) -> None:
        if not usage:
            return
        self.last_prompt_tokens = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
        self.last_completion_tokens = int(
            usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
        )
        self.last_total_tokens = int(
            usage.get("total_tokens", self.last_prompt_tokens + self.last_completion_tokens) or 0
        )

    def update_model(self, model=None, context_length=0, **kwargs):
        if context_length:
            self.context_length = int(context_length)
        self.threshold_tokens = int(self.context_length * self.threshold_percent)

    def should_compress(self, prompt_tokens: int | None = None) -> bool:
        if not self.context_length:
            return False
        tokens = self.last_prompt_tokens if prompt_tokens is None else prompt_tokens
        return tokens >= int(self.context_length * self.threshold_percent)

    def should_compress_preflight(self, messages) -> bool:
        approx = sum(estimate_tokens(message_text(m)) for m in messages)
        return self.should_compress(approx)

    # -- pin scanning ---------------------------------------------------------
    def _scan(self, messages: list[dict]) -> int:
        added = 0
        for m in messages:
            if is_anchor_row(m):
                continue
            added += self.store.add_many(detect_pins(message_text(m)), source="auto")
        return added

    # -- compression -----------------------------------------------------------
    def compress(
        self,
        messages: list,
        current_tokens: int | None = None,
        focus_topic: str | None = None,
        force: bool = False,
        memory_context: str = "",
    ) -> list:
        if not messages:
            return messages

        # 1. Learn every pin in the incoming history (idempotent).
        self._scan(messages)

        # 2. Partition: protected rows stay verbatim; the rest may fold.
        non_system = [i for i, m in enumerate(messages) if m.get("role") != "system"]
        protected: set[int] = {i for i, m in enumerate(messages) if m.get("role") == "system"}
        protected.update(non_system[: self.protect_first_n])
        protected.update(non_system[-self.protect_last_n :] if self.protect_last_n else [])
        had_carrier = False
        for i, m in enumerate(messages):
            if is_anchor_row(m):
                if message_text(m).lstrip().startswith("[Anchor digest"):
                    protected.add(i)  # never re-digest our own digest
                else:
                    had_carrier = True  # old carrier: dropped, rebuilt below
                continue
            if focus_topic and focus_topic.lower() in message_text(m).lower():
                protected.add(i)

        out: list[dict] = []
        run: list[dict] = []
        digested = 0

        def flush():
            nonlocal digested
            if not run:
                return
            if len(run) == 1:
                out.append(run[0])
            else:
                out.append(build_digest(run))
                digested += len(run)
            run.clear()

        for i, m in enumerate(messages):
            if is_anchor_row(m) and not message_text(m).lstrip().startswith("[Anchor digest"):
                continue  # old carrier row — replaced by the fresh one
            if i in protected:
                flush()
                out.append(m)
            else:
                run.append(m)
        flush()

        pins = self.store.list()
        if pins:
            carrier = build_pin_carrier(pins, focus_topic)
            insert_at = 0
            while insert_at < len(out) and out[insert_at].get("role") == "system":
                insert_at += 1
            out.insert(insert_at, carrier)
        elif digested == 0:
            return list(messages)  # nothing to do; leave the list untouched

        if digested == 0 and had_carrier:
            # Only the carrier was refreshed — that is still a pass, but
            # do not claim savings or churn counters for it.
            return out
        if digested == 0:
            return out

        before = current_tokens or sum(estimate_tokens(message_text(m)) for m in messages)
        after = sum(estimate_tokens(message_text(m)) for m in out)
        self.tokens_saved_total += max(0, before - after)
        self.compression_count += 1
        return out

    # -- status -----------------------------------------------------------------
    def get_status(self) -> dict:
        return {
            "name": self.name,
            "last_prompt_tokens": self.last_prompt_tokens,
            "last_completion_tokens": self.last_completion_tokens,
            "last_total_tokens": self.last_total_tokens,
            "threshold_tokens": self.threshold_tokens,
            "context_length": self.context_length,
            "compression_count": self.compression_count,
            "pins_active": len(self.store),
            "tokens_saved_total_est": self.tokens_saved_total,
            "model_calls_made": 0,
        }

    # -- engine tools -------------------------------------------------------------
    def get_tool_schemas(self) -> list:
        return [
            {
                "name": "anchor_pin",
                "description": "Pin a fact so Anchor's context compression preserves it verbatim forever (decisions, amounts, dates, names). Use when something is agreed or must not drift.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "The fact, exactly as it should be preserved."},
                        "kind": {"type": "string", "description": "decision | money | date | fact", "default": "fact"},
                    },
                    "required": ["text"],
                },
            },
            {
                "name": "anchor_unpin",
                "description": "Remove a pin by its id or exact text.",
                "parameters": {
                    "type": "object",
                    "properties": {"pin": {"type": "string"}},
                    "required": ["pin"],
                },
            },
            {
                "name": "anchor_pins",
                "description": "List all active Anchor pins (verbatim facts protected from compression).",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "anchor_status",
                "description": "Anchor status: active pins, compressions run, estimated tokens saved, model calls made (always 0).",
                "parameters": {"type": "object", "properties": {}},
            },
        ]

    def handle_tool_call(self, name: str, args: dict, **kwargs) -> str:
        self.store.reload()  # another agent clone may have written pins
        args = args or {}
        if name == "anchor_pin":
            text = (args.get("text") or "").strip()
            if not text:
                return json.dumps({"error": "anchor_pin needs a non-empty 'text'"})
            pin = self.store.add(text, kind=args.get("kind") or "fact", source="manual")
            return json.dumps({"pinned": pin})
        if name == "anchor_unpin":
            target = (args.get("pin") or "").strip()
            return json.dumps({"removed": self.store.remove(target)})
        if name == "anchor_pins":
            return json.dumps({"pins": self.store.list()})
        if name == "anchor_status":
            return json.dumps(self.get_status())
        return json.dumps({"error": f"Unknown tool: {name}"})


# Module-level engine shared by the /anchor slash command. Pins live in
# the store file, so the command works even in a different process than
# the engine that serves the conversation.
_command_engine: Optional[AnchorEngine] = None


def _get_command_engine() -> AnchorEngine:
    global _command_engine
    if _command_engine is None:
        _command_engine = AnchorEngine()
    _command_engine.store.reload()
    return _command_engine


def _handle_anchor_command(raw_args: str) -> str:
    engine = _get_command_engine()
    parts = (raw_args or "").strip().split(None, 1)
    sub = parts[0].lower() if parts else "status"
    rest = parts[1] if len(parts) > 1 else ""
    if sub == "pin" and rest:
        pin = engine.store.add(rest, kind="fact", source="manual")
        return f"📌 Pinned [{pin['id']}]: {pin['text']}"
    if sub == "unpin" and rest:
        return "Pin removed." if engine.store.remove(rest) else "No such pin."
    if sub in ("list", "pins"):
        pins = engine.store.list()
        if not pins:
            return "No Anchor pins yet. Pin one with /anchor pin <fact>."
        return "\n".join(f"📌 [{p['kind']}] {p['text']}" for p in pins)
    if sub in ("status", ""):
        s = engine.get_status()
        return (
            f"⚓ Anchor: {s['pins_active']} pin(s) protected · "
            f"{s['compression_count']} compression(s) · "
            f"~{s['tokens_saved_total_est']} tokens saved (est.) · "
            f"model calls made: 0"
        )
    return "Usage: /anchor [status|pins|pin <fact>|unpin <id or fact>]"


def register(ctx) -> None:
    """General-plugin + context-engine-directory entry point."""
    engine = AnchorEngine()
    ctx.register_context_engine(engine)
    # Also register the tools in the general registry when the host
    # offers it: agent init de-duplicates them against the engine's own
    # schemas ("already registered via plugin/cache path"), so the model
    # sees each tool exactly once, and plugin.yaml's provides_tools is
    # literally true on both surfaces.
    for schema in engine.get_tool_schemas():
        try:
            ctx.register_tool(
                name=schema["name"],
                toolset="anchor",
                schema=schema,
                handler=lambda args, _n=schema["name"], **kw: engine.handle_tool_call(_n, args or {}),
                description=schema["description"],
            )
        except Exception:
            pass  # engine-directory collector has no register_tool; engine tools still apply
    try:
        ctx.register_command(
            "anchor",
            handler=_handle_anchor_command,
            description="Anchor pins: facts that survive context compression verbatim.",
            args_hint="[status|pins|pin <fact>|unpin <id>]",
        )
    except Exception:
        # The engine-directory collector forwards register_command; a
        # minimal ctx (tests, older hosts) may not offer it — the engine
        # itself is unaffected.
        pass
