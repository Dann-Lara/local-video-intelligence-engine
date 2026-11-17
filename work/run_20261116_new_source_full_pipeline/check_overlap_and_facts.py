import json, sys, re
sys.stdout.reconfigure(encoding="utf-8")
root = r"C:\Users\SPARTAN PC\Documents\new_life\clipping"
tr = json.load(open(root + r"\projects\a7ace6f1-43b8-4769-9b67-58693575c499\transcription\transcription.json", encoding="utf-8"))
segs = tr["segments"]
b = json.load(open(root + r"\work\run_20261116_new_source_full_pipeline\stage_b.json", encoding="utf-8"))
prot = {"clip_0001": (526.791111, 584.949211), "clip_0002": (1099.488073, 1131.353240),
        "clip_0003": (780.770975, 829.352842), "clip_0004": (1131.485170, 1176.206168),
        "clip_0005": (1178.691338, 1228.382041)}
for c in b["candidates"]:
    s, e = c["start_seconds"], c["end_seconds"]
    ov = {k: round(max(0.0, min(e, p1) - max(s, p0)), 3) for k, (p0, p1) in prot.items()}
    nz = {k: v for k, v in ov.items() if v > 0}
    ids = set(c["transcript_refs"])
    text = " ".join(x["text"] for x in segs if x["segment_id"] in ids)
    nums = sorted(set(re.findall(r"\d+(?:[.,]\d+)?", text)))
    print(c["clip_id"], "%.2f-%.2f" % (s, e), "SOLAPE:", nz or "ninguno")
    print("   nums en refs:", nums[:20])
    print("   texto:", text[:260].replace("\n", " "))
