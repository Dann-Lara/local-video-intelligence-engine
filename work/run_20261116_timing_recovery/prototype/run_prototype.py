"""Prototipo aislado: clip_0002 con corte de precision de frame.

Objetivo: demostrar, ANTES de cualquier render masivo, que la correccion
temporal (corte hibrido con recodificacion de video) atraviesa todo el pipeline
real (render, validate_mp4, seo, validate_seo, manifest) y conserva el diseno.

Reglas que respeta:
- No modifica `services/` ni configuracion protegida.
- Inyecta el corte en tiempo de ejecucion (monkeypatch de
  `services.delivery.pipeline.render_clip`).
- Usa un directorio de salida y de trabajo aislados.
- Copia los 10 archivos protegidos (sin tocarlos) para que la guardia
  `baselines.check_protected` opere igual que en produccion.
- Nombre nuevo: `clip_0002_timingfix_20261116`. No reutiliza MP4 previos.
- No publica ni aprueba: queda en `pending_user_review`.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(r"C:\Users\SPARTAN PC\Documents\new_life\clipping")
RUN = ROOT / "work" / "run_20261116_timing_recovery"
PROTO = RUN / "prototype"
JOBS = PROTO / "jobs"
OUTPUT = PROTO / "output"
WORK = PROTO / "work"

BASE_JOB = ROOT / "work" / "run_20261115_six_candidate_rerender" / "jobs" / "clip_0002_recovered_20261115.json"
JOB_ID = "clip_0002_timingfix_20261116_delivery"
STEM = "clip_0002_timingfix_20261116"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PROTO))

from services.delivery import baselines  # noqa: E402
from services.delivery import pipeline  # noqa: E402
from accurate_cut import render_clip_frame_accurate  # noqa: E402


def prepare_output_dir() -> dict:
    """Copia los 10 protegidos al directorio aislado y verifica hashes."""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    copied = []
    for name, expected in baselines.PROTECTED_SHA256.items():
        dest = OUTPUT / name
        if not dest.exists():
            shutil.copy2(ROOT / "output" / name, dest)
            copied.append(name)
        actual = baselines.sha256_file(dest)
        if actual != expected:
            raise SystemExit(f"Hash protegido no coincide en {dest}: {actual}")
    report = baselines.check_protected(OUTPUT)
    if not report["all_ok"]:
        raise SystemExit("Los protegidos copiados no pasan check_protected.")
    return {"copied": copied, "all_ok": report["all_ok"]}


def build_job() -> Path:
    job = json.loads(BASE_JOB.read_text(encoding="utf-8"))
    job["job_id"] = JOB_ID
    job["output_stem"] = STEM
    job.pop("expected_mp4_sha256", None)
    # Los tiempos editoriales NO cambian: 73.0 -> 134.0 (rec_0002).
    job["candidate"]["requested_start_seconds"] = 73.0
    job["candidate"]["requested_end_seconds"] = 134.0
    job["candidate"]["start_seconds"] = 73.0
    job["candidate"]["end_seconds"] = 134.0
    # Correccion de TEXTO SEO (no editorial): el validador interpreta los
    # apostrofes de "group's"/"line's" como comillas y exige literalidad.
    # Se reformula sin apostrofes, conservando exactamente las mismas
    # afirmaciones respaldadas por la transcripcion.
    job["seo"]["description"] = (
        "The first hiding spot of the group fails and Darius is arrested. "
        "The cops form a line and sweep the city; Jimmy explains the weakness "
        "of that line and says the cops do not know about the tunnels under "
        "the city.\n\n"
        "Jimmy says the cops do not realize there are tunnels under the city.\n\n"
        "A complete first-act beat: chase, first arrest, police strategy, and "
        "the counter-plan. It establishes the cost of failure and the plan "
        "that drives the next phase.\n\n"
        "Editorial excerpt from the original video, selected from the real "
        "transcript. It is not a guide and it is not a promise of results."
    )
    JOBS.mkdir(parents=True, exist_ok=True)
    path = JOBS / f"{STEM}.json"
    path.write_text(
        json.dumps(job, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


def main() -> int:
    resume = "--resume" in sys.argv[1:]
    if WORK.exists() and not resume:
        raise SystemExit(
            f"El directorio de trabajo ya existe: {WORK}. "
            "No se reutiliza un estado previo: usa otro nombre o --resume."
        )
    if not WORK.exists():
        WORK.mkdir(parents=True)

    protected = prepare_output_dir()
    job_path = build_job()

    def patched(**kwargs):
        return render_clip_frame_accurate(**kwargs, timeout=7200.0)

    original = pipeline.render_clip
    pipeline.render_clip = patched
    try:
        summary = pipeline.run_job(
            job_path,
            stages=["seo", "validate_seo", "manifest"] if resume else "all",
            force=resume,
            output_dir=OUTPUT,
            work_dir=WORK,
        )
    finally:
        pipeline.render_clip = original

    (PROTO / "run_summary.json").write_text(
        json.dumps(
            {
                "job_path": str(job_path),
                "output_dir": str(OUTPUT),
                "work_dir": str(WORK),
                "protected": protected,
                "summary": summary,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    print(json.dumps(
        {
            "protected": protected,
            "stages": [
                {"stage": s["stage"], "status": s["status"]}
                for s in summary["stages"]
            ],
        },
        indent=2,
        ensure_ascii=False,
    ))
    state = summary["state"]
    print(json.dumps({
        "actual_start_seconds": state["timing_alignment"]["clip_zero_maps_to_source_seconds"],
        "global_ncc": state["timing_alignment"]["global_ncc"],
        "deliverable": state.get("deliverable"),
        "validate_checks": (state.get("validate_mp4") or {}).get("checks"),
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
