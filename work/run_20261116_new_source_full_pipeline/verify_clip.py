import json, subprocess, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
root = Path(r"C:\Users\SPARTAN PC\Documents\new_life\clipping")
sys.path.insert(0, str(root))
from services.delivery import baselines, pipeline
stem = "a7ace6f1_clip_0001_es_20261117"
p = baselines.check_protected(root / "output")
print("protegidos:", json.dumps(p, ensure_ascii=False)[:400])
m = json.loads((root / "output" / f"{stem}.manifest.json").read_text(encoding="utf-8"))
print("manifest keys:", list(m.keys()))
s = json.dumps(m, ensure_ascii=False)
for k in ("frame_accurate", "pre_roll", "output_seek", "pending_user_review",
          "human_review", "cut_method", "fps_normalization"):
    print(k, ":", k in s)
print("review:", json.dumps(m.get("review"), ensure_ascii=False)[:500])
mp4 = root / "output" / f"{stem}.mp4"
print("ffprobe:", subprocess.run(["ffprobe", "-v", "error", "-show_entries",
      "format=duration:stream=codec_name,profile,width,height,r_frame_rate,pix_fmt,sample_rate,channels",
      "-of", "json", str(mp4)], capture_output=True, text=True).stdout[:1600])
