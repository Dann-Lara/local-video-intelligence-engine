import json, subprocess, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
root = Path(r"C:\Users\SPARTAN PC\Documents\new_life\clipping")
w = root / "work" / "run_20261116_new_source_full_pipeline" / "delivery" / "a7ace6f1_clip_0001_es_20261117"
st = json.loads((w / "state.json").read_text(encoding="utf-8"))
print("stages:", {k: (v.get("status") if isinstance(v, dict) else v) for k, v in st.items() if k in ("render", "validate", "seo", "manifest", "deliverable")})
print("stages keys:", list(st.keys()))
print(json.dumps(st.get("stages", {}), indent=2, ensure_ascii=False)[:3000])
print(json.dumps(st.get("validation", {}), indent=2, ensure_ascii=False)[:3000])
for p in sorted(w.rglob("*.mp4")) + sorted(w.rglob("*.mov")):
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=r_frame_rate,avg_frame_rate,nb_frames,width,height,codec_name,duration",
           "-of", "json", str(p)]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    print(p.relative_to(w), out.strip().replace("\n", " "))
