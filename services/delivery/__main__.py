"""CLI de entrega: `python -m services.delivery --job <job.json> --stage all`.

La ejecucion es reanudable: las etapas ya completadas se omiten salvo `--force`.
Un fallo de SEO no obliga a volver a renderizar.
"""

from __future__ import annotations

import argparse
import json
import sys

from .pipeline import STAGES, DeliveryError, run_job


def _summary(result: dict) -> dict:
    state = result["state"]
    deliverable = state.get("deliverable", {})
    return {
        "job_id": result["job_id"],
        "work_dir": result["work_dir"],
        "state": result["state_path"],
        "stages": result["stages"],
        "deliverable": {
            "file": deliverable.get("file_name"),
            "sha256": deliverable.get("sha256"),
            "bytes": deliverable.get("bytes"),
            "duration_seconds": deliverable.get("duration_seconds"),
        },
        "seo": (state.get("seo") or {}).get("output_path"),
        "manifest": (state.get("manifest") or {}).get("output_path"),
        "review_status": "pending_user_review",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m services.delivery",
        description="Render, valida, genera SEO y manifiesto de un Short.",
    )
    parser.add_argument("--job", required=True, help="Ruta del job JSON.")
    parser.add_argument(
        "--stage",
        default="all",
        help=f"Etapa o lista separada por comas. Validas: all,{','.join(STAGES)}",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reejecuta la etapa aunque conste como completada.",
    )
    parser.add_argument("--output-dir", default=None, help="Carpeta de salida.")
    parser.add_argument("--work-dir", default=None, help="Carpeta de trabajo.")
    args = parser.parse_args(argv)

    try:
        result = run_job(
            args.job,
            stages=args.stage,
            force=args.force,
            output_dir=args.output_dir,
            work_dir=args.work_dir,
        )
    except DeliveryError as exc:
        print(json.dumps({
            "status": "FAIL",
            "error": str(exc),
            "details": exc.details,
        }, indent=2, ensure_ascii=False))
        return 1
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({
            "status": "FAIL",
            "error": str(exc),
            "type": type(exc).__name__,
        }, indent=2, ensure_ascii=False))
        return 1

    print(json.dumps(_summary(result), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
