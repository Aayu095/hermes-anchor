"""Anchor's deterministic digest for messages that are *not* pinned.

The built-in compressor spends a model call summarising the middle of a
conversation — and still loses facts. Anchor spends nothing: messages
that carry no pin are replaced by an extractive digest (role + first
line of each), which is honest about being a map, not a summary. The
facts themselves live in the pin carrier, verbatim.
"""

from __future__ import annotations

import hashlib

DIGEST_MARKER = "[Anchor digest"
PIN_CARRIER_MARKER = "[Anchor — pinned facts, kept verbatim]"

_DIGEST_LINE_CHARS = 96
# The digest is a map, not an archive: its total size is hard-capped no
# matter how large the folded run is. Facts are never lost to this cap —
# anything that matters is a pin, and pins live outside the digest.
DIGEST_MAX_CHARS = 1200


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token). Labelled an estimate
    everywhere it is surfaced; used only for stats, never for gating."""
    return max(1, len(text) // 4) if text else 0


def message_text(message: dict) -> str:
    """Plain-text view of a message's content (str or part list)."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "\n".join(parts)
    return ""


def is_anchor_row(message: dict) -> bool:
    """True for rows Anchor itself authored (carrier or digest). Anchor
    never re-digests or re-pins its own rows — that is what makes a second
    compression pass idempotent."""
    text = message_text(message).lstrip()
    return text.startswith(DIGEST_MARKER) or text.startswith(PIN_CARRIER_MARKER)


def digest_uid(messages: list[dict]) -> str:
    h = hashlib.sha1()
    for m in messages:
        h.update(message_text(m).encode("utf-8"))
    return "anchor-digest-" + h.hexdigest()[:24]


def build_digest(messages: list[dict]) -> dict:
    """Fold a run of unpinned messages into one digest message."""
    lines = [
        f"{DIGEST_MARKER} — {len(messages)} message(s) compressed by Anchor "
        f"(deterministic, zero model calls). Pinned facts are kept separately, verbatim.]"
    ]
    used = len(lines[0])
    shown = 0
    for m in messages:
        text = " ".join(message_text(m).split())
        if not text:
            continue
        if len(text) > _DIGEST_LINE_CHARS:
            text = text[: _DIGEST_LINE_CHARS - 1] + "…"
        line = f"- ({m.get('role', '?')}) {text}"
        if shown and used + len(line) > DIGEST_MAX_CHARS:
            lines.append(f"- …and {len(messages) - shown} more message(s), folded under the same cap.")
            break
        lines.append(line)
        used += len(line)
        shown += 1
    return {
        "role": "user",
        "content": "\n".join(lines),
        "message_uid": digest_uid(messages),
    }


def build_pin_carrier(pins: list[dict], focus_topic: str | None = None) -> dict:
    """The carrier message that holds every pin verbatim.

    Placed immediately after the system/head block on every compression.
    Rebuilt from the store each pass and stamped with a content uid, so
    pins appear exactly once and never drift by a single character.
    """
    ordered = list(pins)
    if focus_topic:
        needle = focus_topic.lower()
        ordered.sort(key=lambda p: 0 if needle in p["text"].lower() else 1)
    lines = [
        PIN_CARRIER_MARKER,
        "These facts were pinned (by the user, the agent, or Anchor's "
        "detector) and are preserved exactly as written. Treat them as "
        "current, agreed facts:",
    ]
    for p in ordered:
        lines.append(f"- [{p['kind']}] {p['text']}")
    h = hashlib.sha1("\n".join(lines).encode("utf-8")).hexdigest()[:24]
    return {"role": "user", "content": "\n".join(lines), "message_uid": f"anchor-pins-{h}"}
