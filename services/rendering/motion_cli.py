"""CLI para generar el ASS de motion graphics (Fase 5E, pasos 1-2).

IMPORTANTE: este CLI **no renderiza video**. Solo construye y escribe el archivo
`.ass` de efectos animados a partir de un plan JSON y de la transcripcion real.
El quemado sobre video se hara en una fase posterior, sobre una copia aislada,
con un segundo filtro `ass=` de FFmpeg.

Uso (ejecutar desde la raiz del repositorio):

    python -m services.rendering.motion_cli \
        --plan motion_plan.json \
        --outdir work/fase5e_<ts>/motion

Formato del plan JSON:

    {
      "transcription_path": "projects/<id>/transcription/transcription.json",
      "clips": [
        {
          "clip_id": "clip_0001",
          "absolute_start_seconds": 526.791111,
          "clip_duration_seconds": 58.1581,
          "events": [
            {"effect_type": "typography", "anchor": "seg_0209",
             "text": "100 COPS", "position": "top"},
            {"effect_type": "arrow", "anchor": "seg_0217",
             "text": "get the trap doors", "position": "indicator"},
            {"effect_type": "cta_subscribe", "anchor": "seg_0213",
             "hold_seconds": 2.5}
          ]
        }
      ]
    }

Reglas:
- Cada evento debe anclarse a un `segment_id` real o traer `start_seconds`/
  `end_seconds` explicitos. Nunca se inventan tiempos.
- Los CTA (`cta_like`, `cta_subscribe`, `cta_share`) son **opt-in**: solo se
  emiten si aparecen en la lista `events`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from .motion import (
    MotionPlan,
    MotionValidationError,
    build_motion_ass,
    build_plan_from_spec,
    build_motion_cues,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m services.rendering.motion_cli",
        description="Genera el ASS de motion graphics (sin renderizar video).",
    )
    parser.add_argument("--plan", required=True, help="Ruta al plan JSON de motion.")
    parser.add_argument(
        "--outdir",
        required=True,
        help="Carpeta de salida para los .ass (se crea si no existe).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Valida y describe los eventos sin escribir archivos.",
    )
    return parser


def _load_transcription(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("segments", []), data


def _process_clip(
    clip: Dict[str, Any],
    *,
    segments: List[Dict[str, Any]],
    outdir: Path,
    dry_run: bool,
) -> Dict[str, Any]:
    clip_id = clip["clip_id"]
    abs_start = float(clip["absolute_start_seconds"])
    clip_duration = float(clip["clip_duration_seconds"])
    width = int(clip.get("width", 1080))
    height = int(clip.get("height", 1920))
    margin_lr = int(clip.get("margin_lr", 70))

    specs = clip.get("events", []) or []
    plan: MotionPlan = build_plan_from_spec(
        specs,
        segments=segments,
        absolute_start_seconds=abs_start,
        clip_duration_seconds=clip_duration,
        width=width,
        height=height,
        margin_lr=margin_lr,
        source=f"{clip_id}",
    )
    cues = build_motion_cues(plan, clip_duration=clip_duration)

    entry: Dict[str, Any] = {
        "clip_id": clip_id,
        "clip_duration_seconds": clip_duration,
        "event_count": len(plan.events),
        "cue_count": len(cues),
        "cues": [
            {
                "effect_type": c.effect_type,
                "start_seconds": round(c.start_seconds, 3),
                "end_seconds": round(c.end_seconds, 3),
                "style": c.style,
                "source_segment_id": c.source_segment_id,
                "text": c.text,
            }
            for c in cues
        ],
    }

    if dry_run:
        entry["status"] = "validated"
        return entry

    out_path = outdir / f"{clip_id}_motion.ass"
    out_path.write_text(
        build_motion_ass(
            cues, width=width, height=height, margin_lr=margin_lr
        ),
        encoding="utf-8",
    )
    entry["status"] = "ok"
    entry["ass_path"] = str(out_path)
    return entry


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

    if not args.dry_run:
        outdir.mkdir(parents=True, exist_ok=True)

    manifest: Dict[str, Any] = {
        "plan_path": str(plan_path),
        "transcription_path": str(transcription_path),
        "total_segments": len(segments),
        "words_available": bool(transcription.get("words")),
        "render_video": False,
        "clips": [],
    }

    exit_code = 0
    for clip in plan.get("clips", []):
        try:
            entry = _process_clip(
                clip, segments=segments, outdir=outdir, dry_run=args.dry_run
            )
            manifest["clips"].append(entry)
            verb = "VALID" if args.dry_run else "OK"
            print(
                f"[{verb}] {entry['clip_id']} -> {entry.get('ass_path', '(dry-run)')} "
                f"({entry['cue_count']} cues, {entry['event_count']} eventos)"
            )
        except MotionValidationError as exc:
            exit_code = 1
            manifest["clips"].append(
                {"clip_id": clip.get("clip_id"), "status": "failed", "error": str(exc)}
            )
            print(f"[FAIL] {clip.get('clip_id')}: {exc}", file=sys.stderr)

    if not args.dry_run:
        (outdir / "motion_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
