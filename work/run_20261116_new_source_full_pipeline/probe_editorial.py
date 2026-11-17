"""Sondeo de 1 ventana editorial con configuracion conservadora.

Diagnostica si LM Studio (google/gemma-3-4b) produce JSON valido con ventanas
del tamano de un Short (~50 s) antes de relanzar Stage B completo.

Solo lectura de la transcripcion; no escribe artefactos del servicio editorial.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(r"C:\Users\SPARTAN PC\Documents\new_life\clipping")
RUN = ROOT / "work" / "run_20261116_new_source_full_pipeline"
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT))

from schemas.contracts import TranscriptSegment  # noqa: E402
from services.editorial.client import LMStudioClient  # noqa: E402
from services.editorial.config import EditorialConfig  # noqa: E402
from services.editorial.parse import parse_model_response  # noqa: E402
from services.editorial.prompt import (  # noqa: E402
    build_system_prompt,
    build_window_user_prompt,
)
from services.editorial.windows import build_windows, index_segments  # noqa: E402
from services.transcription.store import TranscriptionStore  # noqa: E402


def main() -> int:
    stage_a = json.loads((RUN / "stage_a.json").read_text(encoding="utf-8"))
    project_id = stage_a["project_id"]
    tr = TranscriptionStore(project_root=ROOT / "projects").load_transcription(project_id)
    segments = list(tr.segments)
    by_id = index_segments(segments)

    cfg = EditorialConfig(
        model_id="google/gemma-3-4b",
        temperature=0.0,
        max_tokens=6000,
        request_timeout=300.0,
        max_repair_attempts=3,
        window_target_chars=1800,
        window_hard_max_chars=2600,
        overlap_segments=1,
        context_segments=1,
        max_candidates_per_window=1,
        max_candidates_total=20,
    )
    windows = build_windows(
        segments,
        target_chars=cfg.window_target_chars,
        hard_max_chars=cfg.window_hard_max_chars,
        overlap_segments=cfg.overlap_segments,
        context_segments=cfg.context_segments,
    )
    print("ventanas:", len(windows), "cuerpo win_0001:", len(windows[0].body_ids))

    w = windows[0]
    user = build_window_user_prompt(
        window_id=w.window_id,
        body_ids=list(w.body_ids),
        context_before_ids=list(w.context_before_ids),
        context_after_ids=list(w.context_after_ids),
        segments_by_id=by_id,
        language="es",
        duration_seconds=1586.340862,
        max_candidates=cfg.max_candidates_per_window,
        clip_min_seconds=cfg.clip_min_seconds,
        clip_target_seconds=cfg.clip_target_seconds,
        clip_max_seconds=cfg.clip_max_seconds,
    )
    print("chars prompt usuario:", len(user))
    client = LMStudioClient(
        base_url=cfg.base_url, model_id=cfg.model_id,
        temperature=cfg.temperature, max_tokens=cfg.max_tokens,
        timeout=cfg.request_timeout,
    )
    t0 = time.time()
    raw = client.complete(system_prompt=build_system_prompt(), user_prompt=user)
    print("segundos respuesta:", round(time.time() - t0, 2), "chars raw:", len(raw))
    print("RAW (primeros 500):", raw[:500].replace("\n", " "))

    counter = [0]

    def factory():
        counter[0] += 1
        return f"probe_{counter[0]:04d}"

    try:
        cands, rejected = parse_model_response(
            raw,
            source_asset_id=stage_a["source"]["sha256"],
            window_id=w.window_id,
            body_ids=list(w.body_ids),
            segments_by_id=by_id,
            config=cfg,
            duration_seconds=1586.340862,
            clip_id_factory=factory,
        )
    except Exception as exc:
        print("PARSE ERROR:", type(exc).__name__, str(exc)[:300])
        return 1
    print("candidatos:", len(cands), "rechazos:", len(rejected))
    for c in cands:
        print(json.dumps({
            "id": c.proposal.clip_id,
            "start": c.proposal.start_seconds,
            "end": c.proposal.end_seconds,
            "dur": round(c.duration_seconds, 2),
            "n_segmentos": len(c.proposal.transcript_refs),
            "decision": c.editorial_decision,
            "ad": c.ad_classification,
            "title": c.proposal.title,
        }, ensure_ascii=False))
    for r in rejected[:5]:
        print("rechazo:", r.get("reason"), r.get("detail"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
