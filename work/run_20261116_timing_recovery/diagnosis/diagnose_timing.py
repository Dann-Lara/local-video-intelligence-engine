"""Diagnostico de precision temporal del corte (Fase 1).

No modifica el pipeline: solo lee paquetes/fotogramas con ffprobe y compara
los artefactos ya generados por la ejecucion 2026-11-15.

Salidas:
- work/run_20261116_timing_recovery/diagnosis/timing_evidence.json
- work/run_20261116_timing_recovery/diagnosis/source_keyframes.json
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(r"C:\Users\SPARTAN PC\Documents\new_life\clipping")
DIAG = ROOT / "work" / "run_20261116_timing_recovery" / "diagnosis"
SOURCE = ROOT / "input" / "Escape 100 Cops, Win $500,000 (2160p_30fps_AV1-128kbit_AAC-inglés).mp4"

CANDIDATES = {
    "rec_0001": (0.0, 43.0),
    "rec_0002": (73.0, 134.0),
    "rec_0003": (153.0, 187.0),
    "rec_0004": (259.0, 284.0),
    "rec_0005": (678.06, 730.16),
    "rec_0006": (1097.26, 1175.82),
}

PREV_CUTS = {
    "rec_0001": "work/clip_0001_recovered_20261115_delivery/clips/clip_0001_recovered_20261115_source.mp4",
    "rec_0002": "work/clip_0002_recovered_20261115_delivery/clips/clip_0002_recovered_20261115_source.mp4",
    "rec_0003": "work/clip_0003_recovered_20261115_delivery/clips/clip_0003_recovered_20261115_source.mp4",
    "rec_0004": "work/clip_0004_recovered_20261115_delivery/clips/clip_0004_recovered_20261115_source.mp4",
    "rec_0005": "work/clip_0005_recovered_20261115_delivery/clips/clip_0005_recovered_20261115_source.mp4",
}

PREV_VERTICALS = {
    "rec_0001": "work/clip_0001_recovered_20261115_delivery/clips/clip_0001_recovered_20261115_vertical.mp4",
    "rec_0002": "work/clip_0002_recovered_20261115_delivery/clips/clip_0002_recovered_20261115_vertical.mp4",
    "rec_0003": "work/clip_0003_recovered_20261115_delivery/clips/clip_0003_recovered_20261115_vertical.mp4",
    "rec_0004": "work/clip_0004_recovered_20261115_delivery/clips/clip_0004_recovered_20261115_vertical.mp4",
    "rec_0005": "work/clip_0005_recovered_20261115_delivery/clips/clip_0005_recovered_20261115_vertical.mp4",
}

PREV_ACTUAL_START = {
    "rec_0001": 0.0,
    "rec_0002": 72.423039,
    "rec_0003": 150.000907,
    "rec_0004": 257.439637,
    "rec_0005": 675.422041,
}


def run_json(cmd):
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[:3]} -> {proc.returncode}: {proc.stderr[-800:]}")
    return json.loads(proc.stdout)


def video_packets(path: Path):
    data = run_json([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "packet=pts_time,dts_time,flags",
        "-of", "json", str(path),
    ])
    packets = []
    for p in data.get("packets", []):
        try:
            pts = float(p["pts_time"])
        except (KeyError, TypeError, ValueError):
            continue
        packets.append({
            "pts_time": pts,
            "dts_time": float(p["dts_time"]) if p.get("dts_time") not in (None, "N/A") else None,
            "flags": p.get("flags", ""),
            "keyframe": "K" in (p.get("flags") or ""),
        })
    packets.sort(key=lambda x: x["pts_time"])
    return packets


def first_video_frame(path: Path):
    data = run_json([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "frame=pts_time,pkt_duration_time,key_frame",
        "-read_intervals", "%+#1", "-of", "json", str(path),
    ])
    frames = data.get("frames", [])
    return frames[0] if frames else None


def main() -> int:
    DIAG.mkdir(parents=True, exist_ok=True)
    print("Leyendo paquetes de video de la fuente ...")
    packets = video_packets(SOURCE)
    keyframes = [p for p in packets if p["keyframe"]]
    print(f"paquetes video: {len(packets)}; keyframes: {len(keyframes)}")

    (DIAG / "source_keyframes.json").write_text(
        json.dumps({
            "source": str(SOURCE),
            "video_packet_count": len(packets),
            "keyframe_count": len(keyframes),
            "keyframes": [
                {"index": i, "pts_time": k["pts_time"]} for i, k in enumerate(keyframes)
            ],
        }, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    keys = [k["pts_time"] for k in keyframes]
    evidence = {
        "source": str(SOURCE),
        "source_video_packets": len(packets),
        "source_keyframes": len(keys),
        "cut_command_template": [
            "ffmpeg", "-y", "-ss", "{start:.6f}", "-i", "{source}",
            "-t", "{duration:.6f}", "-map", "0:v:0", "-map", "0:a:0?",
            "-c", "copy", "-avoid_negative_ts", "make_zero",
            "-movflags", "+faststart", "{output}",
        ],
        "candidates": {},
    }

    for rec, (start, end) in CANDIDATES.items():
        prev_kf = max((k for k in keys if k <= start + 1e-9), default=None)
        next_kf = min((k for k in keys if k > start + 1e-9), default=None)
        entry = {
            "requested_start_seconds": start,
            "requested_end_seconds": end,
            "requested_duration_seconds": round(end - start, 6),
            "previous_keyframe_pts": prev_kf,
            "next_keyframe_pts": next_kf,
            "snap_back_seconds": round(start - prev_kf, 6) if prev_kf is not None else None,
            "keyframe_interval_to_next": round(next_kf - prev_kf, 6) if prev_kf is not None and next_kf is not None else None,
        }

        cut_rel = PREV_CUTS.get(rec)
        if cut_rel:
            cut = ROOT / cut_rel
            if cut.exists():
                cut_probe = run_json([
                    "ffprobe", "-v", "error", "-print_format", "json",
                    "-show_format", "-show_streams", str(cut),
                ])
                cut_first = first_video_frame(cut)
                first_pkt = next((p for p in video_packets(cut)), None)
                entry["previous_cut"] = {
                    "path": cut_rel,
                    "exists": True,
                    "duration_seconds": float(cut_probe["format"]["duration"]),
                    "first_video_frame": cut_first,
                    "first_video_packet": first_pkt,
                    "measured_actual_start_seconds": PREV_ACTUAL_START.get(rec),
                    "measured_minus_requested": round(
                        PREV_ACTUAL_START.get(rec, start) - start, 6
                    ),
                }
                vert_rel = PREV_VERTICALS.get(rec)
                vert = ROOT / vert_rel if vert_rel else None
                if vert and vert.exists():
                    vp = run_json([
                        "ffprobe", "-v", "error", "-print_format", "json",
                        "-show_format", "-show_streams", str(vert),
                    ])
                    entry["previous_vertical"] = {
                        "path": vert_rel,
                        "duration_seconds": float(vp["format"]["duration"]),
                        "first_video_frame": first_video_frame(vert),
                    }
            else:
                entry["previous_cut"] = {"path": cut_rel, "exists": False}

        evidence["candidates"][rec] = entry
        print(
            f"{rec}: start={start:.3f} prev_kf={prev_kf} "
            f"next_kf={next_kf} snap={entry['snap_back_seconds']} "
            f"measured={PREV_ACTUAL_START.get(rec)}"
        )

    (DIAG / "timing_evidence.json").write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    # Verificacion de que el 'inicio medido' coincide con un keyframe de la fuente
    matches = []
    for rec, entry in evidence["candidates"].items():
        measured = PREV_ACTUAL_START.get(rec)
        if measured is None:
            continue
        best = min(keys, key=lambda k: abs(k - measured))
        matches.append({
            "rec": rec,
            "measured": measured,
            "closest_keyframe": best,
            "abs_diff": round(abs(best - measured), 6),
            "is_keyframe": abs(best - measured) <= 0.002,
        })
    evidence["measurement_vs_keyframe"] = matches
    (DIAG / "timing_evidence.json").write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(matches, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
