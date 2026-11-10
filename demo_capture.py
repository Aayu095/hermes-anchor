"""Drive the REAL Anchor engine through Hermes's own loader with a
scripted session and capture every output the demo video shows.

Run with the hermes-agent source on PYTHONPATH and HERMES_HOME set to a
home whose plugins/ dir contains this plugin. Outputs demo_capture.json.
Nothing in the video is invented: every number and line comes from here.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # not used by loader; harmless

from plugins.context_engine import load_context_engine  # noqa: E402

LONG = (
    "Tool output — market_research.md (excerpt): The venue market in the city "
    "grew 14% year over year. Shortlist criteria include capacity for 300 guests, "
    "a stage for the keynote, breakout rooms, catering partnerships, parking for "
    "80 cars, and load-in access for the demo booths. Three venues declined due "
    "to date conflicts. The report recommends deciding before the end of the "
    "month because prices rise once wedding season bookings open. Marketing "
    "channels ranked: community posts first, partner newsletters second, paid "
    "social third. Expected attendance modelling suggests 220-260 attendees. "
)


def main() -> None:
    engine = load_context_engine("anchor")
    assert engine is not None, "engine failed to load"
    engine.update_model(context_length=200_000)

    convo = [
        {"role": "system", "content": "You are Hermes, a helpful agent."},
        {"role": "user", "content": "We are planning the product launch. Decision: we launch on 20 Nov 2026."},
        {"role": "assistant", "content": "Noted — building the plan around 20 Nov 2026."},
        {"role": "user", "content": "The budget is $4,200 for the venue, not a cent more."},
        {"role": "assistant", "content": "Got it, $4,200 is the ceiling."},
        {"role": "user", "content": "Final: the venue is Marina Hall."},
        {"role": "assistant", "content": "Marina Hall it is. Let me pull the research."},
    ]
    for i in range(14):
        role = "user" if i % 2 == 0 else "assistant"
        convo.append({"role": role, "content": LONG + f" (part {i + 1})"})
    convo.append({"role": "user", "content": "What did we decide about the launch?"})

    def chars(msgs):
        return sum(len(str(m.get("content", ""))) for m in msgs)

    capture = {"pins_after_seed": [], "passes": []}
    engine.on_turn_complete(convo)
    pins = json.loads(engine.handle_tool_call("anchor_pins", {}))["pins"]
    capture["pins_after_seed"] = [p["text"] for p in pins]

    cur = convo
    for n in (1, 2, 3):
        before = chars(cur)
        cur = engine.compress(cur)
        capture["passes"].append(
            {"n": n, "before_chars": before, "after_chars": chars(cur), "messages": len(cur)}
        )

    text = "\n".join(str(m.get("content", "")) for m in cur)
    facts = [
        "Decision: we launch on 20 Nov 2026.",
        "The budget is $4,200 for the venue, not a cent more.",
        "Final: the venue is Marina Hall.",
    ]
    capture["facts_verbatim"] = {f: (f in text) for f in facts}
    capture["status"] = engine.get_status()
    carrier = next(
        (str(m.get("content", "")) for m in cur if str(m.get("content", "")).startswith("[Anchor — pinned")),
        "",
    )
    capture["carrier"] = carrier
    out = Path(__file__).with_name("demo_capture.json")
    out.write_text(json.dumps(capture, indent=1), encoding="utf-8")
    print(json.dumps(capture, indent=1))


if __name__ == "__main__":
    main()
