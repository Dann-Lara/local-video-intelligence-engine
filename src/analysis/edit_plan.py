"""Planes de edicion (blueprint seccion 27).

Convierte los candidatos SELECCIONADOS de `clip_candidates.json` en un
`edit_plan.json` por clip: la instruccion determinista de render (seccion 2.4:
mismos parametros + mismo plan = mismo fichero de salida). Es la etapa
PLANNING_EDITS.

Reglas:

1. Solo se planifica lo que RANKING selecciono (`selection.selected`), en su
   orden. Los descartados no llegan aqui.
2. El plan se rellena desde la config (`output.*`, `subtitles.*`, `audio.*`),
   nunca con valores hardcodeados (seccion 52).
3. El crop del MVP es `center` (seccion 30): no se asume que el sujeto esta al
   centro; `smart_vertical` llega en Fase 2.
4. El hook viaja del candidato al plan: tipo, origen, texto y el flag de
   `requires_generated_hook`/`integrity_note` (secciones 25/26) se conservan
   para que el render sepa que el texto en pantalla no fue pronunciado.
5. Un plan por clip, validado contra `edit_plan.schema.json` al escribirse.

Es una operacion pura: lee el conjunto, no toca la base de datos.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.core import artifacts
from src.core import config as config_module

SCHEMA_VERSION = "1.0"
DEFAULT_FORMAT = "9:16"


@dataclass
class EditPlansResult:
    job_id: str | None
    clip_count: int
    paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        lines = [f"planes      {self.clip_count} edit_plan.json por clip"]
        for index, path in enumerate(self.paths, start=1):
            lines.append(f"  {index}. {path}")
        return "\n".join(lines)


def _output_block(config: dict[str, Any]) -> dict[str, Any]:
    output = config_module.get(config, "output", {})
    return {
        "width": int(config_module.get(output, "width", 1080)),
        "height": int(config_module.get(output, "height", 1920)),
        "fps": int(config_module.get(output, "fps", 30)),
        "format": DEFAULT_FORMAT,
        "video_codec": str(config_module.get(output, "video_codec", "h264_nvenc")),
        "crf": config_module.get(output, "crf"),
        "preset": config_module.get(output, "preset"),
        "pixel_format": config_module.get(output, "pixel_format"),
        "audio_codec": str(config_module.get(output, "audio_codec", "aac")),
        "audio_bitrate": config_module.get(output, "audio_bitrate"),
        "container": str(config_module.get(output, "container", "mp4")),
    }


def _subtitles_block(config: dict[str, Any]) -> dict[str, Any]:
    subtitles = config_module.get(config, "subtitles", {})
    return {
        "enabled": bool(config_module.get(subtitles, "enabled", True)),
        "style": str(config_module.get(subtitles, "style", "default")),
        "language": None,
        "max_words_per_line": int(config_module.get(subtitles, "max_words_per_line", 4)),
        "max_lines": int(config_module.get(subtitles, "max_lines", 2)),
        "max_chars_per_line": config_module.get(subtitles, "max_chars_per_line"),
        "min_duration_ms": config_module.get(subtitles, "min_duration_ms"),
        "max_duration_ms": config_module.get(subtitles, "max_duration_ms"),
        "position": str(config_module.get(subtitles, "position", "lower_center")),
        "renderer": str(config_module.get(subtitles, "renderer", "remotion")),
    }


def _hook_block(candidate: dict[str, Any]) -> dict[str, Any]:
    hook = candidate.get("hook") or {}
    return {
        "type": str(hook.get("type") or "none"),
        "origin": str(hook.get("origin") or "generated"),
        "text": hook.get("text"),
        "requires_generated_hook": bool(hook.get("requires_generated_hook", False)),
        "displayed": False,
        "integrity_note": hook.get("integrity_note"),
    }


def _audio_block(config: dict[str, Any]) -> dict[str, Any]:
    audio = config_module.get(config, "audio", {})
    intensity = audio.get("music")
    music = None
    if isinstance(intensity, dict) and intensity.get("enabled"):
        music = {
            "enabled": True,
            "file": None,
            "volume_db": intensity.get("volume_db"),
            "duck_voice": bool(intensity.get("duck_voice", True)),
        }
    return {
        "normalize": bool(config_module.get(audio, "normalize", True)),
        "target_lufs": audio.get("target_lufs"),
        "true_peak_db": audio.get("true_peak_db"),
        "fade_in_seconds": float(config_module.get(audio, "fade_in_seconds", 0.0)),
        "fade_out_seconds": float(config_module.get(audio, "fade_out_seconds", 0.0)),
        "music": music,
    }


def _plan_from_candidate(
    candidate: dict[str, Any],
    config: dict[str, Any],
    *,
    job_id: str,
    candidate_id: str,
    input_video: str,
) -> dict[str, Any]:
    source = candidate.get("source") or {}
    start, end = float(source.get("start", 0.0)), float(source.get("end", 0.0))
    return {
        "schema_version": SCHEMA_VERSION,
        "clip_id": candidate_id,
        "job_id": job_id,
        "candidate_id": candidate_id,
        "source": {
            "file": str(input_video or source.get("file") or ""),
            "start": start,
            "end": end,
            "duration": round(max(end - start, 0.0), 3),
        },
        "output": _output_block(config),
        "crop": {"mode": "center", "background": "crop"},
        "subtitles": _subtitles_block(config),
        "hook": _hook_block(candidate),
        "audio": _audio_block(config),
        "effects": [],
    }


def build_edit_plans(
    candidate_set_path: Path | str,
    config: dict[str, Any],
    *,
    job_id: str | None = None,
    output_dir: Path | str | None = None,
    input_video: str | None = None,
    overwrite: bool = True,
) -> EditPlansResult:
    """Escribe un `edit_plan.json` por candidato seleccionado.

    Devuelve el resultado; el orquestador registra los artefactos en la base.
    Si hay cero seleccionados, no escribe nada (no hay plan sin decisión, y
    decidir "no publicar nada" es una decisión valida).
    """
    set_doc = artifacts.read_json(candidate_set_path, schema_name="candidate_set")
    job_id = job_id or str(set_doc.get("job_id") or "")
    input_video = input_video or str((set_doc.get("source") or {}).get("file") or "")

    selection = set_doc.get("selection") or {}
    selected_ids = list(selection.get("selected", []))
    by_id = {
        str(candidate.get("candidate_id")): candidate
        for candidate in set_doc.get("candidates", [])
        if isinstance(candidate, dict)
    }

    dir_path = Path(output_dir) if output_dir else Path(candidate_set_path).parent / "edit_plans"
    dir_path.mkdir(parents=True, exist_ok=True)

    paths: list[str] = []
    for candidate_id in selected_ids:
        candidate = by_id.get(candidate_id)
        if candidate is None:
            continue
        plan = _plan_from_candidate(
            candidate, config, job_id=job_id, candidate_id=candidate_id, input_video=input_video
        )
        target = dir_path / f"edit_plan_{candidate_id}.json"
        info = artifacts.write_json(target, plan, schema_name="edit_plan", overwrite=overwrite)
        paths.append(str(info.path))

    return EditPlansResult(job_id=job_id, clip_count=len(paths), paths=paths)