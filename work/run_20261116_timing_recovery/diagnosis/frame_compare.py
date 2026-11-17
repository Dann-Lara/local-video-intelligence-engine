"""Comparacion de fotogramas: primer frame del corte vs fuente.

Demuestra si el primer frame visible del corte corresponde al keyframe
anterior o al inicio solicitado. Solo lee archivos; no modifica nada.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(r"C:\Users\SPARTAN PC\Documents\new_life\clipping")
DIAG = ROOT / "work" / "run_20261116_timing_recovery" / "diagnosis"
FRAMES = DIAG / "frames"
SOURCE = ROOT / "input" / "Escape 100 Cops, Win $500,000 (2160p_30fps_AV1-128kbit_AAC-inglés).mp4"


def run(cmd):
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[:6]} -> {proc.returncode}\n{proc.stderr[-1200:]}")
    return proc


def source_frame(offset_from_60: float, out_name: str) -> Path:
    """Extrae un frame exacto usando seek de entrada + seek de salida."""
    out = FRAMES / out_name
    run([
        "ffmpeg", "-hide_banner", "-v", "error",
        "-ss", "60", "-i", str(SOURCE),
        "-ss", f"{offset_from_60:.3f}", "-frames:v", "1", "-q:v", "1",
        "-y", str(out),
    ])
    return out


def cut_frame(clip: Path, out_name: str) -> Path:
    out = FRAMES / out_name
    run([
        "ffmpeg", "-hide_banner", "-v", "error",
        "-i", str(clip), "-frames:v", "1", "-q:v", "1", "-y", str(out),
    ])
    return out


def ssim(a: Path, b: Path) -> float:
    proc = run([
        "ffmpeg", "-hide_banner", "-i", str(a), "-i", str(b),
        "-lavfi", "ssim", "-f", "null", "-",
    ])
    text = (proc.stderr or "") + (proc.stdout or "")
    match = re.search(r"All:([0-9.]+)", text)
    if not match:
        raise RuntimeError(f"No SSIM en salida: {text[-800:]}")
    return float(match.group(1))


def psnr(a: Path, b: Path) -> float:
    proc = run([
        "ffmpeg", "-hide_banner", "-i", str(a), "-i", str(b),
        "-lavfi", "psnr", "-f", "null", "-",
    ])
    text = (proc.stderr or "") + (proc.stdout or "")
    match = re.search(r"average:([0-9.]+)", text)
    return float(match.group(1)) if match else float("nan")


def main() -> int:
    FRAMES.mkdir(parents=True, exist_ok=True)
    clip2 = ROOT / "work" / "clip_0002_recovered_20261115_delivery" / "clips" / "clip_0002_recovered_20261115_source.mp4"

    # rec_0002: start 73.0, keyframe previo 72.439033
    kf = source_frame(72.439033 - 60.0, "src_rec2_kf_72_439033.png")
    req = source_frame(73.0 - 60.0, "src_rec2_req_73_000000.png")
    c0 = cut_frame(clip2, "cut_rec2_frame0.png")

    result = {
        "clip": str(clip2),
        "source": str(SOURCE),
        "evidence": {
            "cut_frame0_vs_source_keyframe_72.439033": {
                "ssim": ssim(c0, kf),
                "psnr_db": psnr(c0, kf),
            },
            "cut_frame0_vs_source_requested_73.000000": {
                "ssim": ssim(c0, req),
                "psnr_db": psnr(c0, req),
            },
        },
    }
    (DIAG / "frame_comparison.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
