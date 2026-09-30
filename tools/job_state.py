"""Muestra el estado de un job: etapas, artefactos y log de transiciones.

Util para depurar el pipeline a mano sin escribir SQL. El comando normal para
avanzar de etapa es el correspondiente (`analyze`, `candidates`, `render`...).

Uso:
    python tools/job_state.py              # lista todos los jobs
    python tools/job_state.py <job_id>     # detalle de un job
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core import config as config_module  # noqa: E402
from src.core import db as db_module  # noqa: E402

config = config_module.load()
repo = db_module.JobRepository(db_module.open_database(config))

if len(sys.argv) < 2:
    for job in repo.list_jobs():
        print(f"{job['id']}  {job['state']}  {job['video_id']}")
    raise SystemExit(0)

job_id = sys.argv[1]
job = repo.get_job(job_id)
if job is None:
    raise SystemExit(f"job desconocido: {job_id}")

video = repo.get_video(job["video_id"])
print("job       ", job["id"], job["state"])
print("video     ", video["path"] if video else "-")
print("etapas    ", ", ".join(str(s) for s in repo.completed_stages(job_id)))
print("siguiente ", repo.resume_stage(job_id))
print()
for artifact in repo.list_artifacts(job_id):
    digest = (artifact["sha256"] or "")[:12]
    print(f"  {artifact['stage']:<22} {artifact['kind']:<28} {digest:<12} {artifact['path']}")
print()
print(json.dumps(repo.timeline(job_id), ensure_ascii=False, indent=2, default=str))
