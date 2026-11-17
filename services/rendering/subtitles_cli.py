"""CLI minimo para integrar subtitulos en las versiones verticales (Fase 5C).

Uso (ejecutar desde la raiz del repositorio):

    python -m services.rendering.subtitles_cli \
        --plan plan.json \
        --outdir work/fase5c_<ts>/clips

`plan.json` describe, de forma explicita y verificable, cada clip a subtitular:

    {
      "transcription_path": "projects/<id>/transcription/transcription.json",
      "clips": [
        {
          "clip_id": "clip_0001",
          "input_path": "work/fase5b_.../clips/clip_0001_shorts.mp4",
          "output_path": "work/fase5c_.../clips/clip_0001_shorts_subtitled.mp4",
          "absolute_start_seconds": 526.791111,
          "clip_duration_seconds": 58.1581,
          "width": 1080,
          "height": 1920
        }
      ]
    }

El desplazamiento `absolute_start_seconds` (tiempo de la fuente que corresponde
al PTS 0 del clip) NO se inventa: se determina por correlacion de audio contra la
fuente y se documenta en el informe de la fase. Este CLI no recalcula ese valor.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .errors import RenderingError
from .subtitles import build_cues, render_with_subtitles


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m services.rendering.subtitles_cli",
        description="Integra subtitulos sincronizados en clips verticales (Fase 5C).",
    )
    parser.add_argument("--plan", required=True, help="Ruta al plan.json de la fase.")
    parser.add_argument(
        "--outdir",
        required=True,
        help="Carpeta de salida (se crea si no existe).",
    )
    return parser


def _load_transcription(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("segments", []), data


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    plan_path = Path(args.plan)
    outdir = Path(args.outdir)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))

    transcription_path = Path(plan["transcription_path"])
    if not transcription_path.exists():
        print(f"Transcripcion inexistente: {transcription_path}", file=sys.stderr)
        return 2
    segments, transcription = _load_transcription(transcription_path)

    outdir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "plan_path": str(plan_path),
        "transcription_path": str(transcription_path),
        "total_segments": len(segments),
        "words_available": bool(transcription.get("words")),
        "clips": [],
    }

    exit_code = 0
    for clip in plan.get("clips", []):
        clip_id = clip["clip_id"]
        input_path = Path(clip["input_path"])
        output_rel = clip.get("output_path")
        output_path = Path(output_rel) if output_rel else (outdir / f"{clip_id}_shorts_subtitled.mp4")
        abs_start = float(clip["absolute_start_seconds"])
        clip_duration = float(clip["clip_duration_seconds"])
        width = int(clip.get("width", 1080))
        height = int(clip.get("height", 1920))

        entry = {
            "clip_id": clip_id,
            "input_path": str(input_path),
            "output_path": str(output_path),
            "absolute_start_seconds": abs_start,
            "clip_duration_seconds": clip_duration,
        }
        try:
            cues = build_cues(
                segments,
                absolute_start_seconds=abs_start,
                clip_duration_seconds=clip_duration,
                width=width,
                height=height,
            )
            result = render_with_subtitles(
                source_path=input_path,
                output_path=output_path,
                cues=cues,
                width=width,
                height=height,
            )
            entry["status"] = "ok"
            entry["cue_count"] = len(cues)
            entry["cues"] = [
                {
                    "start_seconds": c.start_seconds,
                    "end_seconds": c.end_seconds,
                    "text": c.text.replace(r"\N", " / "),
                    "source_segment_id": c.source_segment_id,
                }
                for c in cues
            ]
            entry.update(result)
            print(
                f"[OK] {clip_id} -> {output_path} "
                f"({result['actual_duration_seconds']:.3f}s, "
                f"{result['width']}x{result['height']}, {len(cues)} cues)"
            )
        except RenderingError as exc:
            entry["status"] = "failed"
            entry["error"] = str(exc)
            entry["error_details"] = getattr(exc, "details", {})
            exit_code = 1
            print(f"[FAIL] {clip_id}: {exc}", file=sys.stderr)

        manifest["clips"].append(entry)

    (outdir / "subtitles_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
