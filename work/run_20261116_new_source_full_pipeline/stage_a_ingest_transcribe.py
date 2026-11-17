"""Etapa A: ingesta del video nuevo y transcripcion local (idempotente).

- Selecciona el unico video de `input/` sin proyecto conocido (por hash).
- Verifica que no exista transcripcion valida reutilizable.
- Ingiere y transcribe con el modelo local ya instalado (sin descargas).
- Escribe `stage_a.json` con identification, idioma y estado.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\SPARTAN PC\Documents\new_life\clipping")
RUN = ROOT / "work" / "run_20261116_new_source_full_pipeline"
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT))

from services.ingestion.ingest import ingest_file  # noqa: E402
from services.transcription import transcribe_project  # noqa: E402
from services.transcription.store import TranscriptionStore  # noqa: E402


def main() -> int:
    inventory = json.loads((RUN / "input_inventory.json").read_text(encoding="utf-8"))
    new = [v for v in inventory["videos"] if not v["known_project"]]
    if len(new) != 1:
        raise SystemExit(f"Se esperaba 1 video nuevo; hay {len(new)}: {new}")
    video = new[0]
    source = Path(video["path"])
    if not source.exists() or source.stat().st_size != video["bytes"]:
        raise SystemExit("El archivo nuevo cambio desde el inventario.")

    result = ingest_file(source)
    project_dir = Path(result.project_json_path).parent
    project_id = result.project.project_id

    store = TranscriptionStore(project_root=ROOT / "projects")
    prior = None
    try:
        prior = store.load_transcription(project_id)
    except Exception as exc:  # no existe / no es reusable
        prior_error = type(exc).__name__
    else:
        prior_error = None

    if prior is not None:
        print("Transcripcion previa reutilizable; no se re-transcribe.")
        transcription = prior
    else:
        print(f"Sin transcripcion reutilizable ({prior_error}). Transcribiendo localmente...")
        transcription = transcribe_project(project_dir)

    payload = {
        "source": video,
        "project_id": project_id,
        "project_dir": str(project_dir),
        "ingestion_created": result.created,
        "prior_transcription": prior_error or "reused",
        "language": transcription.language,
        "model_size": transcription.model_size,
        "device": transcription.device,
        "compute_type": transcription.compute_type,
        "inference_completed": transcription.inference_completed,
        "duration_seconds": transcription.duration_seconds,
        "segment_count": len(transcription.segments),
        "word_count": len(transcription.words),
        "first_segments": [
            {"id": s.segment_id, "start": s.start_seconds, "end": s.end_seconds,
             "text": s.text}
            for s in transcription.segments[:5]
        ],
        "transcription_path": str(project_dir / "transcription" / "transcription.json"),
    }
    (RUN / "stage_a.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False)[:3500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
