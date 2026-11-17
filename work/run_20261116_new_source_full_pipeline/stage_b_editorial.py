"""Etapa B: analisis editorial con LM Studio local sobre la transcripcion nueva.

No modifica la transcripcion ni aprueba candidatos. Guarda el run oficial del
servicio editorial en projects/<id>/editorial/ y un resumen legible en el run.
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

from services.editorial import (  # noqa: E402
    EditorialConfig,
    EditorialOrchestrator,
    PROMPT_VERSION,
)


def main() -> int:
    stage_a = json.loads((RUN / "stage_a.json").read_text(encoding="utf-8"))
    project_dir = Path(stage_a["project_dir"])

    config = EditorialConfig(
        model_id="google/gemma-3-4b",
        temperature=0.0,
        max_tokens=6000,
        request_timeout=300.0,
        max_repair_attempts=3,
        # Ventanas ~50 s: el modelo 4B volcaba todos los segment_id de ventanas
        # de ~7 min y truncaba el JSON. Tamano calibrado con `probe_editorial.py`.
        window_target_chars=900,
        window_hard_max_chars=1400,
        overlap_segments=1,
        context_segments=1,
        max_candidates_per_window=1,
        max_candidates_total=20,
    )
    print("Config editorial:", json.dumps({
        "model_id": config.model_id,
        "base_url": config.base_url,
        "prompt_version": PROMPT_VERSION,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "window_target_chars": config.window_target_chars,
        "max_repair_attempts": config.max_repair_attempts,
    }, ensure_ascii=False))
    run = EditorialOrchestrator(project_root=None, config=config).analyze_project(project_dir)

    candidates = []
    for c in run.candidates:
        candidates.append({
            "clip_id": c.clip_id,
            "title": c.proposal.title,
            "start_seconds": c.proposal.start_seconds,
            "end_seconds": c.proposal.end_seconds,
            "duration_seconds": c.duration_seconds,
            "decision": c.editorial_decision,
            "ad_classification": c.ad_classification,
            "context_dependency": c.context_dependency,
            "boundary_evaluation": c.boundary_evaluation,
            "evidence_text": c.evidence_text,
            "editorial_reason": c.proposal.editorial_reason,
            "hook": c.proposal.hook,
            "main_idea": c.proposal.main_idea,
            "ending": c.proposal.ending,
            "transcript_refs": c.proposal.transcript_refs,
            "confidence": c.proposal.confidence,
            "window": c.originated_from_window,
            "review_status": str(c.proposal.review_status.value),
        })

    payload = {
        "project_id": stage_a["project_id"],
        "model_id": config.model_id,
        "prompt_version": PROMPT_VERSION,
        "status": str(run.status),
        "window_count": len(run.windows),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "ad_filter_summary": getattr(run, "ad_filter_summary", None),
        "candidates_path": str(project_dir / "editorial" / "candidates.json"),
        "diagnostics_path": str(project_dir / "editorial" / "diagnostics.json"),
    }
    (RUN / "stage_b.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        k: payload[k] for k in ("status", "window_count", "candidate_count")
    }, ensure_ascii=False))
    for c in candidates:
        print(json.dumps({k: c[k] for k in (
            "clip_id", "start_seconds", "end_seconds", "decision",
            "ad_classification")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
