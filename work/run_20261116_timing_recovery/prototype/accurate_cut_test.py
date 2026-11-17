"""Prueba aislada de corte con precision de frame (rec_0002).

Compara dos variantes de corte:
  A) video recodificado + audio copiado
  B) video recodificado + audio recodificado AAC

No toca el pipeline ni ningun archivo existente: escribe en
work/run_20261116_timing_recovery/prototype/cut_test/.
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
OUT = ROOT / "work" / "run_20261116_timing_recovery" / "prototype" / "cut_test"
SOURCE = ROOT / "input" / "Escape 100 Cops, Win $500,000 (2160p_30fps_AV1-128kbit_AAC-inglés).mp4"
START = 73.0
END = 134.0
DUR = END - START


def run(cmd, check=True):
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"FALLO {cmd[:8]}\n{proc.stderr[-1500:]}")
    return proc


def ffprobe_json(path):
    return json.loads(run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]).stdout)


def first_frame(path, stream="v:0"):
    data = json.loads(run([
        "ffprobe", "-v", "error", "-select_streams", stream,
        "-show_entries", "frame=pts_time,key_frame", "-read_intervals", "%+#1",
        "-of", "json", str(path),
    ]).stdout)
    frames = data.get("frames", [])
    return frames[0] if frames else None


def ssim(a, b):
    proc = run([
        "ffmpeg", "-hide_banner", "-i", str(a), "-i", str(b),
        "-lavfi", "ssim", "-f", "null", "-",
    ], check=False)
    m = re.search(r"All:([0-9.]+)", (proc.stderr or ""))
    return float(m.group(1)) if m else None


def extract_source_frame(offset_from_60: float, out: Path):
    run([
        "ffmpeg", "-hide_banner", "-v", "error", "-ss", "60", "-i", str(SOURCE),
        "-ss", f"{offset_from_60:.6f}", "-frames:v", "1", "-q:v", "1", "-y", str(out),
    ])


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    pre = START - 10.0
    offset = START - pre

    variants = {
        "A_audio_copy": ["-c:a", "copy"],
        "B_audio_aac192": ["-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2"],
    }
    results = {}
    for name, audio_args in variants.items():
        out = OUT / f"rec_0002_{name}.mp4"
        print(f"Generando {name} ...")
        run([
            "ffmpeg", "-hide_banner", "-y",
            "-ss", f"{pre:.6f}", "-i", str(SOURCE),
            "-ss", f"{offset:.6f}", "-t", f"{DUR:.6f}",
            "-map", "0:v:0", "-map", "0:a:0?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "14",
            "-pix_fmt", "yuv420p",
            *audio_args,
            "-movflags", "+faststart",
            str(out),
        ])
        probe = ffprobe_json(out)
        v = next(s for s in probe["streams"] if s["codec_type"] == "video")
        a = next(s for s in probe["streams"] if s["codec_type"] == "audio")

        frame0 = OUT / f"{name}_frame0.png"
        run([
            "ffmpeg", "-hide_banner", "-v", "error", "-i", str(out),
            "-frames:v", "1", "-q:v", "1", "-y", str(frame0),
        ])
        kf_png = OUT / "source_kf_72_439033.png"
        req_png = OUT / "source_73_006270.png"
        if not kf_png.exists():
            extract_source_frame(72.439033 - 60.0, kf_png)
        if not req_png.exists():
            # primer frame con pts >= 73.0 (frame 2188 = 73.00627 s)
            extract_source_frame(73.006270 - 60.0, req_png)

        results[name] = {
            "path": str(out.relative_to(ROOT).as_posix()),
            "bytes": out.stat().st_size,
            "format_duration": float(probe["format"]["duration"]),
            "video_duration": float(v.get("duration", 0)),
            "audio_duration": float(a.get("duration", 0)),
            "video_codec": v.get("codec_name"),
            "video_profile": v.get("profile"),
            "pix_fmt": v.get("pix_fmt"),
            "width": v.get("width"),
            "height": v.get("height"),
            "r_frame_rate": v.get("r_frame_rate"),
            "audio_codec": a.get("codec_name"),
            "audio_profile": a.get("profile"),
            "sample_rate": a.get("sample_rate"),
            "channels": a.get("channels"),
            "video_first_frame": first_frame(out, "v:0"),
            "audio_first_frame": first_frame(out, "a:0"),
            "frame0_vs_source_keyframe_72_439033_ssim": ssim(frame0, kf_png),
            "frame0_vs_source_73_006270_ssim": ssim(frame0, req_png),
        }
        print(json.dumps(results[name], indent=2, ensure_ascii=False))

    # Alineacion NCC con la funcion real del pipeline (solo lectura)
    sys.path.insert(0, str(ROOT))
    from services.delivery.pipeline import measure_alignment

    align_dir = OUT / "align_audio"
    for name in variants:
        out = OUT / f"rec_0002_{name}.mp4"
        align = measure_alignment(
            out, SOURCE, expected_start=START, work_audio_dir=align_dir
        )
        results[name]["ncc_alignment"] = {
            "global_ncc": align["global_ncc"],
            "clip_zero_maps_to_source_seconds": align["clip_zero_maps_to_source_seconds"],
            "max_chunk_drift_seconds": align["max_chunk_drift_seconds"],
        }

    (OUT / "cut_test_results.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        k: {
            "duration": v["format_duration"],
            "v0": v["video_first_frame"],
            "a0": v["audio_first_frame"],
            "ssim_kf": v["frame0_vs_source_keyframe_72_439033_ssim"],
            "ssim_req": v["frame0_vs_source_73_006270_ssim"],
            "ncc_start": v["ncc_alignment"]["clip_zero_maps_to_source_seconds"],
        }
        for k, v in results.items()
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
