import json, sys
sys.stdout.reconfigure(encoding="utf-8")
root = r"C:\Users\SPARTAN PC\Documents\new_life\clipping"
tr = json.load(open(root + r"\projects\a7ace6f1-43b8-4769-9b67-58693575c499\transcription\transcription.json", encoding="utf-8"))
by = {s["segment_id"]: s for s in tr["segments"]}
b = json.load(open(root + r"\work\run_20261116_new_source_full_pipeline\stage_b.json", encoding="utf-8"))
want = {"clip_0001", "clip_0002", "clip_0003", "clip_0004", "clip_0008", "clip_0009"}
for c in b["candidates"]:
    if c["clip_id"] not in want:
        continue
    print("=" * 100)
    print(c["clip_id"], "%.2f-%.2f" % (c["start_seconds"], c["end_seconds"]))
    for sid in c["transcript_refs"]:
        s = by[sid]
        print("[%s %6.2f-%6.2f] %s" % (sid, s["start_seconds"], s["end_seconds"], s["text"].replace("\n", " / ")))
