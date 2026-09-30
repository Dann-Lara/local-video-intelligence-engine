"""Render de clips (blueprint secciones 28-30, Sprint 6).

FFmpeg hace todo el trabajo de media (trim, crop vertical, audio, encode y mux;
seccion 29) y Remotion compone encima (subtitulos, overlays y textos; seccion
28). El renderer lo decide el edit_plan en `subtitles.renderer`:

- ``ffmpeg_libass``: un solo paso de FFmpeg quema los subtitulos con libass.
  Es el camino rapido para iterar (config lo documenta asi).
- ``remotion``: FFmpeg produce el "master" 9:16 (trim + crop + audio) y
  Remotion compone los subtitulos y el hook sobre el master.
- ``none`` (o subtitulos desactivados): FFmpeg sin capa de texto.

Reglas transversales:

- Mismo plan + misma fuente = mismo fichero (seccion 2.4): los argumentos de
  FFmpeg son deterministicos (sin timestamps de metadata, sin cadenas
  aleatorias) y todo valor sale del plan o de la config.
- El crop del MVP es `center` (seccion 30): escala para cubrir y recorta en el
  centro. Nunca se asume donde esta el sujeto.
- Los comandos se ejecutan con argv list y sin shell (seccion 29 + modulo
  `src/media/ffmpeg.py`): la ruta del proyecto lleva espacio y acento.
- Paso 6 de la seccion 50: si el fichero de salida ya existe, no se re-renderiza.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.core import artifacts
from src.core import config as config_module
from src.media import ffmpeg
from src.subtitles import ass as ass_module
from src.subtitles import cues as cues_module

# Una app de bajo perfil comparte el campo de vision con las apps nativas, que
# es lo que pide la seccion 31 ("lograr un consenso de campo de vision").
MP4_FASTSTART = "+faststart"
_DEFAULT_CRF = 20
_MASTER_CRF = 16

# Estados de bitrate/preset validos para libx264. "p4" es un preset de NVENC;
# al pedir libx264 no hay que pasarselo.
_LIBX264_PRESETS = {
    "ultrafast", "superfast", "veryfast", "faster", "fast",
    "medium", "slow", "slower", "veryslow",
}


class RenderError(RuntimeError):
    """Un clip no se pudo renderizar."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass
class RenderResult:
    clip_id: str
    plan_path: str
    output_path: str
    renderer: str
    duration: float
    subtitle_count: int
    reused: bool
    sha256: str | None
    aux_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        state = "reutilizado" if self.reused else "renderizado"
        line = (
            f"[{self.clip_id}] {self.renderer} -> {self.output_path} "
            f"({self.duration:g}s, {self.subtitle_count} subtitulos, {state})"
        )
        return line


@dataclass
class RenderPlansResult:
    job_id: str
    plans: list[RenderResult]

    def to_dict(self) -> dict[str, Any]:
        return {"job_id": self.job_id, "clips": [plan.to_dict() for plan in self.plans]}

    def summary(self) -> str:
        rendered = sum(1 for clip in self.plans if not clip.reused)
        reused = len(self.plans) - rendered
        lines = [f"clips       {len(self.plans)} ({rendered} renderizado(s), {reused} reutilizado(s))"]
        for result in self.plans:
            state = "reutilizado" if result.reused else "renderizado"
            lines.append(
                f"  {result.clip_id}  {result.renderer}  {result.duration:g}s  "
                f"{result.subtitle_count} subtitulos  {state}"
            )
        return "\n".join(lines)


# --- resolucion de codecs (seccion 29) -------------------------------------

_NVENC_CACHE: dict[str, bool] = {}


def _nvenc_available() -> bool:
    """NVENC esta compilado en el ffmpeg instalado? Se cachea (la salida de
    `-encoders` no cambia a mitad de ejecucion)."""
    if "h264" not in _NVENC_CACHE:
        available = False
        try:
            binary = ffmpeg.require("ffmpeg")
            info = ffmpeg.run([binary, "-hide_banner", "-encoders"], check=True)
        except (ffmpeg.BinaryNotFoundError, ffmpeg.CommandFailedError):
            info = None
        if info is not None:
            available = "h264_nvenc" in info.stdout
        _NVENC_CACHE["h264"] = available
    return _NVENC_CACHE["h264"]


