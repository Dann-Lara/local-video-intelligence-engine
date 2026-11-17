import json, subprocess, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
root = Path(r"C:\Users\SPARTAN PC\Documents\new_life\clipping")
w = root / "work" / "run_20261116_new_source_full_pipeline" / "delivery" / "a7ace6f1_clip_0001_es_20261117"
st = json.loads((w / "state.json").read_text(encoding="utf-8"))
print(json.dumps(st["render"], indent=2, ensure_ascii=False)[:6000])
