"""Contact sheet de un fotograma por clip para revision visual (solo lectura)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\SPARTAN PC\Documents\new_life\clipping")
RUN = ROOT / "work" / "run_20261116_new_source_full_pipeline"
OUT = RUN / "frames"
sys.stdout.reconfigure(encoding="utf-8")

STEMS = [
    "a7ace6f1_clip_0001_es_20261117",
    "a7ace6f1_clip_0002_es_20261117",
    "a7ace6f1_clip_0003_es_20261117",
    "a7ace6f1_clip_0004_es_20261117",
    "a7ace6f1_clip_0008_es_20261117",
    "a7ace6f1_clip_0009_es_20261117",
]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    from PIL import Image, ImageDraw
    tiles = []
    for idx, stem in enumerate(STEMS, 1):
        mp4 = ROOT / "output" / f"{stem}.mp4"
        state = json.loads((RUN / "delivery" / stem / "state.json").read_text(encoding="utf-8"))
        duration = float(state["deliverable"]["duration_seconds"])
        note = (state["render"]["artifacts"]["notifications"][0])
        t = min(float(note["hold_start_seconds"]) + 0.2, duration - 0.5)
        png = OUT / f"{stem}_frame.png"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{t:.3f}", "-i", str(mp4), "-frames:v", "1", str(png),
        ], check=True)
        im = Image.open(png).convert("RGB").resize((270, 480))
        tiles.append((stem, t, im))
    sheet = Image.new("RGB", (270 * 3, 480 * 2 + 40 * 2), "white")
    draw = ImageDraw.Draw(sheet)
    for i, (stem, t, im) in enumerate(tiles):
        x = (i % 3) * 270
        y = (i // 3) * (480 + 40)
        sheet.paste(im, (x, y + 20))
        draw.text((x + 4, y + 3), f"clip {stem.split('_')[2]} t={t:.1f}s", fill="black")
    path = OUT / "contact_sheet.png"
    sheet.save(path)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
