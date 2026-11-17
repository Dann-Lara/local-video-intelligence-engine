"""Comprobaciones de audio por clip (senal, silencio y clic local)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(r"C:\Users\SPARTAN PC\Documents\new_life\clipping")
RUN = ROOT / "work" / "run_20261116_new_source_full_pipeline"
AUDIO = RUN / "audio_checks"
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
    AUDIO.mkdir(parents=True, exist_ok=True)
    results = []
    for stem in STEMS:
        state = json.loads((RUN / "delivery" / stem / "state.json").read_text(encoding="utf-8"))
        note = state["render"]["artifacts"]["notifications"][0]
        t_click = float(note["enter_start_seconds"]) + 0.012
        wav = AUDIO / f"{stem}.wav"
        proc = subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(ROOT / "output" / f"{stem}.mp4"),
            "-vn", "-ac", "1", "-ar", "44100", "-f", "wav", str(wav),
        ], capture_output=True)
        if proc.returncode != 0:
            raise SystemExit(f"{stem}: ffmpeg audio fallo")
        raw = subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(wav),
            "-f", "f32le", "-acodec", "pcm_f32le", "-",
        ], capture_output=True, check=True).stdout
        x = np.frombuffer(raw, dtype="<f4")
        sr = 44100
        rms = float(np.sqrt(np.mean(x ** 2)))
        peak = float(np.max(np.abs(x)))
        silent = float(np.mean(np.abs(x) < 1e-4))
        i0 = int(t_click * sr)
        click = x[i0:i0 + int(0.045 * sr)]
        before = x[max(0, i0 - sr):i0]
        click_rms = float(np.sqrt(np.mean(click ** 2))) if click.size else 0.0
        before_rms = float(np.sqrt(np.mean(before ** 2))) if before.size else 0.0
        item = {
            "clip_id": stem.split("_")[2],
            "seconds": round(len(x) / sr, 3),
            "rms": round(rms, 5),
            "peak": round(peak, 5),
            "silent_fraction": round(silent, 4),
            "click_time_seconds": round(t_click, 3),
            "click_rms": round(click_rms, 5),
            "pre_click_rms": round(before_rms, 5),
            "decode_ok": state["validate_mp4"]["decode_integrity"]["ok"],
            "ncc": round(float(state["timing_alignment"]["global_ncc"]), 6),
        }
        item["pass"] = (
            item["decode_ok"] and item["ncc"] >= 0.90 and rms > 0.005
            and peak > 0.05 and silent < 0.90 and click_rms > 0.001
        )
        results.append(item)
        print(json.dumps(item, ensure_ascii=False))
    out = RUN / "audio_checks.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print("AUDIO PASS:", sum(r["pass"] for r in results), "/", len(results))
    return 0 if all(r["pass"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
