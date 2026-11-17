import json, sys
sys.stdout.reconfigure(encoding="utf-8")
j = json.load(open("work/run_20261116_new_source_full_pipeline/stage_b.json", encoding="utf-8"))
for c in j["candidates"]:
    print("=" * 100)
    print(c["clip_id"], "%.2f-%.2f" % (c["start_seconds"], c["end_seconds"]),
          "%.2fs" % c["duration_seconds"], "win", c["window"], "conf", c["confidence"])
    print("TITULO:", c["title"])
    print("HOOK:", c["hook"][:240])
    print("IDEA:", c["main_idea"][:240])
    print("ENDING:", c["ending"][:240])
    print("EVIDENCIA:", c["evidence_text"][:300])
    refs = c["transcript_refs"]
    print("REFS:", refs[0], "->", refs[-1], "(n=%d)" % len(refs))
    be = c["boundary_evaluation"]
    print("BOUNDARY start_ok=%s end_ok=%s" % (be.get("start_ok"), be.get("end_ok")))
    print("  start:", str(be.get("start"))[:170])
    print("  end:", str(be.get("end"))[:170])
