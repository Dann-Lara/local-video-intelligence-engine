"""Renderiza los 5 jobs restantes, uno a la vez, con recuperacion de FPS.

Flujo por job:
1. `python -m services.delivery --stage all` (log aislado).
2. Si falla unicamente por `frame_rate`, normaliza a 30000/1001 en work/
   (sin tocar codigo compartido ni contratos) y reanuda
   validate_mp4,seo,validate_seo,manifest.
3. Verifica que los 4 entregables existen y que los hashes protegidos siguen ok.

Cualquier otro fallo detiene el proceso.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\SPARTAN PC\Documents\new_life\clipping")
RUN = ROOT / "work" / "run_20261116_new_source_full_pipeline"
JOBS = RUN / "jobs"
OUTPUT = ROOT / "output"
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT))

from services.delivery import baselines  # noqa: E402

STEMS = [
    "a7ace6f1_clip_0002_es_20261117",
    "a7ace6f1_clip_0003_es_20261117",
    "a7ace6f1_clip_0004_es_20261117",
    "a7ace6f1_clip_0008_es_20261117",
    "a7ace6f1_clip_0009_es_20261117",
]
RESUME_STAGES = "validate_mp4,seo,validate_seo,manifest"


def run(cmd: list[str], log: Path) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    text = (proc.stdout or "") + ("\nSTDERR:\n" + proc.stderr if proc.stderr else "")
    log.write_text(text, encoding="utf-8")
    print("$", " ".join(cmd))
    print(text[-2500:])
    return proc.returncode, text


def parse_failure(text: str) -> list[str]:
    marker = text.find("{")
    while marker >= 0:
        try:
            obj = json.JSONDecoder().raw_decode(text[marker:])[0]
        except Exception:
            marker = text.find("{", marker + 1)
            continue
        if isinstance(obj, dict) and obj.get("status") == "FAIL":
            return list((obj.get("details") or {}).get("failed") or [])
        marker = text.find("{", marker + 1)
    return []


def verify(stem: str) -> dict:
    for suffix in (".mp4", ".seo.md", ".manifest.json"):
        p = OUTPUT / f"{stem}{suffix}"
        if not p.exists() or p.stat().st_size == 0:
            raise SystemExit(f"Falta entregable {p}")
    prot = baselines.check_protected(OUTPUT)
    if not prot["all_ok"]:
        raise SystemExit(f"Hashes protegidos alterados tras {stem}")
    man = json.loads((OUTPUT / f"{stem}.manifest.json").read_text(encoding="utf-8"))
    if man.get("review_status") != "pending_user_review":
        raise SystemExit(f"Estado de revision incorrecto en {stem}: {man.get('review_status')}")
    hr = man.get("human_review") or {}
    if hr.get("approved") is not False:
        raise SystemExit(f"human_review.approved no es false en {stem}: {hr}")
    return {
        "stem": stem,
        "review_status": man.get("review_status"),
        "approved": hr.get("approved"),
        "protected_ok": prot["all_ok"],
        "mp4_bytes": (OUTPUT / f"{stem}.mp4").stat().st_size,
    }


def main() -> int:
    done = []
    for stem in STEMS:
        job = JOBS / f"{stem}.json"
        work = RUN / "delivery" / stem
        work.mkdir(parents=True, exist_ok=True)
        print("\n" + "=" * 100)
        print("RENDERIZANDO", stem)
        code, text = run([
            sys.executable, "-m", "services.delivery", "--job", str(job.relative_to(ROOT)),
            "--stage", "all", "--output-dir", "output", "--work-dir",
            str(work.relative_to(ROOT)),
        ], work / "full_run.log")
        if code != 0:
            failed = parse_failure(text)
            if failed != ["frame_rate"]:
                raise SystemExit(f"{stem}: fallo inesperado {failed or text[-800:]}")
            print("Fallo conocido de frame_rate; normalizando dentro de work/")
            code2, text2 = run([
                sys.executable, str((RUN / "normalize_fps.py").relative_to(ROOT)),
                str(work.relative_to(ROOT)),
            ], work / "fps.log")
            if code2 != 0:
                raise SystemExit(f"{stem}: normalizacion fallo: {text2[-800:]}")
            code3, text3 = run([
                sys.executable, "-m", "services.delivery", "--job",
                str(job.relative_to(ROOT)), "--stage", RESUME_STAGES,
                "--output-dir", "output", "--work-dir", str(work.relative_to(ROOT)),
            ], work / "resume.log")
            if code3 != 0:
                raise SystemExit(f"{stem}: reanudacion fallo: {text3[-1200:]}")
        info = verify(stem)
        done.append(info)
        print("OK", json.dumps(info, ensure_ascii=False))
    (RUN / "delivery_summary.json").write_text(
        json.dumps(done, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print("TODOS LOS JOBS COMPLETADOS:", len(done))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
