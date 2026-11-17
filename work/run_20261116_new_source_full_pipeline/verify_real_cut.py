"""Verificacion real del corte preciso con el codigo compartido ya parcheado.

Corta rec_0002 (73.0 -> 134.0) en un directorio aislado y mide:
- alineacion NCC de audio contra la fuente;
- SSIM del frame 0 contra el frame real en 73.006270 y contra el keyframe anterior.

Solo lectura de la fuente; no publica nada.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\SPARTAN PC\Documents\new_life\clipping")
RUN = ROOT / "work" / "run_20261116_new_source_full_pipeline"
WORK = RUN / "verification"
SOURCE = ROOT / "input" / "Escape 100 Cops, Win $500,000 (2160p_30fps_AV1-128kbit_AAC-inglés).mp4"
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT))

from services.delivery.pipeline import measure_alignment  # noqa: E402
from services.rendering.renderer import render_clip  # noqa: E402


def frame(path: Path, t: float, tag: str) -> Path:
    out = WORK / f"{tag}.png"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y",
         "-ss", f"{t:.6f}", "-i", str(path), "-frames:v", "1", str(out)],
        check=True, capture_output=True,
    )
    return out


def ssim(a: Path, b: Path) -> float:
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(a), "-i", str(b),
         "-lavfi", "ssim", "-frames:v", "1", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    text = p.stderr
    idx = text.rfind("All:")
    if idx < 0 or "SSIM" not in text:
        raise RuntimeError(text[-1000:])
    return float(text[idx + len("All:"):].split()[0])


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    out = WORK / "rec_0002_verified_source.mp4"
    meta = render_clip(
        source_path=SOURCE, start_seconds=73.0, end_seconds=134.0,
        output_path=out, timeout=7200.0,
    )
    align = measure_alignment(
        out, SOURCE, expected_start=73.0, work_audio_dir=WORK,
    )
    cut0 = frame(out, 0.0, "verified_cut_frame0")
    at_target = frame(SOURCE, 73.006270, "verified_source_73_006270")
    at_keyframe = frame(SOURCE, 72.439033, "verified_source_keyframe")
    result = {
        "cut": {k: meta[k] for k in (
            "requested_start_seconds", "requested_end_seconds",
            "actual_duration_seconds", "duration_delta_seconds",
            "cut_method", "input_seek_seconds", "output_seek_seconds",
        )},
        "ffmpeg_command": meta["ffmpeg_command"],
        "alignment": {
            "clip_zero_maps_to_source_seconds":
                align["clip_zero_maps_to_source_seconds"],
            "delta_vs_requested_seconds":
                align["clip_zero_maps_to_source_seconds"] - 73.0,
            "global_ncc": align["global_ncc"],
            "max_chunk_drift_seconds": align["max_chunk_drift_seconds"],
        },
        "ssim": {
            "cut_frame0_vs_source_73_006270": ssim(cut0, at_target),
            "cut_frame0_vs_keyframe_72_439033": ssim(cut0, at_keyframe),
        },
    }
    (RUN / "verification_real_source.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
