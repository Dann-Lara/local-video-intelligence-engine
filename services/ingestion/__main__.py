"""CLI de la Etapa A: ingesta de archivos locales.

Uso (desde la raiz del repositorio):

    python -m services.ingestion --file "input\\mi video.mp4"
    python -m services.ingestion --input-dir input
    python -m services.ingestion              # usa input/ por defecto

Codigos de salida: 0 = todo correcto, 1 = al menos un error (el detalle
se imprime en stderr, con ruta y causa).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional, Sequence, TextIO

from .errors import IngestionError
from .ingest import (
    IngestionResult,
    default_input_dir,
    default_project_root,
    ingest_directory,
    ingest_file,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m services.ingestion",
        description="Ingiere archivos de video locales y crea el registro de proyecto "
        "con los metadatos de FFprobe. No modifica el archivo original.",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--file", help="Ruta de un archivo fuente concreto")
    source.add_argument(
        "--input-dir",
        help="Directorio con los archivos a ingerir (por defecto: input/ del repositorio)",
    )
    parser.add_argument(
        "--project-root",
        help="Directorio donde se crean los proyectos (por defecto: projects/ del repositorio)",
    )
    parser.add_argument("--ffprobe", help="Ruta o nombre del ejecutable de FFprobe")
    parser.add_argument(
        "--timeout", type=float, help="Segundos de espera por invocacion de FFprobe"
    )
    return parser


def summarize(result: IngestionResult) -> Any:
    project = result.project
    return {
        "created": result.created,
        "project_id": project.project_id,
        "status": project.status,
        "source_asset_id": project.source_asset_id,
        "duration_seconds": project.duration_seconds,
        "source_path": project.source_path,
        "metadata": project.metadata,
        "project_json": str(result.project_json_path),
        "source_metadata_json": str(result.source_metadata_path),
    }


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    runner: Any = None,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
) -> int:
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    args = build_parser().parse_args(argv)

    project_root = args.project_root
    runner_kwargs = {
        "project_root": project_root,
        "runner": runner,
        "ffprobe_executable": args.ffprobe,
        "timeout": args.timeout,
    }

    if args.file:
        try:
            result = ingest_file(args.file, **runner_kwargs)
        except IngestionError as exc:
            print(f"ERROR: {exc}", file=err)
            return 1
        print(json.dumps(summarize(result), ensure_ascii=False, indent=2), file=out)
        return 0

    input_dir = args.input_dir or str(default_input_dir())
    try:
        directory_result = ingest_directory(input_dir, **runner_kwargs)
    except IngestionError as exc:
        print(f"ERROR: {exc}", file=err)
        return 1

    if not directory_result.results and not directory_result.failures:
        print(f"No se encontraron archivos en: {input_dir}", file=out)
        return 0

    payload = [summarize(item) for item in directory_result.results]
    print(json.dumps(payload, ensure_ascii=False, indent=2), file=out)
    for path, error in directory_result.failures:
        print(f"ERROR: {path}: {error}", file=err)
    return 1 if directory_result.failures else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
