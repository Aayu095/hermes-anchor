import sys
from pathlib import Path

# The repo root IS the plugin package (directory name = package name).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest


@pytest.fixture()
def store_path(tmp_path):
    return tmp_path / "pins.json"


@pytest.fixture()
def engine(store_path):
    from anchor.engine import AnchorEngine

    return AnchorEngine(context_length=1000, store_path=store_path)


def make_messages(n_middle=30):
    msgs = [{"role": "system", "content": "You are Hermes."}]
    msgs.append({"role": "user", "content": "Decision: we launch on 20 Nov 2026."})
    msgs.append({"role": "assistant", "content": "Noted, planning around it."})
    for i in range(n_middle):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"Filler discussion line number {i} about nothing important at all, just talk."})
    msgs.append({"role": "user", "content": "The budget is $4,200 for the venue."})
    msgs.append({"role": "assistant", "content": "Got it, I will keep costs under that."})
    msgs.append({"role": "user", "content": "What did we decide?"})
    return msgs
