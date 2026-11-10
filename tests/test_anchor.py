import json

import pytest

from anchor.digest import PIN_CARRIER_MARKER, is_anchor_row, message_text
from anchor.pins import PinStore, classify_sentence, detect_pins
from conftest import make_messages

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sentence,kind",
    [
        ("Decision: we launch on Friday.", "decision"),
        ("Decided: the price stays at 49.", "decision"),
        ("Approved: vendor is Acme Corp.", "decision"),
        ("Deadline: submit by Monday.", "decision"),
        ("Remember: the client hates email.", "decision"),
        ("The budget is $4,200 for the venue.", "money"),
        ("It costs ₹18,000 in total.", "money"),
        ("Pay 500 dollars upfront.", "money"),
        ("We meet on 2026-11-20 to review.", "date"),
        ("The launch is on 20 Nov 2026.", "date"),
        ("Send it by Friday.", "date"),
    ],
)
def test_classify_pins(sentence, kind):
    assert classify_sentence(sentence) == kind


@pytest.mark.parametrize(
    "sentence",
    [
        "I think maybe we could try something new.",
        "Hello there, how are you today?",
        "The weather is nice this week.",
        "",
        "ok",
    ],
)
def test_classify_non_pins(sentence):
    assert classify_sentence(sentence) is None


def test_detect_pins_verbatim_and_dedup():
    text = "Filler talk. Decision: price is $49. More talk. Decision: price is $49."
    pins = detect_pins(text)
    assert len(pins) == 1
    assert pins[0]["text"] == "Decision: price is $49."


def test_detect_pins_multiple_kinds():
    pins = detect_pins("Budget is $900. We ship on 2026-12-01. Just chatting here.")
    kinds = {p["kind"] for p in pins}
    assert kinds == {"money", "date"}


def test_detect_pins_empty():
    assert detect_pins("") == []
    assert detect_pins("nothing factual here at all") == []


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def test_store_add_and_list(store_path):
    store = PinStore(store_path)
    pin = store.add("Decision: ship it.", kind="decision")
    assert pin["id"]
    assert len(store) == 1
    assert store.list()[0]["text"] == "Decision: ship it."


def test_store_add_idempotent(store_path):
    store = PinStore(store_path)
    store.add("Decision: ship it.")
    store.add("Decision: ship it.")
    assert len(store) == 1


def test_store_persists_across_instances(store_path):
    PinStore(store_path).add("Budget is $10.")
    again = PinStore(store_path)
    assert len(again) == 1
    assert again.list()[0]["text"] == "Budget is $10."


def test_store_remove_by_text_and_id(store_path):
    store = PinStore(store_path)
    pin = store.add("Decision: ship it.")
    assert store.remove(pin["id"]) is True
    store.add("Decision: ship it.")
    assert store.remove("Decision: ship it.") is True
    assert store.remove("Decision: ship it.") is False
    assert len(store) == 0


# ---------------------------------------------------------------------------
# Engine basics
# ---------------------------------------------------------------------------


def test_engine_name(engine):
    assert engine.name == "anchor"


def test_should_compress_threshold(engine):
    # context_length=1000, threshold 75% -> fires at 750
    assert engine.should_compress(749) is False
    assert engine.should_compress(750) is True
    assert engine.should_compress(2000) is True


def test_should_compress_no_context_length(store_path):
    from anchor.engine import AnchorEngine

    e = AnchorEngine(context_length=0, store_path=store_path)
    assert e.should_compress(10_000) is False


def test_update_from_response(engine):
    engine.update_from_response({"prompt_tokens": 800, "completion_tokens": 50, "total_tokens": 850})
    assert engine.last_prompt_tokens == 800
    assert engine.last_total_tokens == 850
    assert engine.should_compress() is True


def test_update_model_sets_threshold(engine):
    engine.update_model(context_length=2000)
    assert engine.threshold_tokens == 1500


# ---------------------------------------------------------------------------
# Compression — the core promise
# ---------------------------------------------------------------------------


def all_text(messages):
    return "\n".join(message_text(m) for m in messages)


def test_pins_survive_verbatim(engine):
    out = engine.compress(make_messages())
    text = all_text(out)
    assert "Decision: we launch on 20 Nov 2026." in text
    assert "The budget is $4,200 for the venue." in text


def test_carrier_present_exactly_once(engine):
    out = engine.compress(make_messages())
    carriers = [m for m in out if message_text(m).startswith(PIN_CARRIER_MARKER)]
    assert len(carriers) == 1


def test_compression_shrinks_context(engine):
    msgs = make_messages(60)
    out = engine.compress(msgs)
    before = sum(len(message_text(m)) for m in msgs)
    after = sum(len(message_text(m)) for m in out)
    assert after < before
    assert engine.compression_count == 1
    assert engine.tokens_saved_total > 0


