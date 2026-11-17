"""Evidencia del solapamiento real de rec_0006 con los rangos protegidos."""
from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(r"C:\Users\SPARTAN PC\Documents\new_life\clipping")
DIAG = ROOT / "work" / "run_20261116_timing_recovery" / "diagnosis"
CAND = ROOT / "work" / "run_20261106_editorial_recovery" / "candidates_recovered.json"
TRANS = ROOT / "projects" / "d3d5157c-542e-4967-b87c-86f254ef5602" / "transcription" / "transcription.json"

PROTECTED = {
    "clip_0002": (1099.488073, 1131.353240),
    "clip_0004": (1131.485170, 1176.206168),
}


def words(text: str):
    return re.findall(r"[a-z0-9']+", text.lower())


def main() -> int:
    cands = json.loads(CAND.read_text(encoding="utf-8"))["candidates"]
    rec6 = next(c for c in cands if c["id"] == "rec_0006")
    rec6_start = float(rec6["start_seconds"])
    rec6_end = float(rec6["end_seconds"])
    trans = json.loads(TRANS.read_text(encoding="utf-8"))
    segments = trans["segments"]

    def text_in_range(start, end):
        chosen = [
            s for s in segments
            if s["end_seconds"] > start + 1e-6 and s["start_seconds"] < end - 1e-6
        ]
        return chosen, " ".join(s["text"].strip() for s in chosen)

    overlaps = {}
    union_start = None
    union_end = None
    for clip_id, (p0, p1) in PROTECTED.items():
        ov = max(0.0, min(rec6_end, p1) - max(rec6_start, p0))
        overlaps[clip_id] = {
            "protected_range": [p0, p1],
            "protected_duration": round(p1 - p0, 6),
            "overlap_seconds": round(ov, 6),
            "overlap_fraction_of_protected": round(ov / (p1 - p0), 6),
            "overlap_fraction_of_rec_0006": round(ov / (rec6_end - rec6_start), 6),
        }
        union_start = p0 if union_start is None else min(union_start, p0)
        union_end = p1 if union_end is None else max(union_end, p1)

    seg2, text2 = text_in_range(*PROTECTED["clip_0002"])
    seg4, text4 = text_in_range(*PROTECTED["clip_0004"])
    seg6, text6 = text_in_range(rec6_start, rec6_end)
    protected_union_text = text2 + " " + text4
    ratio = difflib.SequenceMatcher(
        None, " ".join(words(protected_union_text)), " ".join(words(text6))
    ).ratio()
    jaccard = len(set(words(protected_union_text)) & set(words(text6))) / max(
        1, len(set(words(protected_union_text)) | set(words(text6)))
    )

    covered = max(0.0, min(rec6_end, union_end) - max(rec6_start, union_start))
    evidence = {
        "candidate": "rec_0006",
        "rec_range_seconds": [rec6_start, rec6_end],
        "rec_duration_seconds": round(rec6_end - rec6_start, 6),
        "rec_title_en": rec6["title_en"],
        "protected_ranges": PROTECTED,
        "overlaps": overlaps,
        "protected_union_range": [union_start, union_end],
        "covered_by_protected_union_seconds": round(covered, 6),
        "covered_fraction_of_rec_0006": round(covered / (rec6_end - rec6_start), 6),
        "text_similarity": {
            "sequence_matcher_ratio": round(ratio, 6),
            "jaccard_words": round(jaccard, 6),
        },
        "segments": {
            "clip_0002_protected_range": [s["segment_id"] for s in seg2],
            "clip_0004_protected_range": [s["segment_id"] for s in seg4],
            "rec_0006": [s["segment_id"] for s in seg6],
            "rec_0006_transcript_refs": rec6["transcript_refs"],
        },
        "text": {
            "clip_0002_protected_range": text2,
            "clip_0004_protected_range": text4,
            "rec_0006_clip_text": rec6["clip_text"],
        },
        "conclusion": (
            "rec_0006 reutiliza el contenido fuente de los dos Shorts protegidos: "
            "casi todo su rango queda cubierto por la union de clip_0002 y clip_0004 "
            "y el texto coincide en su mayoria."
        ),
    }
    (DIAG / "candidate6_overlap.json").write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "rec": [rec6_start, rec6_end],
        "overlaps": overlaps,
        "covered_fraction": evidence["covered_fraction_of_rec_0006"],
        "similarity": evidence["text_similarity"],
        "segments_rec6": len(seg6),
        "segments_protected": len(seg2) + len(seg4),
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
