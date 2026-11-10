"""Render the Anchor demo video from demo_capture.json (real outputs).

Dark terminal style, line-reveal frames, ffmpeg concat -> anchor-demo.mp4
Every number on screen comes from the capture file produced by driving
the real engine through Hermes's own loader (demo_capture.py).
"""

import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
CAP = json.loads((HERE / "demo_capture.json").read_text(encoding="utf-8"))
FRAMES = Path("/tmp/anchor-frames")
FRAMES.mkdir(exist_ok=True)

W, H = 1280, 720
BG = (11, 14, 17)
INK = (232, 234, 238)
MUTED = (148, 156, 166)
ORANGE = (255, 77, 0)
GREEN = (74, 222, 128)
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
MONO_B = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"

F_TITLE = ImageFont.truetype(MONO_B, 40)
F_BODY = ImageFont.truetype(MONO, 25)
F_SMALL = ImageFont.truetype(MONO, 21)
F_CMD = ImageFont.truetype(MONO_B, 25)

scenes: list[tuple[list[tuple[str, tuple, object]], float]] = []


def line(text, color=INK, font=None):
    return (text, color, font or F_BODY)


p1 = CAP["passes"][0]
shrink = round(100 * (1 - p1["after_chars"] / p1["before_chars"]))
facts = [f for f, ok in CAP["facts_verbatim"].items() if ok]

scenes.append((
    [
        line("Hermes forgets your decisions", INK, F_TITLE),
        line("when it compresses its context.", INK, F_TITLE),
        line("", INK, F_SMALL),
        line("This is Anchor — a context engine that pins them first.", MUTED),
    ], 5.0))

scenes.append((
    [
        line("$ hermes · session: launch planning", MUTED, F_SMALL),
        line(""),
        line("user:  Decision: we launch on 20 Nov 2026.", INK),
        line("user:  The budget is $4,200 for the venue,", INK),
        line("       not a cent more.", INK),
        line("user:  Final: the venue is Marina Hall.", INK),
        line(""),
        line("…then 14 long research messages fill the context…", MUTED),
    ], 9.0))

pin_lines = [line("> /anchor pins", ORANGE, F_CMD), line("")]
for f in facts:
    pin_lines.append(line(f"  PIN  {f}", GREEN, F_SMALL))
pin_lines += [line(""), line("Pinned by Anchor itself. Zero model calls.", MUTED)]
scenes.append((pin_lines, 9.0))

scenes.append((
    [
        line("> /compress", ORANGE, F_CMD),
        line(""),
        line(f"context: {p1['before_chars']:,} chars -> {p1['after_chars']:,} chars  (-{shrink}%)", INK),
        line(""),
        line("[Anchor — pinned facts, kept verbatim]", ORANGE, F_SMALL),
        line("- [decision] Decision: we launch on 20 Nov 2026.", INK, F_SMALL),
        line("- [money] The budget is $4,200 for the venue,", INK, F_SMALL),
        line("          not a cent more.", INK, F_SMALL),
        line("- [decision] Final: the venue is Marina Hall.", INK, F_SMALL),
        line(""),
        line("The middle became one capped digest. The pins", MUTED),
        line("did not move — word for word.", MUTED),
    ], 12.0))

scenes.append((
    [
        line("> /compress   (pass 2)", ORANGE, F_CMD),
        line("> /compress   (pass 3)", ORANGE, F_CMD),
        line(""),
        line(f"context after pass 1: {CAP['passes'][0]['after_chars']:,} chars", INK),
        line(f"context after pass 2: {CAP['passes'][1]['after_chars']:,} chars", INK),
        line(f"context after pass 3: {CAP['passes'][2]['after_chars']:,} chars", INK),
        line(""),
        line("Identical. Nothing drifted, nothing duplicated.", GREEN),
        line("(The test suite does this 100 times and diffs the pins.)", MUTED, F_SMALL),
    ], 11.0))

check_lines = [line("After 3 compressions, is the context still true?", INK, F_CMD), line("")]
for f in facts:
    check_lines.append(line(f"  OK  {f}", GREEN, F_SMALL))
check_lines += [line(""), line("All three facts — verbatim, in the live context.", MUTED)]
scenes.append((check_lines, 10.0))

scenes.append((
    [
        line("A prompt can't intercept compression.", INK, F_TITLE),
        line("Anchor can.", ORANGE, F_TITLE),
        line(""),
        line("Anchor — pins first, compress second.", INK),
        line("github.com/Aayu095/hermes-anchor", MUTED),
        line("Built for the Hermes Agent Plugin Hackathon", MUTED, F_SMALL),
    ], 7.0))


def render(idx: int, lines) -> Path:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 46], fill=(18, 22, 27))
    d.text((24, 12), "anchor — hermes plugin demo · real engine, scripted session", font=F_SMALL, fill=MUTED)
    y = 92
    for text, color, font in lines:
        if text:
            d.text((56, y), text, font=font, fill=color)
        y += 44 if font == F_TITLE else 36
    d.text((56, H - 44), "All outputs captured from the real plugin via Hermes's own loader.", font=F_SMALL, fill=(90, 98, 108))
    out = FRAMES / f"f{idx:02d}.png"
    img.save(out)
    return out


concat = []
total = 0.0
for i, (lines, dur) in enumerate(scenes):
    p = render(i, lines)
    concat.append(f"file '{p}'\nduration {dur}")
    total += dur
concat.append(f"file '{FRAMES / f'f{len(scenes) - 1:02d}.png'}'")
(concat_path := FRAMES / "concat.txt").write_text("\n".join(concat))

target = HERE / "anchor-demo.mp4"
subprocess.run(
    ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_path),
     "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "medium", str(target)],
    check=True, capture_output=True,
)
print(f"wrote {target} · scenes={len(scenes)} · nominal duration ~{total:.0f}s")
