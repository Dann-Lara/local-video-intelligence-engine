import json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
root = Path(r"C:\Users\SPARTAN PC\Documents\new_life\clipping")
sys.path.insert(0, str(root))
from services.delivery.pipeline import EXPECTED_CONTAINER, ffprobe_json, container_checks
w = root / "work" / "run_20261116_new_source_full_pipeline" / "delivery" / "a7ace6f1_clip_0001_es_20261117"
st = json.loads((w / "state.json").read_text(encoding="utf-8"))
print("EXPECTED:", json.dumps(EXPECTED_CONTAINER, indent=2, ensure_ascii=False))
probe = st["validate_mp4"]["probe"]
print("STATE video stream:", json.dumps(probe["streams"][0], indent=2, ensure_ascii=False)[:1500])
p = w / "render" / "a7ace6f1_clip_0001_es_20261117.mp4"
fresh = ffprobe_json(p)
print("FRESH r_frame_rate:", repr(fresh["streams"][0].get("r_frame_rate")))
print("STATE r_frame_rate:", repr(probe["streams"][0].get("r_frame_rate")))
print("STATE checks:", json.dumps(st["validate_mp4"]["checks"], indent=2, ensure_ascii=False))
