"""Extiende clip_0009 con segmentos reales para dar cierre natural y evidencia de idioma.

No modifica transcripcion, codigo compartido ni protecciones. Solo reescribe el
job aislado y selection.json, con trazabilidad de la decision humana.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\SPARTAN PC\Documents\new_life\clipping")
RUN = ROOT / "work" / "run_20261116_new_source_full_pipeline"
JOB = RUN / "jobs" / "a7ace6f1_clip_0009_es_20261117.json"
sys.stdout.reconfigure(encoding="utf-8")

NEW_END = 1531.52
NEW_IDS = [f"seg_{i:04d}" for i in range(925, 935)]
NOTE = (
    "Extension editorial humana: se anaden seg_0928 a seg_0934 hasta el limite real "
    "de Whisper 1531.52 s para dar un cierre natural ('Muy muy bien para un carro de "
    "calle') y reunir evidencia suficiente de idioma (6 palabras funcionales era "
    "insuficiente para el Detector; 20 con la extension). No se relaja ningun umbral."
)


def main() -> int:
    job = json.loads(JOB.read_text(encoding="utf-8"))
    cand = job["candidate"]
    if cand["clip_id"] != "clip_0009":
        raise SystemExit("Job inesperado")
    tr = json.loads((ROOT / "projects" / job["project_id"] / "transcription"
                     / "transcription.json").read_text(encoding="utf-8"))
    by = {s["segment_id"]: s for s in tr["segments"]}
    for sid in NEW_IDS:
        if sid not in by:
            raise SystemExit(f"Falta {sid}")
    if float(by[NEW_IDS[-1]]["end_seconds"]) != NEW_END:
        raise SystemExit("El nuevo fin no coincide con Whisper")

    cand["original_end_seconds"] = cand["end_seconds"]
    cand["requested_end_seconds"] = NEW_END
    cand["end_seconds"] = NEW_END
    cand["evidence_refs"] = NEW_IDS
    cand["human_review"]["extension_note"] = NOTE
    cand["human_review"]["approved"] = False
    cand["boundary_evaluation"]["notes"] = (
        str(cand["boundary_evaluation"].get("notes", "")) + " " + NOTE
    ).strip()

    seo = job["seo"]
    seo["title"] = "432 caballos en la medición final"
    seo["alt_titles"] = [
        "La medición final del Supra turbo",
        "Se fue a 16 libras y dio 432 caballos",
    ]
    seo["facts"] = [
        {"claim": "Se fue a 16 libras", "segment_id": "seg_0925"},
        {"claim": "Y nos dio 432 caballos", "segment_id": "seg_0926"},
        {"claim": "Ya estamos del otro lado", "segment_id": "seg_0927"},
        {"claim": "432 yo creo que está", "segment_id": "seg_0933"},
        {"claim": "Muy muy bien para un carro de calle", "segment_id": "seg_0934"},
    ]
    seo["notes"] = (
        seo["notes"] + " " + NOTE
        + " Se retira '290' del titulo porque esa cifra no aparece en este clip."
    )
    JOB.write_text(json.dumps(job, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")

    sel_path = RUN / "selection.json"
    sel = json.loads(sel_path.read_text(encoding="utf-8"))
    sel["human_extension"] = {
        "clip_id": "clip_0009",
        "from_end_seconds": float(by["seg_0927"]["end_seconds"]),
        "to_end_seconds": NEW_END,
        "added_refs": NEW_IDS[3:],
        "reason": NOTE,
    }
    for item in sel["selected"]:
        if item["clip_id"] == "clip_0009":
            item["end_seconds"] = NEW_END
            item["duration_seconds"] = round(NEW_END - item["start_seconds"], 3)
            item["refs"] = len(NEW_IDS)
    sel_path.write_text(json.dumps(sel, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print("OK clip_0009", NEW_END, len(NEW_IDS), "refs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
