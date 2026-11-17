"""Normaliza el FPS del MP4 de trabajo a 30000/1001 dentro de work/.

La fuente nueva es 30/1, pero el contrato de entrega exige 30000/1001.
No se modifica el codigo compartido ni se relaja container_checks: se
re-codifica unicamente el artefacto de trabajo (audio copiado) y se actualiza
el state.json AISLADO de este job para que la validacion vuelva a correr.

Uso: python normalize_fps.py <directorio_de_trabajo_del_job>
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\SPARTAN PC\Documents\new_life\clipping")
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT))

from services.delivery.pipeline import ffprobe_json, sha256_file  # noqa: E402

EXPECTED_FPS = "30000/1001"


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Uso: normalize_fps.py <work_dir>")
    work = Path(sys.argv[1]).resolve()
    state_path = work / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    artifacts = state["render"]["artifacts"]
    original = Path(artifacts["final_work_path"])

    probe = ffprobe_json(original)
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    if video["r_frame_rate"] == EXPECTED_FPS:
        print("Ya esta en", EXPECTED_FPS, "- sin cambios")
        return 0

    normalized = original.with_name(original.stem + "_fps2997.mp4")
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(original),
        "-vf", f"fps={EXPECTED_FPS}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart",
        str(normalized),
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0 or not normalized.exists() or normalized.stat().st_size == 0:
        raise SystemExit(f"FFmpeg fallo: {proc.stderr.decode('utf-8', errors='replace')[-2000:]}")

    out = ffprobe_json(normalized)
    v = next(s for s in out["streams"] if s["codec_type"] == "video")
    a = next(s for s in out["streams"] if s["codec_type"] == "audio")
    if v["r_frame_rate"] != EXPECTED_FPS or v["avg_frame_rate"] != EXPECTED_FPS:
        raise SystemExit(f"FPS incorrecto tras normalizar: {v['r_frame_rate']}")
    if (v.get("profile") != "High" or v.get("pix_fmt") != "yuv420p"
            or int(v["width"]) != 1080 or int(v["height"]) != 1920):
        raise SystemExit("El video normalizado no cumple el contrato de video.")
    if (a.get("codec_name") != "aac" or a.get("profile") != "LC"
            or str(a.get("sample_rate")) != "44100" or int(a["channels"]) != 2):
        raise SystemExit("El audio normalizado no cumple el contrato de audio.")

    note = {
        "reason": "La fuente nueva es 30/1; el contrato de entrega exige 30000/1001.",
        "original_path": str(original),
        "original_fps": video["r_frame_rate"],
        "normalized_path": str(normalized),
        "normalized_fps": v["r_frame_rate"],
        "normalized_duration_seconds": float(out["format"]["duration"]),
        "expected_duration_seconds": artifacts["video_duration_seconds"],
        "method": "ffmpeg -vf fps=30000/1001 -c:v libx264 -crf 18 -preset veryfast -pix_fmt yuv420p -c:a copy",
        "contract_relaxed": False,
        "audio_reencoded": False,
        "shared_code_modified": False,
    }
    artifacts["fps_normalization"] = note
    artifacts["final_work_path"] = str(normalized)
    artifacts["final_work_sha256"] = sha256_file(normalized)
    artifacts["final_work_bytes"] = normalized.stat().st_size
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")
    print(json.dumps(note, indent=2, ensure_ascii=False))
    print("state.json actualizado; reanudar desde validate_mp4.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
