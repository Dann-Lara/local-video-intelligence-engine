import json, sys
sys.stdout.reconfigure(encoding="utf-8")
root = r"C:\Users\SPARTAN PC\Documents\new_life\clipping"
sys.path.insert(0, root)
from services.delivery.language import build_language_context, detect_language
tr = json.load(open(root + r"\projects\a7ace6f1-43b8-4769-9b67-58693575c499\transcription\transcription.json", encoding="utf-8"))
by = {s["segment_id"]: s for s in tr["segments"]}
base = ["seg_0925", "seg_0926", "seg_0927"]
extra = ["seg_0928", "seg_0929", "seg_0930", "seg_0931", "seg_0932", "seg_0933", "seg_0934"]
for n in range(0, len(extra) + 1):
    ids = base + extra[:n]
    segs = [by[i] for i in ids]
    text = " ".join(s["text"] for s in segs)
    ctx = build_language_context(segs, source_audio_language="es",
                                 source_audio_language_source="job")
    d = detect_language(text)
    print("hasta", ids[-1], "tokens", d["tokens"], "hits", d["hits"],
          "decision", ctx["overlay_language_decision"],
          "end", segs[-1]["end_seconds"])
