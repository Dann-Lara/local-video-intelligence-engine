"""Renderiza un `edit_plan.json` con un renderer distinto al del plan.

Sirve para comparar los caminos de salida (Remotion vs libass vs sin subtitulos)
sobre el MISMO clip, sin tocar el job ni su `renders/`: escribe en una carpeta
aparte. El render de verdad se hace con `python main.py render <job_id>`; esto es
solo para depurar.

Uso:
    python tools/render_variant.py <edit_plan.json> ffmpeg_libass
    python tools/render_variant.py <edit_plan.json> none [carpeta_destino]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core import config as config_module  # noqa: E402
from src.media import render as render_module  # noqa: E402

plan_path = Path(sys.argv[1])
renderer = sys.argv[2]
config = config_module.load()

plan = json.loads(plan_path.read_text(encoding="utf-8"))
plan["subtitles"]["renderer"] = renderer

# El transcript vive junto a los edit_plans, dos niveles arriba.
work = plan_path.parent.parent
transcript_path = work / "transcript.json"
output_dir = (
    Path(sys.argv[3])
    if len(sys.argv) > 3
    else work / f"renders_{renderer}"
)

result = render_module.render_plan(
    plan,
    config,
    transcript=json.loads(transcript_path.read_text(encoding="utf-8"))
    if transcript_path.is_file()
    else None,
    output_dir=output_dir,
    remotion_dir=config_module.resolve_path(config, "remotion_dir"),
)

print(f"renderer  {result.renderer}")
print(f"salida    {result.output_path}")
print(f"aux       {', '.join(result.aux_paths) or '-'}")
print(f"cues      {result.subtitle_count} ({result.duration:g}s)")
