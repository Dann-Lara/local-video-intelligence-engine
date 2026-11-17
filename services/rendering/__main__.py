"""CLI minimo para renderizar clips aprobados de un run editorial (Fase 5).

Uso (ejecutar desde la raiz del repositorio):

    python -m services.rendering \
        --candidates work/fase4d_.../editorial/candidates.json \
        --source "input/video.mp4" \
        --outdir work/fase5a_.../clips \
        [--clip-id clip_0001 --clip-id clip_0002]

- No modifica los artefactos de entrada ni el video original.
- Escribe los MP4 y un `render_manifest.json` en `--outdir`.
- Lee los tiempos y referencias directamente del `candidates.json`; no
  reconstruye datos de memoria.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .errors import RenderingError
from .renderer import render_clip


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m services.rendering",
        description="Renderiza clips desde un candidates.json editorial (Fase 5).",
    )
    parser.add_argument(
        "--candidates",
        required=True,
        help="Ruta a editorial/candidates.json con las propuestas aceptadas.",
    )
    parser.add_argument(
        "--source",
        required=True,
        help="Ruta al video fuente original (no se modifica).",
    )
    parser.add_argument(
        "--outdir",
        required=True,
        help="Carpeta de salida para los MP4 (se crea si no existe).",
    )
    parser.add_argument(
        "--clip-id",
        action="append",
        default=None,
        help="clip_id a renderizar (repetible). Por defecto, todos los candidatos.",
    )
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    candidates_path = Path(args.candidates)
    source_path = Path(args.source)
    outdir = Path(args.outdir)

    data = json.loads(candidates_path.read_text(encoding="utf-8"))
    candidates = data.get("candidates", [])
    wanted = set(args.clip_id) if args.clip_id else None

    selected = []
    for cand in candidates:
        proposal = cand.get("proposal", {})
        clip_id = proposal.get("clip_id")
        if wanted is not None and clip_id not in wanted:
            continue
        selected.append(cand)

    if not selected:
        print("No hay candidatos que coincidan; nada que renderizar.", file=sys.stderr)
        return 2

    outdir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "source_candidates": str(candidates_path),
        "source_video": str(source_path),
        "run_created_at": data.get("created_at"),
        "prompt_version": data.get("engine", {}).get("prompt_version"),
        "model_id": data.get("engine", {}).get("model_id"),
        "clips": [],
    }

    exit_code = 0
    for cand in selected:
        proposal = cand["proposal"]
        clip_id = proposal["clip_id"]
        start = float(proposal["start_seconds"])
        end = float(proposal["end_seconds"])
        title = proposal.get("title", clip_id)
        output_path = outdir / f"{clip_id}.mp4"
        entry = {
            "clip_id": clip_id,
            "title": title,
            "requested_start_seconds": start,
            "requested_end_seconds": end,
            "output_path": str(output_path),
        }
        try:
            result = render_clip(
                source_path=source_path,
                start_seconds=start,
                end_seconds=end,
                output_path=output_path,
            )
            entry["status"] = "ok"
            entry.update(result)
            print(
                f"[OK] {clip_id} -> {output_path} "
                f"({result['actual_duration_seconds']:.3f}s, "
                f"{result['width']}x{result['height']}, "
                f"{result['video_codec']}/{result['audio_codec']})"
            )
        except RenderingError as exc:
            entry["status"] = "failed"
            entry["error"] = str(exc)
            entry["error_details"] = getattr(exc, "details", {})
            exit_code = 1
            print(f"[FAIL] {clip_id}: {exc}", file=sys.stderr)
        manifest["clips"].append(entry)

    (outdir / "render_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
