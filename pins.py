"""Anchor pin detection and storage.

A *pin* is a short fact that must survive context compression verbatim:
a decision, an amount of money, a date/deadline, a contact. Detection is
fully deterministic (regex + sentence markers) — Anchor makes zero model
calls anywhere, so the same conversation always produces the same pins.

Storage is a small JSON file guarded by a lock: compress() may run on a
pooled daemon thread while tools run on the conversation thread.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------

# Explicit human/agent markers: a sentence that *announces* a fact.
_MARKER_RE = re.compile(
    r"(?i)\b(decision|decided|agreed|approved|final(?:ised|ized)?|deadline|"
    r"remember|note|pinned?|confirmed|locked(?:\s+in)?)\b\s*[:\-—]"
)

# Money: a currency symbol/code followed by an amount (or the reverse).
_MONEY_RE = re.compile(
    r"(?:[₹$€£]|Rs\.?|INR|USD|EUR|GBP)\s?\d[\d,]*\.?\d*"
    r"|\d[\d,]*\.?\d*\s?(?:₹|Rs\.?|INR|USD|EUR|GBP|dollars?|rupees?)",
    re.IGNORECASE,
)

# Dates and deadlines: ISO dates, d Month yyyy, Month d, weekday deadlines.
_DATE_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}\b"
    r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?\b"
    r"|\bby\s+(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day\b",
    re.IGNORECASE,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")

MAX_PIN_CHARS = 500


def pin_key(text: str) -> str:
    """Stable id for a pin text (content-addressed)."""
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:16]


def classify_sentence(sentence: str) -> str | None:
    """Return the pin kind for a sentence, or None if it is not a pin.

    Kinds: ``decision`` (explicit marker), ``money``, ``date``.
    A marker sentence wins over the looser money/date patterns, and a
    marker alone is enough — "Decision: we ship on Friday" is a pin even
    though it carries no amount or formal date.
    """
    s = sentence.strip()
    if not s or len(s) < 4:
        return None
    if _MARKER_RE.search(s):
        return "decision"
    if _MONEY_RE.search(s):
        return "money"
    if _DATE_RE.search(s):
        return "date"
    return None


def detect_pins(text: str) -> list[dict]:
    """Extract pins from a block of text. Deterministic; no model calls.

    Returns a list of ``{"text": ..., "kind": ...}`` with the sentence kept
    *verbatim* (stripped of surrounding whitespace only), de-duplicated in
    first-seen order.
    """
    if not text:
        return []
    found: list[dict] = []
    seen: set[str] = set()
    for raw in _SENTENCE_SPLIT_RE.split(text):
        sentence = raw.strip()
        if not sentence or len(sentence) > MAX_PIN_CHARS:
            continue
        kind = classify_sentence(sentence)
        if kind is None:
            continue
        key = pin_key(sentence)
        if key in seen:
            continue
        seen.add(key)
        found.append({"text": sentence, "kind": kind})
    return found


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------


def default_store_path() -> Path:
    """Where pins live: $HERMES_HOME/plugin-data/anchor/pins.json.

    Falls back to ~/.hermes when HERMES_HOME is unset. Pins are keyed per
    store file; the engine resolves the active profile's home at load.
    """
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(home) / "plugin-data" / "anchor" / "pins.json"


class PinStore:
    """Thread-safe JSON pin store.

    Each pin: ``{id, text, kind, source, created_at}`` where source is
    ``auto`` (detected), ``manual`` (anchor_pin tool) or ``focus``.
    Content-addressed ids make re-detection idempotent: scanning the same
    conversation twice never duplicates a pin.
    """

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else default_store_path()
        self._lock = threading.Lock()
        self._pins: dict[str, dict] = {}
        self._load()

    # -- persistence -------------------------------------------------------
    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._pins = {k: v for k, v in data.items() if isinstance(v, dict)}
        except (OSError, ValueError):
            self._pins = {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._pins, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            # Compression must never fail because a pin could not persist;
            # the in-memory copy still protects this session.
            pass

    def reload(self) -> None:
        """Re-read the store file (another process may have written)."""
        with self._lock:
            self._load()

    # -- operations --------------------------------------------------------
    def add(self, text: str, kind: str = "manual", source: str = "manual") -> dict:
        text = text.strip()[:MAX_PIN_CHARS]
        pin = {
            "id": pin_key(text),
            "text": text,
            "kind": kind,
            "source": source,
            "created_at": time.time(),
        }
        with self._lock:
            existing = self._pins.get(pin["id"])
            if existing is not None:
                return existing
            self._pins[pin["id"]] = pin
            self._save()
            return pin

    def add_many(self, pins: list[dict], source: str = "auto") -> int:
        added = 0
        for p in pins:
            before = len(self._pins)
            self.add(p["text"], kind=p.get("kind", "manual"), source=source)
            if len(self._pins) > before:
                added += 1
        return added

    def remove(self, pin_id_or_text: str) -> bool:
        key = pin_id_or_text if pin_id_or_text in self._pins else pin_key(pin_id_or_text)
        with self._lock:
            if key in self._pins:
                del self._pins[key]
                self._save()
                return True
            return False

    def list(self) -> list[dict]:
        with self._lock:
            return sorted(self._pins.values(), key=lambda p: p.get("created_at", 0))

    def __len__(self) -> int:
        return len(self._pins)
