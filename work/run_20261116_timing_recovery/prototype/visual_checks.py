"""Comprobaciones visuales cuantitativas del prototipo (sin juicio humano).

Compara el frame final contra la version vertical del MISMO instante (control
sin subtitulos/marco/tarjeta) para aislar cada overlay, y mide la geometria de
la tarjeta a partir del canal alfa del .mov de overlay.

NO declara validacion visual: produce evidencia numerica para que la revise
una persona. Los PNG quedan en `prototype/frames/`.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(r"C:\Users\SPARTAN PC\Documents\new_life\clipping")
PROTO = ROOT / "work" / "run_20261116_timing_recovery" / "prototype"
FINAL = PROTO / "output" / "clip_0002_timingfix_20261116.mp4"
VERTICAL = PROTO / "work" / "clips" / "clip_0002_timingfix_20261116_vertical.mp4"
OVERLAY = PROTO / "work" / "render" / "overlay_timing.mov"
REFERENCE = ROOT / "output" / "clip_0002_shorts_mvp_20261028.mp4"
FRAMES = PROTO / "frames"

TIMES = [0.5, 3.0, 6.3, 8.0, 10.6, 30.0, 59.0, 60.5]
SUBTITLE_BAND = (1300, 1620)
BORDER = 12


def frame(path: Path, t: float, tag: str) -> np.ndarray:
    out = FRAMES / f"{tag}_t{t:05.2f}.png"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-v", "error", "-ss", f"{t:.3f}",
        "-i", str(path), "-frames:v", "1", "-y", str(out),
    ], check=True)
    return np.asarray(Image.open(out).convert("RGB")).astype(np.int16)


def overlay_alpha(t: float) -> np.ndarray:
    out = FRAMES / f"overlay_alpha_t{t:05.2f}.png"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-v", "error", "-ss", f"{t:.3f}",
        "-i", str(OVERLAY), "-frames:v", "1", "-pix_fmt", "rgba",
        "-y", str(out),
    ], check=True)
    return np.asarray(Image.open(out).convert("RGBA"))[:, :, 3]


def bbox(mask: np.ndarray):
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
    }


def region_diff(final: np.ndarray, vertical: np.ndarray, y0, y1, x0, x1, th=40):
    d = np.abs(final[y0:y1, x0:x1] - vertical[y0:y1, x0:x1]).sum(axis=2)
    mask = d > th
    return {
        "changed_fraction": round(float(mask.mean()), 5),
        "bbox": bbox(mask),
    }


def border_stats(img: np.ndarray):
    strips = {
        "top": img[:BORDER, :, :],
        "bottom": img[-BORDER:, :, :],
        "left": img[:, :BORDER, :],
        "right": img[:, -BORDER:, :],
    }
    out = {}
    for name, s in strips.items():
        r, g, b = s[:, :, 0], s[:, :, 1], s[:, :, 2]
        cyan = (b > 110) & (b > r + 30) & (g > 55)
        out[name] = {
            "cyan_fraction": round(float(cyan.mean()), 4),
            "mean_rgb": [int(r.mean()), int(g.mean()), int(b.mean())],
        }
    return out


def row_detail(img: np.ndarray):
    gray = img.mean(axis=2)
    grad = np.abs(np.diff(gray, axis=1)).mean(axis=1)
    grad = np.convolve(grad, np.ones(21) / 21, mode="same")
    peak = float(grad.max())
    rows = np.where(grad > 0.25 * peak)[0]
    return {
        "detail_peak": round(peak, 2),
        "active_row_span": [int(rows.min()), int(rows.max())] if len(rows) else None,
    }


def main() -> int:
    FRAMES.mkdir(parents=True, exist_ok=True)
    result = {"times": {}, "overlay_alpha": {}, "reference": {}}

    for t in TIMES:
        f = frame(FINAL, t, "final")
        v = frame(VERTICAL, t, "vertical")
        key = f"{t:.2f}"
        entry = {
            "mean_luminance": round(float(f.mean()), 2),
            "near_black_fraction": round(float((f.max(axis=2) < 20).mean()), 6),
            "subtitle_band": region_diff(
                f, v, SUBTITLE_BAND[0], SUBTITLE_BAND[1], 0, 1080
            ),
            "border": border_stats(f),
            "row_detail": row_detail(f),
            "full_frame_vs_vertical": region_diff(f, v, 0, 1920, 0, 1080),
        }
        entry["subtitle_band"]["cyan_pixels"] = int(
            ((f[SUBTITLE_BAND[0]:SUBTITLE_BAND[1], :, 2] > 110)
             & (f[SUBTITLE_BAND[0]:SUBTITLE_BAND[1], :, 2]
                > f[SUBTITLE_BAND[0]:SUBTITLE_BAND[1], :, 0] + 30)).sum()
        )
        result["times"][key] = entry
        if t in (3.0, 8.0):
            result["overlay_alpha"][key] = bbox(overlay_alpha(t) > 8)

    if REFERENCE.exists():
        for t in (2.0, 8.0):
            r = frame(REFERENCE, t, "ref")
            result["reference"][f"{t:.2f}"] = {
                "mean_luminance": round(float(r.mean()), 2),
                "border": border_stats(r),
                "row_detail": row_detail(r),
            }

    for t in (60.5,):
        pass

    (PROTO / "visual_checks.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "overlay_alpha": result["overlay_alpha"],
        "subtitle_band": {k: v["subtitle_band"] for k, v in result["times"].items()},
        "border_bottom": {k: v["border"]["bottom"] for k, v in result["times"].items()},
        "luminance": {k: (v["mean_luminance"], v["near_black_fraction"])
                      for k, v in result["times"].items()},
        "row_detail_final_8": result["times"]["8.00"]["row_detail"],
        "row_detail_ref_8": result.get("reference", {}).get("8.00", {}).get("row_detail"),
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
