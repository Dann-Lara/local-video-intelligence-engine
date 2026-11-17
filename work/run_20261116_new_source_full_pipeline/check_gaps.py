import json, sys
sys.stdout.reconfigure(encoding="utf-8")
root = r"C:\Users\SPARTAN PC\Documents\new_life\clipping"
tr = json.load(open(root + r"\projects\a7ace6f1-43b8-4769-9b67-58693575c499\transcription\transcription.json", encoding="utf-8"))
for a, b in [(55, 95), (215, 255), (1275, 1325)]:
    print("=" * 90, a, b)
    for s in tr["segments"]:
        if a <= s["start_seconds"] <= b:
            print("[%s %6.2f-%6.2f] %s" % (s["segment_id"], s["start_seconds"], s["end_seconds"], s["text"].replace("\n", " / ")))
