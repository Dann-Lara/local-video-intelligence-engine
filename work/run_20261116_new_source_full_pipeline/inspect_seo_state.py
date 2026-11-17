import json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
w = Path(r"C:\Users\SPARTAN PC\Documents\new_life\clipping\work\run_20261116_new_source_full_pipeline\delivery\a7ace6f1_clip_0001_es_20261117")
st = json.loads((w / "state.json").read_text(encoding="utf-8"))
for name in ("validate_mp4", "seo", "manifest"):
    v = st["stages"].get(name, {})
    print("=" * 90, name, v.get("status"))
    print(json.dumps(v, indent=2, ensure_ascii=False)[:5000])
print("deliverable:", json.dumps(st.get("deliverable", {}), indent=2, ensure_ascii=False))
