import json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
w = Path(r"C:\Users\SPARTAN PC\Documents\new_life\clipping\work\run_20261116_new_source_full_pipeline\delivery\a7ace6f1_clip_0001_es_20261117")
st = json.loads((w / "state.json").read_text(encoding="utf-8"))
a = st["render"]["artifacts"]
print("artifact keys:", list(a.keys()))
for k in ("final_work_path", "final_work_sha256", "video_duration_seconds", "final_work_bytes"):
    print(k, "=", a.get(k))