def test_head_and_tail_protected(engine):
    msgs = make_messages()
    out = engine.compress(msgs)
    # system prompt still first, last user question still last, verbatim
    assert out[0]["content"] == "You are Hermes."
    assert out[-1]["content"] == "What did we decide?"


def test_pins_survive_100_compressions(engine):
    out = engine.compress(make_messages())
    for _ in range(99):
        out = engine.compress(out)
    text = all_text(out)
    assert "Decision: we launch on 20 Nov 2026." in text
    assert "The budget is $4,200 for the venue." in text
    carriers = [m for m in out if message_text(m).startswith(PIN_CARRIER_MARKER)]
    assert len(carriers) == 1


def test_second_pass_does_not_redigest_digest(engine):
    out1 = engine.compress(make_messages())
    digests1 = [message_text(m) for m in out1 if message_text(m).startswith("[Anchor digest")]
    out2 = engine.compress(out1)
    digests2 = [message_text(m) for m in out2 if message_text(m).startswith("[Anchor digest")]
    assert digests1 == digests2


def test_manual_pin_appears_in_carrier(engine):
    engine.store.add("Final: the venue is Marina Hall.", kind="decision", source="manual")
    out = engine.compress(make_messages())
    assert "Final: the venue is Marina Hall." in all_text(out)


def test_unpinned_fact_can_leave_but_pin_stays(engine):
    engine.store.add("Approved: spend up to $700 on ads.", kind="decision", source="manual")
    out = engine.compress(make_messages())
    assert engine.store.remove("Approved: spend up to $700 on ads.") is True
    out2 = engine.compress(out)
    assert "Approved: spend up to $700 on ads." not in all_text(out2)


def test_focus_topic_protects_matching_message(engine):
    msgs = make_messages()
    msgs.insert(10, {"role": "user", "content": "The zephyr prototype needs a blue casing."})
    out = engine.compress(msgs, focus_topic="zephyr")
    assert "The zephyr prototype needs a blue casing." in [
        message_text(m) for m in out if not is_anchor_row(m)
    ]


def test_compress_empty_and_short(engine):
    assert engine.compress([]) == []
    short = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "Decision: keep it short."},
    ]
    out = engine.compress(short)
    assert "Decision: keep it short." in all_text(out)


def test_status_reports_zero_model_calls(engine):
    engine.compress(make_messages())
    status = engine.get_status()
    assert status["model_calls_made"] == 0
    assert status["pins_active"] >= 2
    assert status["compression_count"] == 1


def test_clone_shares_store(engine):
    engine.store.add("Decision: cloned engines keep pins.")
    clone = engine.clone_for_agent()
    assert clone.store.list()[0]["text"] == "Decision: cloned engines keep pins."
    assert clone.name == "anchor"


# ---------------------------------------------------------------------------
# Engine tools + slash command
# ---------------------------------------------------------------------------


def test_tool_pin_list_unpin(engine):
    res = json.loads(engine.handle_tool_call("anchor_pin", {"text": "Deadline: Friday 5pm."}))
    assert res["pinned"]["text"] == "Deadline: Friday 5pm."
    res = json.loads(engine.handle_tool_call("anchor_pins", {}))
    assert len(res["pins"]) == 1
    res = json.loads(engine.handle_tool_call("anchor_unpin", {"pin": "Deadline: Friday 5pm."}))
    assert res["removed"] is True


def test_tool_unknown_and_empty(engine):
    assert "error" in json.loads(engine.handle_tool_call("nope", {}))
    assert "error" in json.loads(engine.handle_tool_call("anchor_pin", {"text": "  "}))


def test_tool_schemas_match_handlers(engine):
    names = [s["name"] for s in engine.get_tool_schemas()]
    assert names == ["anchor_pin", "anchor_unpin", "anchor_pins", "anchor_status"]
    for n in names:
        out = engine.handle_tool_call(n, {"text": "Decision: schema test."} if n == "anchor_pin" else {"pin": "x"} if n == "anchor_unpin" else {})
        assert isinstance(out, str)


def test_slash_command_roundtrip(store_path, monkeypatch):
    import anchor.engine as eng

    monkeypatch.setattr(eng, "_command_engine", None)
    monkeypatch.setenv("HERMES_HOME", str(store_path.parent))
    out = eng._handle_anchor_command("pin Decision: command pins work.")
    assert "Pinned" in out
    out = eng._handle_anchor_command("pins")
    assert "command pins work" in out
    out = eng._handle_anchor_command("status")
    assert "model calls made: 0" in out
