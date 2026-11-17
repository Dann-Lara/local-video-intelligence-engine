"""Inventario de `input/`: identifica videos, metadatos, hash y proyectos existentes.

Solo lectura. Salida: `work/run_20261116_new_source_full_pipeline/input_inventory.json`.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\SPARTAN PC\Documents\new_life\clipping")
RUN = ROOT / "work" / "run_20261116_new_source_full_pipeline"
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def probe(path: Path) -> dict:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-1200:])
    return json.loads(p.stdout)


def main() -> int:
    projects = []
    for p in (ROOT / "projects").glob("*/*"):
        if p.name in ("project.json", "source_metadata.json"):
            projects.append(json.loads(p.read_text(encoding="utf-8")))

    known_paths = {
        str(Path(str(pr.get("source", {}).get("path", ""))).resolve()).lower()
        for pr in projects
    }
    known_hashes = {
        str(pr.get("source_asset_id", "")).removeprefix("sha256-").upper()
        for pr in projects
        if str(pr.get("source_asset_id", "")).startswith("sha256-")
    }

    items = []
    for path in sorted((ROOT / "input").glob("*.mp4")):
        info = probe(path)
        v = next((s for s in info["streams"] if s["codec_type"] == "video"), {})
        a = next((s for s in info["streams"] if s["codec_type"] == "audio"), {})
        digest = sha256(path)
        tags = {**info.get("format", {}).get("tags", {}), **v.get("tags", {}),
                **a.get("tags", {})}
        items.append({
            "name": path.name,
            "path": str(path),
            "bytes": path.stat().st_size,
            "duration_seconds": float(info["format"]["duration"]),
            "width": v.get("width"),
            "height": v.get("height"),
            "fps": v.get("r_frame_rate"),
            "avg_fps": v.get("avg_frame_rate"),
            "video_codec": v.get("codec_name"),
            "video_profile": v.get("profile"),
            "pix_fmt": v.get("pix_fmt"),
            "audio_codec": a.get("codec_name"),
            "audio_profile": a.get("profile"),
            "audio_sample_rate": a.get("sample_rate"),
            "audio_channels": a.get("channels"),
            "audio_language_tag": a.get("tags", {}).get("language"),
            "title_tag": tags.get("title"),
            "sha256": digest,
            "known_project": (
                str(path.resolve()).lower() in known_paths or digest in known_hashes
            ),
        })
        print(json.dumps(items[-1], ensure_ascii=False))

    RUN.mkdir(parents=True, exist_ok=True)
    result = {
        "projects_found": len(projects),
        "project_paths": sorted(known_paths),
        "videos": items,
        "new_videos": [i["name"] for i in items if not i["known_project"]],
    }
    (RUN / "input_inventory.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print("NUEVOS:", result["new_videos"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
