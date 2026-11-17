"""Verificacion final integral de los 6 entregables nuevos.

Solo lectura: recopila evidencia tecnica, SEO, manifiesto, estado, alineacion,
hashes protegidos e inventario. Escribe work/.../verification_final.json.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\SPARTAN PC\Documents\new_life\clipping")
RUN = ROOT / "work" / "run_20261116_new_source_full_pipeline"
OUTPUT = ROOT / "output"
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


def main() -> int:
    report = {"clips": [], "protected": None, "inventory": [], "errors": []}
    for stem in STEMS:
        mp4 = OUTPUT / f"{stem}.mp4"
        seo = OUTPUT / f"{stem}.seo.md"
        man_path = OUTPUT / f"{stem}.manifest.json"
        state_path = RUN / "delivery" / stem / "state.json"
        if not all(p.exists() for p in (mp4, seo, man_path, state_path)):
            report["errors"].append(f"{stem}: falta archivo")
            continue
        state = json.loads(state_path.read_text(encoding="utf-8"))
        man = json.loads(man_path.read_text(encoding="utf-8"))
        probe = pipeline.ffprobe_json(mp4)
        video = next(s for s in probe["streams"] if s["codec_type"] == "video")
        audio = next(s for s in probe["streams"] if s["codec_type"] == "audio")
        vm = state["validate_mp4"]
        checks = vm["checks"]
        seo_val = state["validate_seo"]["result"]
        align = state["timing_alignment"]
        final_align = vm["final_alignment"]
        render_art = state["render"]["artifacts"]
        job = json.loads((RUN / "jobs" / f"{stem}.json").read_text(encoding="utf-8"))
        cand = job["candidate"]
        item = {
            "stem": stem,
            "clip_id": cand["clip_id"],
            "files": {
                name: {"bytes": p.stat().st_size, "sha256": sha(p)}
                for name, p in (("mp4", mp4), ("seo", seo), ("manifest", man_path))
            },
            "range_source_seconds": [cand["start_seconds"], cand["end_seconds"]],
            "actual_start_seconds": align["clip_zero_maps_to_source_seconds"],
            "start_delta_seconds": round(
                align["clip_zero_maps_to_source_seconds"] - cand["start_seconds"], 6),
            "alignment_ncc": align["global_ncc"],
            "final_alignment_ncc": final_align["global_ncc"],
            "overlap_seconds": state["overlap_seconds"],
            "container": {
                "video_codec": video["codec_name"],
                "video_profile": video["profile"],
                "pix_fmt": video["pix_fmt"],
                "width": video["width"], "height": video["height"],
                "r_frame_rate": video["r_frame_rate"],
                "nb_frames": video.get("nb_frames"),
                "duration_seconds": float(probe["format"]["duration"]),
                "audio_codec": audio["codec_name"],
                "audio_profile": audio["profile"],
                "sample_rate": audio["sample_rate"],
                "channels": audio["channels"],
            },
            "checks_total": len(checks),
            "checks_passed": sum(1 for v in checks.values() if v),
            "checks_all_ok": all(checks.values()),
            "decode_integrity": vm["decode_integrity"],
            "fps_normalization": render_art.get("fps_normalization"),
            "cut_method": man["provenance"]["source_cut"]["method"],
            "cut_method_evidence": render_art["source_cut"].get("cut_method"),
            "click": render_art.get("audio_mix"),
            "seo": {
                "language": seo_val["language"]["seo_language"],
                "valid": seo_val["valid"],
                "checks_total": len(seo_val["checks"]),
                "checks_passed": sum(1 for v in seo_val["checks"].values() if v),
                "facts": len(job["seo"]["facts"]),
                "sources": len(job["seo"]["sources"]),
                "output": str(seo),
            },
            "manifest": {
                "status": man.get("status"),
                "review_status": man.get("review_status"),
                "human_review": man.get("human_review"),
                "protected_ok": all(
                    row.get("ok", True)
                    for row in (man.get("protected_baselines") or {}).values()
                    if isinstance(row, dict)
                ) if isinstance(man.get("protected_baselines"), dict) else None,
                "output": str(man_path),
            },
            "stages": {k: v.get("status") for k, v in state["stages"].items()},
        }
        failures = []
        if not item["checks_all_ok"]:
            failures.append("checks MP4")
        if not item["seo"]["valid"]:
            failures.append("SEO")
        if man.get("review_status") != "pending_user_review":
            failures.append("review_status")
        if (man.get("human_review") or {}).get("approved") is not False:
            failures.append("human_review.approved")
        if item["container"]["r_frame_rate"] != "30000/1001":
            failures.append("fps")
        if any(v > 0 for v in item["overlap_seconds"].values()):
            failures.append("overlap")
        if item["alignment_ncc"] < 0.90 or item["final_alignment_ncc"] < 0.90:
            failures.append("ncc")
        item["failures"] = failures
        if failures:
            report["errors"].append(f"{stem}: {failures}")
        report["clips"].append(item)

    report["protected"] = baselines.check_protected(OUTPUT)
    if not report["protected"]["all_ok"]:
        report["errors"].append("hashes protegidos")
    for p in sorted(OUTPUT.iterdir()):
        if p.is_file():
            report["inventory"].append({"name": p.name, "bytes": p.stat().st_size,
                                        "sha256": sha(p)})
    source = next(ROOT.joinpath("input").glob("*español*.mp4"))
    report["source"] = {"name": source.name, "bytes": source.stat().st_size,
                        "sha256": sha(source)}
    report["ok"] = not report["errors"]
    out = RUN / "verification_final.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    for c in report["clips"]:
        print(c["clip_id"], c["container"]["duration_seconds"],
              "startΔ", c["start_delta_seconds"], "ncc", round(c["alignment_ncc"], 6),
              "checks", f"{c['checks_passed']}/{c['checks_total']}",
              "seo", f"{c['seo']['checks_passed']}/{c['seo']['checks_total']}",
              "failures", c["failures"] or "ninguno")
    print("protegidos:", report["protected"]["all_ok"],
          "inventario:", len(report["inventory"]), "errores:", report["errors"])
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