def resolve_video_encoder(output: dict[str, Any]) -> str:
    """Codec de video del plan, con fallback a libx264 si falta NVENC."""
    requested = str(output.get("video_codec") or "h264_nvenc")
    if requested.endswith("_nvenc") and not _nvenc_available():
        return "libx264"
    return requested


def _preset_for(codec: str, requested: str | None) -> str:
    if codec == "libx264":
        return requested if requested in _LIBX264_PRESETS else "medium"
    return requested or "p4"


def _num(value: Any, default: float) -> str:
    """Numero de config a texto de filtro de ffmpeg (`-14.0` -> `-14`)."""
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return f"{default:g}"


# --- construccion de argumentos (puras, para testear) -----------------------


def _vf_filter(plan: dict[str, Any], *, subtitles_name: str | None) -> str:
    output = plan["output"]
    width = int(output["width"])
    height = int(output["height"])
    parts = [
        f"scale={width}:{height}:force_original_aspect_ratio=increase:force_divisible_by=2",
        f"crop={width}:{height}",
    ]
    if subtitles_name:
        parts.append(f"subtitles={subtitles_name}")
    return ",".join(parts)


def _af_filter(plan: dict[str, Any], duration: float) -> str | None:
    audio = plan.get("audio") or {}
    parts: list[str] = []
    if audio.get("normalize", True):
        lufs = _num(audio.get("target_lufs"), -14.0)
        peak = _num(audio.get("true_peak_db"), -1.5)
        parts.append(f"loudnorm=I={lufs}:TP={peak}:LRA=11")
    fade_in = float(audio.get("fade_in_seconds") or 0.0)
    fade_out = float(audio.get("fade_out_seconds") or 0.0)
    if fade_in > 0:
        parts.append(f"afade=t=in:st=0:d={fade_in:g}")
    if fade_out > 0 and fade_out < duration:
        parts.append(f"afade=t=out:st={duration - fade_out:g}:d={fade_out:g}")
    return ",".join(parts) if parts else None


def build_ffmpeg_clip_args(
    plan: dict[str, Any],
    *,
    output_path: Path | str,
    subtitles_name: str | None = None,
    crf_override: int | None = None,
) -> list[str]:
    """Argv completo de FFmpeg para el clip (camino libass/directo).

    Parametros que respetan determinismo (seccion 2.4): `-map_metadata -1`
    elimina el timestamp de creacion de las apps, `-sn` descarta subtitulos del
    contenedor original y nada en la linea sale de un reloj.
    """
    start = float(plan["source"]["start"])
    end = float(plan["source"]["end"])
    duration = round(max(end - start, 0.0), 3)
    if duration <= 0:
        raise RenderError("empty_window", f"ventana del clip sin duracion: {start} -> {end}")
    if not subtitles_name:
        subtitles_name = None

    output = plan["output"]
    fps = int(output.get("fps") or 30)
    codec = resolve_video_encoder(output)
    crf = crf_override if crf_override is not None else output.get("crf")
    if crf is None:
        crf = _DEFAULT_CRF
    bitrate = str(output.get("audio_bitrate") or "192k")

    args: list[str] = [
        ffmpeg.require("ffmpeg"),
        "-y",
        "-loglevel", "error",
        "-ss", f"{start:.3f}",
        "-i", str(plan["source"]["file"]),
        "-t", f"{duration:.3f}",
        "-map", "0:v:0",
        "-map", "0:a:0",
        "-map_metadata", "-1",
        "-map_chapters", "-1",
        "-sn",
        "-vf", _vf_filter(plan, subtitles_name=subtitles_name),
        "-r", str(fps),
        "-pix_fmt", "yuv420p",
        "-c:v", codec,
        "-preset", _preset_for(codec, output.get("preset")),
        "-crf", str(crf),
    ]
    af = _af_filter(plan, duration)
    if af:
        args += ["-af", af]
    args += [
        "-c:a", "aac",
        "-b:a", bitrate,
        "-ac", "2",
        "-ar", "48000",
        "-movflags", MP4_FASTSTART,
        str(output_path),
    ]
    return args


