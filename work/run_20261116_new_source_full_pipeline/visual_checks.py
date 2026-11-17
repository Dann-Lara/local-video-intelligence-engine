"""Comprobaciones visuales automaticas (sin rendered visual humano).

Por clip:
- extrae el frame del corte fuente, del vertical y del final en el mismo t;
- reprocesa el corte fuente con el mismo filtro de encuadre que renderer.py y
  compara contra el vertical (NCC/MAE altos => el encuadre corresponde al tiempo);
- mide diferencia final-vs-vertical (subtitulos/tarjeta) y fraccion de pixeles
  claros en la franja de subtitulos.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

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
VF = ("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
      "crop=1080:1920,gblur=sigma=20,eq=brightness=-0.05[bg];"
      "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
      "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[vout]")


def grab(path: Path, t: float, dest: Path, filter_complex: str | None = None) -> np.ndarray:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-ss", f"{t:.3f}", "-i", str(path)]
    if filter_complex:
        cmd += ["-filter_complex", filter_complex, "-map", "[vout]"]
    cmd += ["-frames:v", "1", str(dest)]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg: {proc.stderr.decode('utf-8', 'replace')[-500:]}")
    img = cv2.imread(str(dest))
    if img is None:
        raise RuntimeError(f"No se pudo leer {dest}")
    return img


def metrics(a: np.ndarray, b: np.ndarray) -> dict:
    x = a.astype(np.float32).ravel()
    y = b.astype(np.float32).ravel()
    corr = float(np.corrcoef(x, y)[0, 1])
    mae = float(np.mean(np.abs(a.astype(np.float32) - b.astype(np.float32))))
    changed = float(np.mean(np.max(np.abs(a.astype(np.int16) - b.astype(np.int16)), axis=2) > 20))
    return {"ncc": round(corr, 6), "mae": round(mae, 3), "changed_fraction": round(changed, 4)}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    for stem in STEMS:
        work = RUN / "delivery" / stem
        state = json.loads((work / "state.json").read_text(encoding="utf-8"))
        note = state["render"]["artifacts"]["notifications"][0]
        duration = float(state["deliverable"]["duration_seconds"])
        t = min(float(note["hold_start_seconds"]) + 0.2, duration * 0.6)
        job = json.loads((RUN / "jobs" / f"{stem}.json").read_text(encoding="utf-8"))
        tr = json.loads((ROOT / "projects" / job["project_id"] / "transcription"
                         / "transcription.json").read_text(encoding="utf-8"))
        by_id = {s["segment_id"]: s for s in tr["segments"]}
        last_ref = job["candidate"]["evidence_refs"][-1]
        seg = by_id[last_ref]
        clip_start = float(job["candidate"]["start_seconds"])
        t_sub = min(max(0.5, (float(seg["start_seconds"]) + float(seg["end_seconds"])) / 2 - clip_start),
                    duration - 0.5)
        source_cut = work / "clips" / f"{stem}_source.mp4"
        vertical = work / "clips" / f"{stem}_vertical.mp4"
        final = ROOT / "output" / f"{stem}.mp4"

        src_frame = grab(source_cut, t, OUT / f"{stem}_src.png", VF)
        vert_frame = grab(vertical, t, OUT / f"{stem}_vertical.png")
        final_frame = grab(final, t, OUT / f"{stem}_final.png")
        subtitle_frame = grab(final, t_sub, OUT / f"{stem}_subtitle.png")

        framing = metrics(src_frame, vert_frame)
        overlay = metrics(vert_frame, final_frame)
        h, w = final_frame.shape[:2]
        band = subtitle_frame[int(h * 0.72):int(h * 0.92), int(w * 0.05):int(w * 0.95)]
        white_fraction = float(np.mean(np.all(band > 190, axis=2)))
        card_band = final_frame[int(h * 0.30):int(h * 0.60), :]
        card_white = float(np.mean(np.all(card_band > 190, axis=2)))
        item = {
            "clip_id": stem.split("_")[2],
            "t_seconds": round(t, 3),
            "t_subtitle_seconds": round(t_sub, 3),
            "last_evidence_segment": last_ref,
            "framing_source_vs_vertical": framing,
            "overlay_vertical_vs_final": overlay,
            "subtitle_white_fraction": round(white_fraction, 4),
            "card_region_white_fraction": round(card_white, 4),
            "resolution": [w, h],
        }
        item["pass"] = (
            framing["ncc"] >= 0.97 and framing["mae"] <= 12
            and overlay["changed_fraction"] > 0.01
            and white_fraction > 0.001
            and card_white > 0.001
        )
        results.append(item)
        print(json.dumps(item, ensure_ascii=False))
    out = RUN / "visual_checks.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print("VISUAL PASS:", sum(r["pass"] for r in results), "/", len(results))
    return 0 if all(r["pass"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
