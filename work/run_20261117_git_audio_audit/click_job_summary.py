"""Resumen de configuracion del clic en los seis jobs (solo lectura)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(r"C:\Users\SPARTAN PC\Documents\new_life\clipping")
CLICK = ROOT / "work" / "mvp_short_clip5_20261028_200526" / "audio" / "click_hf_v1_stereo.wav"
paths, gains, delays, durs = set(), set(), set(), set()
enabled = total = 0
for job_path in sorted((ROOT / "work" / "run_20261116_new_source_full_pipeline" / "jobs").glob("*.json")):
    data = json.loads(job_path.read_text(encoding="utf-8"))
    paths.add(data["click_sound"]["path"])
    gains.add(data["click_sound"]["gain_db"])
    for note in data.get("notifications", []):
        total += 1
        click = note.get("click", {})
        enabled += click.get("enabled_in_mix", True)
        gains.add(click.get("gain_db"))
        delays.add(click.get("delay_s"))
        durs.add(click.get("duration_s"))
print("sha256:", hashlib.sha256(CLICK.read_bytes()).hexdigest().upper())
print("paths:", paths)
print("gains:", sorted(gains))
print("delays:", sorted(delays))
print("durations:", sorted(durs))
print("notifications:", total, "enabled:", enabled)