def build_ffmpeg_master_args(plan: dict[str, Any], *, output_path: Path | str) -> list[str]:
    """Argv del master vertical para Remotion: mismo trim/crop/audio, sin texto."""
    return build_ffmpeg_clip_args(plan, output_path=output_path, crf_override=_MASTER_CRF)


def compose_remotion_props(
    plan: dict[str, Any],
    *,
    video_src: Path | str,
    cues: list[cues_module.SubtitleCue],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Props JSON de la composicion `Clip` (remotion/src/Clip.tsx).

    `durationInSeconds` se calcula como frames/fps para que Remotion recorte a
    la duracion exacta del master y no se quede corto ni sobresalga.

    `videoSrc` va SOLO con el nombre del fichero, nunca con la ruta absoluta:
    `OffthreadVideo` se descarga por HTTP a traves del proxy del renderer, que
    rechaza `file://`. El master se sirve con `--public-dir` y
    `staticFile()` lo resuelve dentro del bundle.
    """
    output = plan["output"]
    fps = int(output.get("fps") or 30)
    duration = float(plan["source"].get("duration") or 0.0) or (
        float(plan["source"]["end"]) - float(plan["source"]["start"])
    )
    frames = max(1, round(duration * fps))
    src = Path(str(video_src)).name

    hook = plan.get("hook") or {}
    hook_type = hook.get("type")
    title = hook.get("text") if hook_type not in (None, "none") and hook.get("text") else None

    branding = config.get("branding") or {}
    accent = str(branding.get("accent_color") or "#FFD400")

    return {
        "videoSrc": src,
        "durationInSeconds": round(frames / fps, 3),
        "fps": fps,
        "title": title,
        "subtitles": [cue.to_dict() for cue in cues],
        "fit": "cover",
        "accentColor": accent,
    }


def build_remotion_args(
    node: str,
    entry: Path | str,
    *,
    component_id: str,
    props_path: Path | str,
    output_path: Path | str,
    crf: int,
    public_dir: Path | str | None = None,
    browser_executable: Path | str | None = None,
    color_space: str = "bt709",
    loglevel: str = "error",
) -> list[str]:
    """Argv de `remotion render` para la composicion `Clip`.

    `public_dir` es obligatorio en la practica: es lo que convierte el master
    en un asset servible por HTTP. `browser_executable` evita que Remotion
    intente descargar su Chrome Headless Shell (seccion 28: sin red, esto falla).

    `color_space` no es cosmetico: sin el, Remotion emite YUVJ (full range) y el
    resultado sale `yuvj420p`, que no es el `yuv420p` que pide el plan y que
    ademas se ve lavado en algunos reproductores.
    """
    args = [
        node,
        str(entry),
        "render",
        component_id,
        str(output_path),
        "--props", str(props_path),
        "--codec", "h264",
        "--crf", str(crf),
        "--pixel-format", "yuv420p",
        "--audio-codec", "aac",
        "--color-space", color_space,
    ]
    if public_dir:
        args += ["--public-dir", str(public_dir)]
    if browser_executable:
        args += ["--browser-executable", str(browser_executable)]
    args += ["--log", loglevel]
    return args


# Navegadores que Remotion puede reutilizar en vez de descargar el suyo. El
# Headless Shell no viene en un Chrome normal, asi que se comprueba la existencia
# del ejecutable en vez de fiarse del nombre de la carpeta.
_BROWSER_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def resolve_browser_executable(config: dict[str, Any] | None = None) -> Path | None:
    """Chrome/Edge del sistema, o None si Remotion deberia usar el suyo."""
    configured = config_module.get(config or {}, "render.browser_executable")
    if configured:
        candidate = config_module.resolve_relative(
            config, "render.browser_executable", str(configured)
        )
        return candidate if candidate.is_file() else None
    for candidate in _BROWSER_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            return path
    return None


def remotion_entry(remotion_dir: Path) -> Path:
    """Entrada JS del CLI de Remotion (evita depender de .cmd en Windows)."""
    return remotion_dir / "node_modules" / "@remotion" / "cli" / "remotion-cli.js"


# --- ejecucion --------------------------------------------------------------


def _run(argv: list[str], *, cwd: Path, timeout: float | None) -> None:
    try:
        ffmpeg.run(argv, timeout=timeout, cwd=cwd)
    except ffmpeg.BinaryNotFoundError as exc:
        raise RenderError("binary_missing", str(exc)) from exc
    except ffmpeg.CommandFailedError as exc:
        tag = str(cwd).endswith("remotion") or "remotion-cli" in str(argv)
        raise RenderError("remotion_failed" if tag else "ffmpeg_failed", str(exc)) from exc


def _cues_for_plan(plan: dict[str, Any], transcript: dict[str, Any] | None) -> list[cues_module.SubtitleCue]:
    if not plan.get("subtitles", {}).get("enabled"):
        return []
    subtitles = plan["subtitles"]
    if str(subtitles.get("renderer") or "remotion") == "none":
        return []
    if transcript is None:
        return []
    start = float(plan["source"]["start"])
    end = float(plan["source"]["end"])
    return cues_module.build_cues(
        transcript,
        start,
        end,
        max_words_per_line=int(subtitles.get("max_words_per_line") or 4),
        max_lines=int(subtitles.get("max_lines") or 2),
        max_chars_per_line=int(subtitles.get("max_chars_per_line") or 22),
        min_duration_ms=int(subtitles.get("min_duration_ms") or 800),
        max_duration_ms=int(subtitles.get("max_duration_ms") or 4000),
    )


def render_plan(
    plan: dict[str, Any],
    config: dict[str, Any],
    *,
    transcript: dict[str, Any] | None,
    output_dir: Path | str,
    remotion_dir: Path | str | None = None,
    timeout: float | None = None,
) -> RenderResult:
    """Renderiza un clip segun su edit_plan. Idempotente (seccion 50/51)."""
    clip_id = str(plan["clip_id"])
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    duration = round(float(plan["source"].get("duration") or 0.0), 3)
    container = str(plan["output"].get("container") or "mp4")
    output_path = output_dir / f"{clip_id}.{container}"

    if output_path.is_file():
        # Checkpoint (seccion 50): un fichero ya renderizado no se repite.
        sha = artifacts.sha256_of_file(output_path)
        return RenderResult(
            clip_id=clip_id,
            plan_path="",
            output_path=str(output_path),
            renderer=str(plan.get("subtitles", {}).get("renderer") or "none"),
            duration=duration,
            subtitle_count=0,
            reused=True,
            sha256=sha,
        )

    source = Path(plan["source"]["file"])
    if not source.is_file():
        raise RenderError("source_missing", f"no existe la fuente del clip: {source}")

    renderer = str(plan.get("subtitles", {}).get("renderer") or "none")
    cues = _cues_for_plan(plan, transcript)

    if renderer == "remotion":
        return _render_with_remotion(
            plan, config, cues=cues, output_dir=output_dir, output_path=output_path,
            duration=duration, clip_id=clip_id, timeout=timeout,
            remotion_dir=Path(remotion_dir) if remotion_dir else None,
        )

    # ffmpeg_libass / none
    subtitles_name = None
    aux_paths: list[str] = []
    if renderer == "ffmpeg_libass" and cues:
        subtitles = plan["subtitles"]
        ass_path = ass_module.write_ass(
            cues,
            output_dir / f"{clip_id}.ass",
            width=int(plan["output"]["width"]),
            height=int(plan["output"]["height"]),
            position=str(subtitles.get("position") or "lower_center"),
        )
        # Ruta relativa a proposito: el filter `subtitles=` se resuelve contra
        # el cwd del proceso, y como el ASS vive junto a la salida, un nombre
        # relativo evita el infierno de escapar `C:` y las comas en el filtro.
        subtitles_name = ass_path.name
        aux_paths.append(str(ass_path))

    argv = build_ffmpeg_clip_args(plan, output_path=output_path, subtitles_name=subtitles_name)
    _run(argv, cwd=output_dir, timeout=timeout)

    return RenderResult(
        clip_id=clip_id,
        plan_path="",
        output_path=str(output_path),
        renderer=renderer,
        duration=duration,
        subtitle_count=len(cues),
        reused=False,
        sha256=artifacts.sha256_of_file(output_path),
        aux_paths=aux_paths,
    )


def _render_with_remotion(
    plan: dict[str, Any],
    config: dict[str, Any],
    *,
    cues: list[cues_module.SubtitleCue],
    output_dir: Path,
    output_path: Path,
    duration: float,
    clip_id: str,
    timeout: float | None,
    remotion_dir: Path | None,
) -> RenderResult:
    if (plan["output"].get("container") or "mp4") != "mp4":
        raise RenderError(
            "unsupported_container",
            f"Remotion solo empaqueta mp4 en el MVP; container pedido: {plan['output'].get('container')}",
        )
    remotion_dir = remotion_dir or _resolve_remotion_dir(config)
    node = shutil.which("node")
    if node is None:
        raise RenderError("node_missing", "node no esta en PATH (seccion 28: Remotion lo necesita)")
    entry = remotion_entry(remotion_dir)
    if not entry.is_file():
        raise RenderError("remotion_missing", f"no existe el CLI de Remotion: {entry}")

    # 1. Master vertical: FFmpeg hace trim + crop + audio (seccion 29).
    master_path = output_dir / f"{clip_id}_master.mp4"
    _run(build_ffmpeg_master_args(plan, output_path=master_path), cwd=output_dir, timeout=timeout)

    # 2. Composicion: Remotion anade subtitulos y hook (seccion 28).
    props = compose_remotion_props(plan, video_src=master_path, cues=cues, config=config)
    props_path = output_dir / f"{clip_id}.props.json"
    props_path.write_text(json.dumps(props, ensure_ascii=False, indent=2), encoding="utf-8")

    output = plan["output"]
    crf = output.get("crf") or _DEFAULT_CRF
    argv = build_remotion_args(
        node,
        entry,
        component_id="Clip",
        props_path=props_path,
        output_path=output_path,
        crf=int(crf),
        # El master vive junto a la salida: con --public-dir se sirve sin copiar
        # un mp4 de 10+ MB por cada clip (seccion 28).
        public_dir=output_dir,
        browser_executable=resolve_browser_executable(config),
    )
    _run(argv, cwd=remotion_dir, timeout=timeout)

    return RenderResult(
        clip_id=clip_id,
        plan_path="",
        output_path=str(output_path),
        renderer="remotion",
        duration=duration,
        subtitle_count=len(cues),
        reused=False,
        sha256=artifacts.sha256_of_file(output_path),
        aux_paths=[str(master_path), str(props_path)],
    )


def _resolve_remotion_dir(config: dict[str, Any]) -> Path:
    return config_module.resolve_path(config, "remotion_dir")


def render_plans(
    plan_paths: list[Path | str],
    config: dict[str, Any],
    *,
    transcript: dict[str, Any] | None,
    output_dir: Path | str,
    job_id: str | None = None,
    remotion_dir: Path | str | None = None,
    timeout: float | None = None,
) -> RenderPlansResult:
    """Renderiza todos los plan_files indicados, en orden. Idempotente."""
    results: list[RenderResult] = []
    for plan_path in plan_paths:
        plan = artifacts.read_json(plan_path, schema_name="edit_plan")
        result = render_plan(
            plan,
            config,
            transcript=transcript,
            output_dir=output_dir,
            remotion_dir=remotion_dir,
            timeout=timeout,
        )
        result.plan_path = str(plan_path)
        results.append(result)
    return RenderPlansResult(job_id=job_id or "", plans=results)