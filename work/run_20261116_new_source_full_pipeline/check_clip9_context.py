import json, sys
sys.stdout.reconfigure(encoding="utf-8")
root = r"C:\Users\SPARTAN PC\Documents\new_life\clipping"
tr = json.load(open(root + r"\projects\a7ace6f1-43b8-4769-9b67-58693575c499\transcription\transcription.json", encoding="utf-8"))
for s in tr["segments"]:
    if 1460 <= s["start_seconds"] <= 1535:
        print("[%s %7.2f-%7.2f] %s" % (s["segment_id"], s["start_seconds"], s["end_seconds"], s["text"].replace("\n", " / ")))
