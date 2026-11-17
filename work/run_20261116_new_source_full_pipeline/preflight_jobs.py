"""Preflight de los 6 jobs antes de renderizar.

Verifica, sin renderizar ni publicar:
- 6 jobs y nombres de salida unicos, inexistentes en output/.
- rangos 0 <= start < end <= duracion; sin solape con PROTECTED_RANGES.
- todos los evidence_refs/facts existen y caen dentro del rango del clip.
- cada cita/fact es subcadena literal del segmento citado.
- candidate.review/human_review sigue pendiente y no aprobado.
- validate_candidate() del pipeline real devuelve 0 bloqueos.
- baselines.check_protected(output/) sigue en 10/10.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\SPARTAN PC\Documents\new_life\clipping")
RUN = ROOT / "work" / "run_20261116_new_source_full_pipeline"
JOBS = RUN / "jobs"
OUTPUT = ROOT / "output"
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT))

from services.delivery import baselines, pipeline  # noqa: E402

DURATION = 1586.340862
errors: list[str] = []
warnings: list[str] = []


def norm(text: str) -> str:
    return " ".join(str(text or "").split()).casefold()


jobs = sorted(JOBS.glob("a7ace6f1_clip_*_es_20261117.json"))
print("jobs:", len(jobs))
if len(jobs) != 6:
    errors.append(f"Se esperaban 6 jobs, hay {len(jobs)}")

tr = json.loads((ROOT / "projects" / "a7ace6f1-43b8-4769-9b67-58693575c499"
                 / "transcription" / "transcription.json").read_text(encoding="utf-8"))
by_id = {s["segment_id"]: s for s in tr["segments"]}

stems: set[str] = set()
for path in jobs:
    job = json.loads(path.read_text(encoding="utf-8"))
    stem = job["output_stem"]
    cand = job["candidate"]
    cid = cand["clip_id"]
    print("=" * 96)
    print(cid, stem)
    if stem in stems:
        errors.append(f"{cid}: stem duplicado {stem}")
    stems.add(stem)
    for suffix in (".mp4", ".manifest.json", ".seo.md"):
        if (OUTPUT / f"{stem}{suffix}").exists():
            errors.append(f"{cid}: ya existe entregable {stem}{suffix}")

    start = float(cand["start_seconds"])
    end = float(cand["end_seconds"])
    if not (0.0 <= start < end <= DURATION):
        errors.append(f"{cid}: rango invalido {start}-{end}")
    ov = baselines.overlap_seconds(start, end)
    nz = {k: round(v, 3) for k, v in ov.items() if v > 0}
    print("  rango:", f"{start:.2f}-{end:.2f}", "solape:", nz or "ninguno")
    if nz:
        errors.append(f"{cid}: solapa protegidos {nz}")

    refs = list(cand["evidence_refs"])
    if not refs:
        errors.append(f"{cid}: sin evidence_refs")
    for sid in refs:
        seg = by_id.get(sid)
        if seg is None:
            errors.append(f"{cid}: evidencia inexistente {sid}")
            continue
        if not (start - 1e-6 <= float(seg["start_seconds"])
                and float(seg["end_seconds"]) <= end + 1e-6):
            errors.append(f"{cid}: {sid} fuera del rango")

    hr = cand.get("human_review", {})
    if hr.get("approved") is not False or hr.get("status") != "pending_user_review":
        errors.append(f"{cid}: estado humano incorrecto {hr}")

    if cand.get("ad_classification") not in ("none", "incidental_mention"):
        errors.append(f"{cid}: ad_classification={cand.get('ad_classification')}")

    if len(job.get("emphasis", [])) != 0:
        errors.append(f"{cid}: emphasis no vacio (no hay word timestamps)")

    # Citas/facts literales contra la transcripcion.
    facts = job["seo"].get("facts", [])
    unsupported = []
    for fact in facts:
        claim = norm(fact["claim"])
        seg = by_id.get(fact["segment_id"])
        if seg is None:
            unsupported.append(f"{fact['segment_id']}: inexistente")
        elif claim not in norm(seg["text"]):
            unsupported.append(f"{fact['segment_id']}: {claim[:60]!r}")
    print("  facts:", len(facts), "sinrespaldo:", unsupported or "ninguno")
    if unsupported:
        errors.append(f"{cid}: facts sin respaldo literal {unsupported}")

    # Notificacion CTA: una, en es, dentro de duracion, sin inventar cifras.
    notifs = job.get("notifications", [])
    if len(notifs) != 1:
        errors.append(f"{cid}: {len(notifs)} notificaciones, se esperaba 1")
    for n in notifs:
        if n.get("subtitle_language") in ("en", "fr", "de", "pt"):
            errors.append(f"{cid}: CTA en idioma incorrecto")
        if float(n["exit_end_seconds"]) > (end - start):
            errors.append(f"{cid}: CTA fuera de duracion")
        if not set(n.get("evidence_refs", [])).issubset(refs):
            errors.append(f"{cid}: evidencia de CTA fuera del clip")

    blocked = pipeline.validate_candidate(job, source_duration=DURATION,
                                          output_dir=OUTPUT)
    print("  validate_candidate:", "OK" if not blocked else blocked)
    if blocked:
        errors.append(f"{cid}: validate_candidate {blocked}")

protected = baselines.check_protected(OUTPUT)
print("=" * 96)
print("protegidos:", protected.get("checked"), "all_ok:", protected.get("all_ok"))
if not protected.get("all_ok"):
    errors.append("protected hashes no ok")

print("ERRORES:", len(errors))
for e in errors:
    print(" -", e)
print("ADVERTENCIAS:", len(warnings))
for w in warnings:
    print(" -", w)
raise SystemExit(1 if errors else 0)
