"""Verificacion previa/posterior al commit: hashes, integridad y entregables.

Solo lectura sobre originales y entregables; escribe unicamente dentro de
work/run_20261117_git_audio_audit/. No renderiza, no modifica MP4, audio ni
configuracion compartida.

Uso:
  python verify_integrity.py before
  python -m pytest -q
  python verify_integrity.py after
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\SPARTAN PC\Documents\new_life\clipping")
RUN = ROOT / "work" / "run_20261116_new_source_full_pipeline"
AUDIT = ROOT / "work" / "run_20261117_git_audio_audit"
OUTPUT = ROOT / "output"
SNAPSHOT = AUDIT / "hashes_snapshot.json"
RESULT = AUDIT / "verification_result.json"
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT))

from services.delivery import baselines, pipeline  # noqa: E402

STEMS = [
    "a7ace6f1_clip_0001_es_20261117",
    "a7ace6f1_clip_0002_es_20261117",
    "a7ace6f1_clip_0003_es_20261117",
    "a7ace6f1_clip_0004_es_20261117",
    "a7ace6f1_clip_0008_es_20261117",
    "a7ace6f1_clip_0009_es_20261117",
]


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def targets() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for stem in STEMS:
        out[f"{stem}.mp4"] = OUTPUT / f"{stem}.mp4"
        out[f"{stem}.seo.md"] = OUTPUT / f"{stem}.seo.md"
        out[f"{stem}.manifest.json"] = OUTPUT / f"{stem}.manifest.json"
    for name in baselines.PROTECTED_SHA256:
        out[f"protected:{name}"] = OUTPUT / name
    source = next(ROOT.joinpath("input").glob("*español*.mp4"))
    out["source:español"] = source
    out["report:verification_final.json"] = RUN / "verification_final.json"
    return out


def hashes() -> dict[str, dict]:
    return {
        key: {"bytes": p.stat().st_size, "sha256": sha(p)}
        for key, p in targets().items()
    }


def deliverable_checks() -> dict:
    clips = []
    errors = []
    for stem in STEMS:
        mp4 = OUTPUT / f"{stem}.mp4"
        seo = OUTPUT / f"{stem}.seo.md"
        man_path = OUTPUT / f"{stem}.manifest.json"
        state_path = RUN / "delivery" / stem / "state.json"
        if not all(p.exists() for p in (mp4, seo, man_path, state_path)):
            errors.append(f"{stem}: falta archivo")
            continue
        state = json.loads(state_path.read_text(encoding="utf-8"))
        man = json.loads(man_path.read_text(encoding="utf-8"))
        probe = pipeline.ffprobe_json(mp4)
        checks = state["validate_mp4"]["checks"]
        seo_val = state["validate_seo"]["result"]
        align = state["timing_alignment"]
        item = {
            "stem": stem,
            "exists": True,
            "decode_integrity": state["validate_mp4"]["decode_integrity"],
            "mp4_checks": f"{sum(1 for v in checks.values() if v)}/{len(checks)}",
            "seo_checks": f"{sum(1 for v in seo_val['checks'].values() if v)}/{len(seo_val['checks'])}",
            "seo_valid": seo_val["valid"],
            "fps": next(s for s in probe["streams"] if s["codec_type"] == "video")["r_frame_rate"],
            "start_delta_seconds": round(
                align["clip_zero_maps_to_source_seconds"]
                - state["job"]["candidate"]["start_seconds"], 6
            ) if "job" in state else None,
            "overlap_seconds": state["overlap_seconds"],
            "review_status": man.get("review_status"),
            "approved": (man.get("human_review") or {}).get("approved"),
            "cut_method": man["provenance"]["source_cut"]["method"],
        }
        failures = []
        if not all(checks.values()):
            failures.append("checks MP4")
        if not seo_val["valid"]:
            failures.append("SEO")
        if item["fps"] != "30000/1001":
            failures.append("fps")
        if any(v > 0 for v in item["overlap_seconds"].values()):
            failures.append("overlap")
        if man.get("review_status") != "pending_user_review":
            failures.append("review_status")
        if item["approved"] is not False:
            failures.append("human_review.approved")
        if not state["validate_mp4"]["decode_integrity"]:
            failures.append("decode_integrity")
        item["failures"] = failures
        if failures:
            errors.append(f"{stem}: {failures}")
        clips.append(item)

    protected = baselines.check_protected(OUTPUT)
    if not protected["all_ok"]:
        errors.append("hashes protegidos")
    return {
        "clips": clips,
        "protected_all_ok": protected["all_ok"],
        "protected_files_ok": sum(1 for v in protected["files"].values() if v["ok"]),
        "protected_files_total": len(protected["files"]),
        "errors": errors,
        "ok": not errors,
    }


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "after"
    current = hashes()
    if mode == "before":
        SNAPSHOT.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
        print("snapshot ANTES guardado:", len(current), "archivos")
        return 0
    before = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    changed = [
        key for key, value in current.items()
        if key not in before or before[key] != value
    ]
    added = sorted(set(current) - set(before))
    removed = sorted(set(before) - set(current))
    checks = deliverable_checks()
    payload = {
        "mode": mode,
        "before_files": len(before),
        "after_files": len(current),
        "changed": changed,
        "added": added,
        "removed": removed,
        "hashes_identical": not changed and not added and not removed,
        "deliverables": checks,
        "ok": checks["ok"] and not changed and not added and not removed,
    }
    RESULT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    print("hashes identicos:", payload["hashes_identical"],
          "cambios:", changed, "anadidos:", added, "quitados:", removed)
    for c in checks["clips"]:
        print(c["stem"], "mp4", c["mp4_checks"], "seo", c["seo_checks"],
              "fps", c["fps"], "estado", c["review_status"],
              "approved", c["approved"], "fallos", c["failures"] or "ninguno")
    print("protegidos:", checks["protected_files_ok"], "/",
          checks["protected_files_total"], "ok:", checks["ok"])
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
