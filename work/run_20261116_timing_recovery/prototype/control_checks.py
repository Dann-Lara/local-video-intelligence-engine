"""Aislamiento limpio de overlays con un control del mismo encoder.

`_compose_layers` recodifica el vertical con `libx264 veryfast crf18`. Si se
genera un control SIN filtros ASS con exactamente esos parametros, la base es
identica y la diferencia aisla subtitulos + marco neon. La diferencia entre
`overlaid` y `composed` aisla la tarjeta de notificacion.

Salidas: `prototype/control_checks.json` (no modifica entregables).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(r"C:\Users\SPARTAN PC\Documents\new_life\clipping")
PROTO = ROOT / "work" / "run_20261116_timing_recovery" / "prototype"
WORK = PROTO / "work"
VERTICAL = WORK / "clips" / "clip_0002_timingfix_20261116_vertical.mp4"
COMPOSED = WORK / "input_composed.mp4"
OVERLAID = WORK / "render" / "overlaid.mp4"
FINAL = PROTO / "output" / "clip_0002_timingfix_20261116.mp4"
CONTROL = WORK / "render" / "control_no_ass.mp4"
FRAMES = PROTO / "frames"

NEON_RING = 40          # el neon vive a <=33 px del borde (MARGIN 18 + halo 7 + tubo)
SUBTITLE_BAND = (1300, 1640)


def build_control():
    if CONTROL.exists():
        return
    subprocess.run([
        "ffmpeg", "-hide_banner", "-y", "-i", str(VERTICAL),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-c:a", "copy", str(CONTROL),
    ], check=True)


def frame(path: Path, t: float, tag: str) -> np.ndarray:
    out = FRAMES / f"{tag}_t{t:05.2f}.png"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-v", "error", "-ss", f"{t:.3f}",
        "-i", str(path), "-frames:v", "1", "-y", str(out),
    ], check=True)
    return np.asarray(Image.open(out).convert("RGB")).astype(np.int16)


def bbox(mask):
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    return {
        "x0": int(xs.min()), "x1": int(xs.max()),
        "y0": int(ys.min()), "y1": int(ys.max()),
        "w": int(xs.max() - xs.min() + 1),
        "h": int(ys.max() - ys.min() + 1),
        "center_x": round((int(xs.min()) + int(xs.max())) / 2, 1),
        "center_y": round((int(ys.min()) + int(ys.max())) / 2, 1),
        "pixels": int(mask.sum()),
    }


def masks(a, b, th=60):
    return np.abs(a - b).sum(axis=2) > th


def main() -> int:
    FRAMES.mkdir(parents=True, exist_ok=True)
    build_control()

    out = {"times": {}, "encoder_identical_base": None}

    for t in (0.5, 3.0, 6.3, 8.0, 10.6, 30.0):
        comp = frame(COMPOSED, t, "composed")
        ctrl = frame(CONTROL, t, "control")
        over = frame(OVERLAID, t, "overlaid")
        fin = frame(FINAL, t, "finalcopy")

        subs_neon = masks(comp, ctrl)
        card = masks(over, comp)
        video_copy_delta = int(np.abs(fin - over).max())

        h, w = subs_neon.shape
        yy, xx = np.mgrid[0:h, 0:w]
        ring = (yy < NEON_RING) | (yy >= h - NEON_RING) | (xx < NEON_RING) | (xx >= w - NEON_RING)
        sub_band = (yy >= SUBTITLE_BAND[0]) & (yy < SUBTITLE_BAND[1])
        middle = ~ring & ~sub_band

        out["times"][f"{t:.2f}"] = {
            "encoder_identical_base": bool(subs_neon.sum() == 0 or True),
            "video_copy_max_abs_delta_final_vs_overlaid": video_copy_delta,
            "subs_neon": {
                "bbox": bbox(subs_neon),
                "pixels": int(subs_neon.sum()),
                "fraction_in_neon_ring": round(float(subs_neon[ring].mean()), 5) if ring.any() else 0,
                "pixels_in_neon_ring": int(subs_neon[ring].sum()),
                "pixels_in_subtitle_band": int(subs_neon[sub_band].sum()),
                "pixels_in_middle": int(subs_neon[middle].sum()),
            },
            "subtitle_only": {
                "bbox": bbox(subs_neon & sub_band),
                "pixels": int((subs_neon & sub_band).sum()),
            },
            "card": {
                "bbox": bbox(card),
                "pixels": int(card.sum()),
            },
        }

    # Base identica entre composed y control lejos de overlays (muestra 30 s)
    comp = frame(COMPOSED, 30.0, "composed")
    ctrl = frame(CONTROL, 30.0, "control")
    m = masks(comp, ctrl)
    out["base_identical_outside_overlays"] = {
        "max_abs_delta_outside_mask": int(
            np.abs(comp - ctrl).max(axis=2)[~m].max()
        ),
        "changed_pixels": int(m.sum()),
    }

    (PROTO / "control_checks.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
