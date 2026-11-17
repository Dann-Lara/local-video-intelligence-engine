"""Validacion tecnica/visual/auditiva del prototipo clip_0002_timingfix_20261116.

Solo lectura + extraccion de frames a un directorio nuevo. No toca el entregable.
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
RUN = ROOT / "work" / "run_20261116_timing_recovery"
PROTO = RUN / "prototype"
FINAL = PROTO / "output" / "clip_0002_timingfix_20261116.mp4"
VERTICAL = PROTO / "work" / "clips" / "clip_0002_timingfix_20261116_vertical.mp4"
SUBS = PROTO / "work" / "assets" / "subs.ass"
FRAMES = PROTO / "frames"
REFERENCE = ROOT / "output" / "clip_0002_shorts_mvp_20261028.mp4"

FRAME_TIMES = [0.5, 3.0, 6.3, 8.0, 10.6, 30.0, 59.0, 60.5]


def run(cmd):
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )


def probe_json(path):
    p = run(["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)])
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-800:])
    return json.loads(p.stdout)


def volumedetect(path):
    p = run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect",
             "-vn", "-f", "null", "-"])
    text = p.stderr or ""
    mean = re.search(r"mean_volume:\s*(-?[0-9.]+) dB", text)
    peak = re.search(r"max_volume:\s*(-?[0-9.]+) dB", text)
    return {
        "mean_volume_dB": float(mean.group(1)) if mean else None,
        "max_volume_dB": float(peak.group(1)) if peak else None,
    }


def astats_peak(path):
    p = run(["ffmpeg", "-hide_banner", "-i", str(path), "-af",
             "astats=metadata=1:reset=0", "-vn", "-f", "null", "-"])
    text = p.stderr or ""
    peak = re.search(r"Peak level dB:\s*(-?[0-9.]+)", text)
    flat = len(re.findall(r"Number of samples:\s*\d+", text))
    return {"peak_level_dB": float(peak.group(1)) if peak else None}


def blackdetect(path):
    p = run(["ffmpeg", "-hide_banner", "-i", str(path), "-vf",
             "blackdetect=d=0.10:pix_th=0.10", "-an", "-f", "null", "-"])
    text = p.stderr or ""
    return [
        {"start": float(m.group(1)), "end": float(m.group(2)), "duration": float(m.group(3))}
        for m in re.finditer(
            r"black_start:([0-9.]+)\s+black_end:([0-9.]+)\s+black_duration:([0-9.]+)",
            text,
        )
    ]


def freezedetect(path):
    p = run(["ffmpeg", "-hide_banner", "-i", str(path), "-vf",
             "freezedetect=n=0.003:d=1.0", "-an", "-f", "null", "-"])
    text = p.stderr or ""
    return [
        {"start": float(m.group(1)), "duration": float(m.group(2))}
        for m in re.finditer(r"freeze_start:\s*([0-9.]+)\s*\|\s*freeze_duration:\s*([0-9.]+)", text)
    ]


def subtitles_report(path):
    cues = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        if len(parts) != 10:
            continue
        start, end = parts[1], parts[2]
        text = re.sub(r"\{[^}]*\}", "", parts[9]).replace("\\N", " | ").strip()
        cues.append({"start": start, "end": end, "text": text, "chars": len(text)})
    return {
        "count": len(cues),
        "first": cues[0] if cues else None,
        "last": cues[-1] if cues else None,
        "max_chars": max((c["chars"] for c in cues), default=0),
        "cues": cues,
    }


def extract(path, t, out):
    run(["ffmpeg", "-hide_banner", "-v", "error", "-ss", f"{t:.3f}", "-i",
         str(path), "-frames:v", "1", "-q:v", "1", "-y", str(out)])


def main() -> int:
    FRAMES.mkdir(parents=True, exist_ok=True)
    probe = probe_json(FINAL)
    v = next(s for s in probe["streams"] if s["codec_type"] == "video")
    a = next(s for s in probe["streams"] if s["codec_type"] == "audio")

    frames = []
    for t in FRAME_TIMES:
        out = FRAMES / f"proto_t{t:05.2f}.png".replace(".", "_", 1)
        extract(FINAL, t, out)
        frames.append({"t": t, "file": str(out.relative_to(ROOT).as_posix()),
                       "bytes": out.stat().st_size})

    ref = None
    if REFERENCE.exists():
        ref = {}
        for t in (2.0, 8.0):
            out = FRAMES / f"ref_clip0002_mvp_t{t:04.1f}.png".replace(".", "_", 1)
            extract(REFERENCE, t, out)
            ref[f"t{t}"] = str(out.relative_to(ROOT).as_posix())

    result = {
        "final": str(FINAL.relative_to(ROOT).as_posix()),
        "probe": {
            "duration": float(probe["format"]["duration"]),
            "bit_rate": probe["format"].get("bit_rate"),
            "video": {k: v.get(k) for k in
                      ("codec_name", "profile", "pix_fmt", "width", "height",
                       "r_frame_rate", "nb_frames", "duration", "start_time")},
            "audio": {k: a.get(k) for k in
                      ("codec_name", "profile", "sample_rate", "channels",
                       "bit_rate", "duration", "start_time")},
        },
        "audio": {
            "final": volumedetect(FINAL),
            "final_astats": astats_peak(FINAL),
            "vertical_preclick": volumedetect(VERTICAL),
        },
        "black_frames": blackdetect(FINAL),
        "freezes": freezedetect(FINAL),
        "subtitles": subtitles_report(SUBS),
        "frames": frames,
        "reference_frames": ref,
    }

    # Comparacion de nivel de audio con los entregables aprobados.
    refs = {}
    for name in ("clip_0002_shorts_mvp_20261028.mp4",
                 "clip_0001_recovered_20261115.mp4"):
        p = ROOT / "output" / name
        if p.exists():
            refs[name] = volumedetect(p)
    result["approved_reference_levels"] = refs

    (PROTO / "validation.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "probe": result["probe"],
        "audio": result["audio"],
        "black_frames": result["black_frames"],
        "freezes": result["freezes"],
        "subs_first": result["subtitles"]["first"],
        "subs_last": result["subtitles"]["last"],
        "subs_count": result["subtitles"]["count"],
        "refs": refs,
        "frames": [f["file"] for f in frames],
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
