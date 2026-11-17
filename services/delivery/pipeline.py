"""Orquestador reanudable de la entrega de un Short (render -> SEO -> manifiesto).

Etapas (cada una guarda estado en `work/<job_id>/state.json`):

    render       corte real + vertical + assets + composicion + mezcla
    validate_mp4 FFprobe, integridad de decodificacion, sincronia y publicacion
    seo          genera `seo.md` en `work/` a partir de la fuente real
    validate_seo valida el SEO contra la transcripcion y el MP4; publica si pasa
    manifest     escribe el manifiesto con hashes y evidencia

Reglas de seguridad:

- No se sobrescribe ningun archivo: las publicaciones son exclusivas.
- Un fallo en `seo`/`validate_seo` deja el MP4 valido en `output/` y permite
  reanudar solo esas etapas sin volver a renderizar.
- Los 5 entregables previos se verifican por hash antes y despues.
- Nada se publica: el estado final es `pending_user_review`.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np
from scipy.io import wavfile

from services.delivery import baselines
from services.delivery.language import (
    OverlayLanguageError,
    SeoLanguageError,
    build_language_context,
    detect_language,
    require_emphasis_language,
    require_overlay_language,
    require_seo_language,
)
from services.delivery.manifest import build_manifest
from services.delivery.seo import build_seo, validate_seo
from services.rendering.card_text import MissingCardTranslation, resolve_card_text
from services.rendering.cards import build_card_for_action
from services.rendering.emphasis import build_emphasis_ass, validate_emphasis
from services.rendering.neon import build_neon_ass
from services.rendering.overlays import (
    compose_overlay,
    mix_with_clicks,
    render_overlay_mov,
    video_duration,
)
from services.rendering.renderer import probe, render_clip, render_vertical
from services.rendering.subtitles import build_cues, write_ass

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = ROOT / "output"
DEFAULT_WORK_DIR = ROOT / "work"

FPS = 30000 / 1001
SR = 44100

STAGES = ("render", "validate_mp4", "seo", "validate_seo", "manifest")

RENDER_SAFE_AD_CLASSIFICATIONS = {"none", "incidental_mention"}

EXPECTED_CONTAINER = {
    "video_codec": "h264",
    "video_profile": "High",
    "pix_fmt": "yuv420p",
    "width": 1080,
    "height": 1920,
    "frame_rate": "30000/1001",
    "audio_codec": "aac",
    "audio_profile": "LC",
    "sample_rate": "44100",
    "channels": 2,
}


class DeliveryError(RuntimeError):
    """Error de entrega con contexto estructurado."""

    def __init__(self, message: str, *, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.details = details or {}


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json(path: Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _run(cmd: Sequence[str], *, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(cmd),
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def ffprobe_json(path: Path) -> Dict[str, Any]:
    proc = _run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    if proc.returncode != 0:
        raise DeliveryError(
            f"FFprobe fallo sobre {path}: {proc.stderr[-1500:]}",
            details={"path": str(path)},
        )
    return json.loads(proc.stdout)


def _stream(data: Dict[str, Any], codec_type: str) -> Dict[str, Any]:
    for stream in data.get("streams", []):
        if stream.get("codec_type") == codec_type:
            return stream
    return {}


def _publish_exclusive(source: Path, destination: Path) -> Path:
    """Copia `source` a `destination` solo si el destino no existe."""
    source = Path(source)
    destination = Path(destination)
    if destination.exists():
        raise DeliveryError(
            f"El destino ya existe y no se sobrescribe: {destination}",
            details={"destination": str(destination)},
        )
    if not source.exists() or source.stat().st_size == 0:
        raise DeliveryError(
            f"Artefacto de origen inexistente o vacio: {source}",
            details={"source": str(source)},
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if sha256_file(source) != sha256_file(destination):
        raise DeliveryError(
            f"La copia publicada no coincide byte a byte: {destination}",
            details={"source": str(source), "destination": str(destination)},
        )
    return destination


# ---------------------------------------------------------------------------
# Validacion del candidato (antes de gastar tiempo de render)
# ---------------------------------------------------------------------------


def validate_candidate(
    job: Dict[str, Any],
    *,
    source_duration: float,
    output_dir: Path,
) -> List[str]:
    """Devuelve la lista de bloqueos editoriales/tecnicos del candidato."""
    errors: List[str] = []
    candidate = job.get("candidate", {})
    source = job.get("source", {})

    source_path = Path(str(source.get("path", "")))
    if not source_path.exists():
        errors.append(f"La fuente no existe: {source_path}")
    elif source_path.stat().st_size == 0:
        errors.append(f"La fuente esta vacia: {source_path}")
    elif source.get("sha256") and sha256_file(source_path) != str(
        source["sha256"]
    ).upper():
        errors.append("El SHA-256 de la fuente no coincide con el declarado.")

    if not source_path.exists():
        return errors

    if candidate.get("ad_classification") not in RENDER_SAFE_AD_CLASSIFICATIONS:
        errors.append(
            "El candidato no es apto para render: ad_classification="
            f"{candidate.get('ad_classification')!r}."
        )
    if candidate.get("editorial_decision") != "accept":
        errors.append(
            "El candidato no esta aceptado editorialmente: "
            f"editorial_decision={candidate.get('editorial_decision')!r}."
        )
    boundary = candidate.get("boundary_evaluation") or {}
    if not (boundary.get("start_ok") and boundary.get("end_ok")):
        errors.append("Los limites de inicio/fin no estan validados.")

    try:
        start = float(candidate["start_seconds"])
        end = float(candidate["end_seconds"])
    except (KeyError, TypeError, ValueError):
        return errors + ["El candidato no tiene start_seconds/end_seconds validos."]

    if not (0.0 <= start < end <= source_duration + 0.01):
        errors.append(
            f"Rango invalido o fuera de la fuente: {start} - {end} "
            f"(duracion {source_duration:.3f})."
        )
    duration = end - start
    if duration < 10.0 or duration > 90.0:
        errors.append(f"Duracion fuera del rango editorial (10-90 s): {duration:.3f}.")

    if errors:
        return errors

    overlaps = baselines.overlap_seconds(start, end)
    nonzero = {k: v for k, v in overlaps.items() if v > 0.0}
    if nonzero:
        errors.append(
            "El rango se solapa con Shorts ya entregados: "
            + ", ".join(f"{k}={v:.3f}s" for k, v in nonzero.items())
        )

    refs = candidate.get("evidence_refs") or []
    if not refs:
        errors.append("El candidato no declara segmentos de evidencia.")
    if not str(candidate.get("evidence_text", "")).strip():
        errors.append("El candidato no declara texto de evidencia.")

    stem = str(job["output_stem"])
    for name in (f"{stem}.mp4", f"{stem}.seo.md", f"{stem}.manifest.json"):
        if (cap := Path(output_dir) / name).exists():
            errors.append(f"Ya existe un entregable con ese nombre: {cap}")
    return errors


# ---------------------------------------------------------------------------
# Alineacion real por NCC (misma tecnica que el MVP aprobado)
# ---------------------------------------------------------------------------


def _extract_mono(
    src: Path, out: Path, *, ss: Optional[float] = None, t: Optional[float] = None
) -> np.ndarray:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-hide_banner", "-y"]
    if ss is not None:
        cmd += ["-ss", f"{float(ss):.6f}"]
    cmd += ["-i", str(src)]
    if t is not None:
        cmd += ["-t", f"{float(t):.6f}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", str(out)]
    proc = _run(cmd)
    if proc.returncode != 0:
        raise DeliveryError(
            f"FFmpeg fallo extrayendo audio de {src}: {proc.stderr[-1500:]}",
            details={"source": str(src)},
        )
    rate, data = wavfile.read(out)
    if rate != SR:
        raise DeliveryError(f"Frecuencia inesperada en {out}: {rate}")
    return data.astype(np.float64) / 32768.0


def _ncc(a: np.ndarray, b: np.ndarray) -> tuple[float, int]:
    n, m = len(a), len(b)
    if m < n:
        raise DeliveryError("La ventana de referencia es mas corta que el clip.")
    size = 1 << int(math.ceil(math.log2(m + n)))
    fa = np.fft.rfft(a, size)
    fb = np.fft.rfft(b, size)
    corr = np.fft.irfft(fb * np.conj(fa), size)[: m - n + 1]
    csum = np.concatenate([[0.0], np.cumsum(b * b)])
    energy = csum[n:] - csum[: m - n + 1]
    denom = np.sqrt(np.maximum(energy, 1e-12) * float(np.sum(a * a)))
    score = corr / denom
    lag = int(np.argmax(score))
    return float(score[lag]), lag


def measure_alignment(
    clip_path: Path,
    source_path: Path,
    *,
    expected_start: float,
    work_audio_dir: Path,
    margin: float = 12.0,
) -> Dict[str, Any]:
    """Mide a que segundo de la fuente corresponde el instante 0 del clip."""
    clip = _extract_mono(clip_path, work_audio_dir / "align_clip_mono.wav")
    clip_duration = len(clip) / SR
    ref_start = max(0.0, expected_start - margin)
    ref_duration = clip_duration + 2 * margin
    ref = _extract_mono(
        source_path,
        work_audio_dir / "align_source_mono.wav",
        ss=ref_start,
        t=ref_duration,
    )
    score, lag = _ncc(clip, ref)
    start = ref_start + lag / SR

    chunks: List[Dict[str, Any]] = []
    win = int(5.0 * SR)
    for k in range(int(math.ceil(len(clip) / win))):
        s = k * win
        e = min(s + win, len(clip))
        if e - s < int(1.5 * SR):
            continue
        expected = lag + s
        lo = max(0, expected - int(0.2 * SR))
        hi = min(len(ref) - (e - s), expected + int(0.2 * SR))
        if hi <= lo:
            continue
        seg = clip[s:e]
        sc, lg = _ncc(seg, ref[lo: hi + (e - s)])
        chunks.append({
            "local_start": round(s / SR, 4),
            "local_end": round(e / SR, 4),
            "ncc": round(sc, 6),
            "delta_seconds_vs_global": round((lg + lo - expected) / SR, 6),
            "start_seconds": round(ref_start + (lg + lo - s) / SR, 6),
        })

    return {
        "method": "NCC audio mono 44.1 kHz contra la fuente",
        "clip": str(Path(clip_path).resolve().relative_to(ROOT).as_posix()),
        "source": str(Path(source_path).resolve().relative_to(ROOT).as_posix()),
        "ref_window_seconds": [round(ref_start, 6), round(ref_start + ref_duration, 6)],
        "global_ncc": round(score, 8),
        "clip_zero_maps_to_source_seconds": round(start, 6),
        "max_chunk_drift_seconds": max(
            (abs(c["delta_seconds_vs_global"]) for c in chunks), default=0.0
        ),
        "chunks": chunks,
    }


# ---------------------------------------------------------------------------
# Etapa 1: render (sin publicar)
# ---------------------------------------------------------------------------


def _compose_layers(
    base_video: Path, assets_dir: Path, out_video: Path
) -> Dict[str, Any]:
    out_video = Path(out_video)
    out_video.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-hide_banner", "-y", "-i", str(base_video),
        "-vf", "subtitles=subs.ass,ass=motion.ass,ass=neon.ass",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-c:a", "copy", str(out_video),
    ]
    proc = _run(cmd, cwd=assets_dir)
    if proc.returncode != 0:
        raise DeliveryError(
            f"FFmpeg fallo componiendo capas: {proc.stderr[-2000:]}",
            details={"command": cmd, "cwd": str(assets_dir)},
        )
    return {"path": str(out_video), "bytes": out_video.stat().st_size}


def _assert_overlay_text_language(
    field_name: str, text: Optional[str], expected: str
) -> None:
    """Impide que un overlay use un idioma distinto al canonico del audio.

    Los textos demasiado cortos o ambiguos se aceptan: el detector no puede
    decidir con evidencia suficiente y el texto ya fue revisado en el job.
    """
    value = str(text or "").strip()
    if not value:
        return
    detection = detect_language(value)
    if (
        detection.get("decision") == "auto"
        and detection.get("language")
        and detection["language"] != expected
    ):
        raise OverlayLanguageError(
            f"{field_name}: el texto esta en {detection['language']} y el audio "
            f"original esta en {expected}; se requiere revision humana.",
            details={
                "field": field_name,
                "expected_language": expected,
                "detected_language": detection["language"],
                "needs_review": True,
            },
        )


def stage_render(ctx: Dict[str, Any]) -> Dict[str, Any]:
    job = ctx["job"]
    work = ctx["work"]
    output_dir = ctx["output_dir"]
    state = ctx["state"]
    candidate = job["candidate"]
    source_path = Path(job["source"]["path"])

    source_probe = ffprobe_json(source_path)
    source_duration = float(source_probe["format"]["duration"])
    errors = validate_candidate(
        job, source_duration=source_duration, output_dir=output_dir
    )
    if errors:
        raise DeliveryError("Candidato no apto para render.", details={"errors": errors})

    protected_before = baselines.check_protected(output_dir)
    if not protected_before["all_ok"]:
        raise DeliveryError(
            "Los entregables protegidos no coinciden con los hashes fijados.",
            details={"protected": protected_before},
        )
    state["protected_before"] = protected_before

    stem = str(job["output_stem"])
    clips_dir = work / "clips"
    assets_dir = work / "assets"
    render_dir = work / "render"
    for directory in (clips_dir, assets_dir, render_dir, work / "audio", work / "cards"):
        directory.mkdir(parents=True, exist_ok=True)

    start = float(candidate["start_seconds"])
    end = float(candidate["end_seconds"])

    cut = render_clip(
        source_path=source_path,
        start_seconds=start,
        end_seconds=end,
        output_path=clips_dir / f"{stem}_source.mp4",
    )
    vertical = render_vertical(
        source_path=Path(cut["output_path"]),
        output_path=clips_dir / f"{stem}_vertical.mp4",
    )
    vertical_path = Path(vertical["output_path"])
    duration = video_duration(vertical_path)

    alignment = measure_alignment(
        vertical_path,
        source_path,
        expected_start=start,
        work_audio_dir=work / "audio",
    )
    actual_start = float(alignment["clip_zero_maps_to_source_seconds"])

    transcription = _read_json(Path(job["transcription_path"]))
    all_segments = transcription["segments"]
    segments_by_id = {s["segment_id"]: s for s in all_segments}
    refs = list(candidate["evidence_refs"])
    missing = [ref for ref in refs if ref not in segments_by_id]
    if missing:
        raise DeliveryError(
            f"Segmentos de evidencia inexistentes: {missing}",
            details={"refs": refs},
        )
    evidence_segments = [segments_by_id[ref] for ref in refs]

    cues = build_cues(
        evidence_segments,
        absolute_start_seconds=actual_start,
        clip_duration_seconds=duration,
    )
    if not cues:
        raise DeliveryError("No se genero ningun subtitulo para el fragmento.")
    subs_path = write_ass(assets_dir / "subs.ass", cues, width=1080, height=1920)

    emphasis_events = list(job.get("emphasis") or [])

    # Idioma canonico del audio: decide tarjetas, énfasis y SEO. Los subtitulos
    # se detectan aparte y no pueden cambiar los overlays.
    cues_text = " ".join(
        str(getattr(cue, "text", "")).replace("\\N", " ") for cue in cues
    ).strip()
    try:
        language_context = build_job_language_context(
            job, transcription, subtitle_text=cues_text or None
        )
        overlay_language = require_overlay_language(language_context)
        if emphasis_events:
            emphasis_language = require_emphasis_language(language_context)
            if emphasis_language != overlay_language:
                raise OverlayLanguageError(
                    "El idioma del enfasis no coincide con el de las tarjetas: "
                    f"{emphasis_language!r} != {overlay_language!r}.",
                    details={"context": language_context, "needs_review": True},
                )
    except OverlayLanguageError as exc:
        raise DeliveryError(
            str(exc),
            details={
                **exc.details,
                "stage": "render",
                "needs_review": True,
            },
        ) from exc
    state["language"] = language_context

    emphasis_errors = validate_emphasis(
        emphasis_events,
        segments_by_id,
        duration=duration,
        cards=list(job.get("notifications") or []),
    )
    if emphasis_errors:
        raise DeliveryError(
            "Rotulos de enfasis invalidos.", details={"errors": emphasis_errors}
        )
    (assets_dir / "motion.ass").write_text(
        build_emphasis_ass(emphasis_events), encoding="utf-8"
    )

    neon_ass = build_neon_ass(duration)
    (assets_dir / "neon.ass").write_text(neon_ass, encoding="utf-8")

    cards: List[Any] = []
    notifications: List[Dict[str, Any]] = []
    cards_meta: List[Dict[str, Any]] = []
    for index, note in enumerate(job.get("notifications") or [], start=1):
        action = note["action"]
        png = work / "cards" / f"card_{index:02d}_{action}.png"
        try:
            # Un titulo/subtitulo aportado por el job solo se acepta si su
            # idioma coincide con el canonico; nunca se usa el texto espanol
            # historico de `card_family.json` como fallback.
            for field_name in ("title_text", "subtitle_text"):
                _assert_overlay_text_language(
                    f"notificacion {index} ({field_name})",
                    note.get(field_name),
                    overlay_language,
                )
            localized = resolve_card_text(action, overlay_language)
            image, report = build_card_for_action(
                action,
                png,
                title_text=note.get("title_text"),
                subtitle_text=note.get("subtitle_text"),
                language=overlay_language,
            )
        except (MissingCardTranslation, OverlayLanguageError) as exc:
            raise DeliveryError(
                str(exc),
                details={
                    **getattr(exc, "details", {}),
                    "stage": "render",
                    "needs_review": True,
                    "notification_index": index,
                },
            ) from exc
        cards.append(image)
        notification = dict(note)
        notification.setdefault("click", {})
        notification["click"].setdefault("enabled_in_mix", True)
        notification["click"].setdefault("gain_db", -2.0)
        notification["click"].setdefault("duration_s", 0.045)
        notifications.append(notification)
        cards_meta.append({
            "action": action,
            "language": overlay_language,
            "title_text": report.get("title_text", localized["title"]),
            "png": str(png.relative_to(work).as_posix()),
            "size": list(image.size),
            "fits": report.get("fits"),
            "subtitle_text": report.get("subtitle_text"),
            "subtitle_source": report.get("localization", {}).get(
                "subtitle_source"
            ),
            "localization": report.get("localization", {}),
            "enter_start_seconds": float(note["enter_start_seconds"]),
            "exit_end_seconds": float(note["exit_end_seconds"]),
        })

    overlay_meta: Optional[Dict[str, Any]] = None
    overlaid_path: Path
    composed_path = work / "input_composed.mp4"
    if cards:
        overlay_mov = render_dir / "overlay_timing.mov"
        overlay_meta = render_overlay_mov(
            cards, notifications, duration=duration, out_mov=overlay_mov
        )
        composed = _compose_layers(vertical_path, assets_dir, composed_path)
        compose_overlay(composed_path, overlay_mov, render_dir / "overlaid.mp4")
        overlaid_path = render_dir / "overlaid.mp4"
    else:
        composed = _compose_layers(vertical_path, assets_dir, composed_path)
        overlaid_path = composed_path

    final_path = render_dir / f"{stem}.mp4"
    click_wav = Path(job["click_sound"]["path"])
    if not click_wav.exists():
        raise DeliveryError(f"Falta el sonido de clic aprobado: {click_wav}")
    mix_meta = mix_with_clicks(
        overlaid_path,
        composed_path,
        notifications,
        click_wav,
        final_path,
    )
    if not final_path.exists() or final_path.stat().st_size == 0:
        raise DeliveryError("La mezcla final no genero archivo.")

    artifacts = {
        "source_cut": {**cut, "sha256": sha256_file(Path(cut["output_path"]))},
        "vertical": {
            **vertical,
            "sha256": sha256_file(vertical_path),
            "duration_seconds": duration,
        },
        "subtitles": {
            "path": str(subs_path),
            "cues": len(cues),
            "segments": len(evidence_segments),
            "style": {
                "font": "Arial Black 96",
                "max_lines": 2,
                "margin_bottom": 340,
                "margin_lr": 70,
            },
        },
        "emphasis": {
            "path": str(assets_dir / "motion.ass"),
            "events": emphasis_events,
            "count": len(emphasis_events),
        },
        "neon": {
            "path": str(assets_dir / "neon.ass"),
            "seed": 20261020,
            "bytes": len(neon_ass),
        },
        "notifications": notifications,
        "cards_anim": cards_meta,
        "overlay_mov": overlay_meta,
        "click": {
            "path": str(click_wav),
            "sha256": sha256_file(click_wav),
            "gain_db": -2.0,
            "delay_s": 0.012,
            "duration_s": 0.045,
            "license_note": "Sintetizado localmente; reutilizado del MVP clip_0005.",
        },
        "audio_mix": mix_meta,
        "final_work_path": str(final_path),
        "final_work_sha256": sha256_file(final_path),
        "final_work_bytes": final_path.stat().st_size,
        "video_duration_seconds": duration,
        "composed": composed,
    }

    state["timing_alignment"] = alignment
    state["requested_cut_range_seconds"] = [start, end]
    state["overlap_seconds"] = baselines.overlap_seconds(
        actual_start, actual_start + duration
    )
    state["render"] = {"finished_at": _now(), "artifacts": artifacts}
    return {
        "final_work": str(final_path),
        "sha256": artifacts["final_work_sha256"],
        "bytes": artifacts["final_work_bytes"],
        "actual_start_seconds": actual_start,
        "duration_seconds": duration,
    }


# ---------------------------------------------------------------------------
# Etapa 2: validacion del MP4 y publicacion exclusiva
# ---------------------------------------------------------------------------


def container_checks(
    probe_data: Dict[str, Any],
    *,
    expected_duration: float,
    actual_start: float,
    requested_start: float,
    requested_end: float,
    alignment_ncc: float,
    overlaps: Dict[str, float],
) -> Dict[str, bool]:
    video = _stream(probe_data, "video")
    audio = _stream(probe_data, "audio")
    fmt = probe_data.get("format", {})
    fmt_duration = float(fmt.get("duration", 0.0) or 0.0)
    v_duration = float(video.get("duration", 0.0) or 0.0)
    a_duration = float(audio.get("duration", 0.0) or 0.0)

    checks = {
        "video_present": bool(video),
        "audio_present": bool(audio),
        "video_codec": video.get("codec_name") == EXPECTED_CONTAINER["video_codec"],
        "video_profile": video.get("profile") == EXPECTED_CONTAINER["video_profile"],
        "pix_fmt": video.get("pix_fmt") == EXPECTED_CONTAINER["pix_fmt"],
        "width": int(video.get("width", 0)) == EXPECTED_CONTAINER["width"],
        "height": int(video.get("height", 0)) == EXPECTED_CONTAINER["height"],
        "frame_rate": video.get("r_frame_rate") == EXPECTED_CONTAINER["frame_rate"],
        "audio_codec": audio.get("codec_name") == EXPECTED_CONTAINER["audio_codec"],
        "audio_profile": audio.get("profile") == EXPECTED_CONTAINER["audio_profile"],
        "sample_rate": str(audio.get("sample_rate")) == EXPECTED_CONTAINER["sample_rate"],
        "channels": int(audio.get("channels", 0)) == EXPECTED_CONTAINER["channels"],
        "duration_matches_expected": abs(fmt_duration - expected_duration) <= 0.35,
        "av_duration_delta": abs(v_duration - a_duration) <= 0.20,
        "start_matches_cut": abs(actual_start - requested_start) <= 0.20,
        "end_matches_request": abs(
            (actual_start + v_duration) - requested_end
        ) <= 0.25,
        "alignment_confident": alignment_ncc >= 0.90,
        "no_protected_overlap": all(v == 0.0 for v in overlaps.values()),
        "has_frames": int(video.get("nb_frames", 0) or 0) > 0,
    }
    return checks


def decode_integrity(path: Path) -> Dict[str, Any]:
    proc = _run([
        "ffmpeg", "-hide_banner", "-v", "error", "-i", str(path),
        "-map", "0", "-f", "null", "-",
    ])
    lines = [ln for ln in (proc.stderr or "").splitlines() if ln.strip()]
    return {
        "returncode": proc.returncode,
        "error_lines": lines[-10:],
        "ok": proc.returncode == 0 and not lines,
    }


def stage_validate_mp4(ctx: Dict[str, Any]) -> Dict[str, Any]:
    job = ctx["job"]
    state = ctx["state"]
    work = ctx["work"]
    output_dir = ctx["output_dir"]
    render = state.get("render")
    if not render:
        raise DeliveryError("Falta la etapa render en el estado.")
    final_path = Path(render["artifacts"]["final_work_path"])
    if not final_path.exists():
        raise DeliveryError(f"No existe el render de trabajo: {final_path}")

    candidate = job["candidate"]
    alignment = state["timing_alignment"]
    actual_start = float(alignment["clip_zero_maps_to_source_seconds"])
    duration = float(render["artifacts"]["video_duration_seconds"])

    probe_data = ffprobe_json(final_path)
    overlaps = baselines.overlap_seconds(actual_start, actual_start + duration)
    checks = container_checks(
        probe_data,
        expected_duration=duration,
        actual_start=actual_start,
        requested_start=float(candidate["start_seconds"]),
        requested_end=float(candidate["end_seconds"]),
        alignment_ncc=float(alignment["global_ncc"]),
        overlaps=overlaps,
    )
    final_alignment = measure_alignment(
        final_path,
        Path(job["source"]["path"]),
        expected_start=actual_start,
        work_audio_dir=work / "audio",
    )
    checks["final_sync_matches_render"] = abs(
        float(final_alignment["clip_zero_maps_to_source_seconds"]) - actual_start
    ) <= 0.05
    checks["final_sync_confident"] = float(final_alignment["global_ncc"]) >= 0.90

    integrity = decode_integrity(final_path)
    checks["decode_integrity"] = integrity["ok"]
    all_ok = all(checks.values())

    stem = str(job["output_stem"])
    published_path = Path(output_dir) / f"{stem}.mp4"
    if all_ok:
        _publish_exclusive(final_path, published_path)

    result = {
        "finished_at": _now(),
        "source": "render de trabajo",
        "work_path": str(final_path),
        "work_sha256": render["artifacts"]["final_work_sha256"],
        "probe": probe_data,
        "checks": checks,
        "all_ok": all_ok,
        "overlap_seconds": overlaps,
        "decode_integrity": integrity,
        "final_alignment": final_alignment,
        "expected_duration_seconds": duration,
        "published": all_ok,
    }
    state["validate_mp4"] = result
    if not all_ok:
        failed = [name for name, ok in checks.items() if not ok]
        raise DeliveryError(
            "El MP4 no cumple el contrato de entrega.", details={"failed": failed}
        )

    fmt_duration = float(probe_data["format"]["duration"])
    state["deliverable"] = {
        "path": str(published_path),
        "file_name": published_path.name,
        "bytes": published_path.stat().st_size,
        "sha256": sha256_file(published_path),
        "duration_seconds": fmt_duration,
    }
    return {
        "published": str(published_path),
        "sha256": state["deliverable"]["sha256"],
        "duration_seconds": fmt_duration,
    }


# ---------------------------------------------------------------------------
# Etapa 3: SEO
# ---------------------------------------------------------------------------


def build_job_language_context(
    job: Dict[str, Any],
    transcription: Dict[str, Any],
    *,
    subtitle_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Contexto de idioma del fragmento final (solo segmentos del clip).

    Fuente de verdad = audio original:

    - `source_audio_language` explicito del job, o
    - metadato ASR de la transcripcion (solo si no es una traduccion), o
    - deteccion segura sobre la transcripcion.

    Los subtitulos se registran aparte y nunca deciden overlays ni SEO.
    """
    candidate = job["candidate"]
    refs = list(candidate.get("evidence_refs") or [])
    by_id = {s["segment_id"]: s for s in transcription.get("segments", [])}
    missing = [ref for ref in refs if ref not in by_id]
    if missing:
        raise DeliveryError(
            f"Segmentos de evidencia inexistentes: {missing}",
            details={"refs": refs},
        )
    if not refs:
        raise DeliveryError("El candidato no declara segmentos de evidencia.")
    segments = [by_id[ref] for ref in refs]

    transcript_is_translation = bool(
        job.get("transcript_is_translation")
        or transcription.get("transcript_is_translation")
    )

    declared_audio = job.get("source_audio_language")
    if declared_audio:
        source_audio_language = declared_audio
        source_audio_language_source = job.get(
            "source_audio_language_source", "job"
        )
    elif transcription.get("language") and not transcript_is_translation:
        source_audio_language = transcription["language"]
        source_audio_language_source = "transcription_metadata"
    else:
        source_audio_language = None
        source_audio_language_source = "unknown"

    return build_language_context(
        segments,
        source_audio_language=source_audio_language,
        source_audio_language_source=source_audio_language_source,
        subtitle_text=subtitle_text,
        transcript_is_translation=transcript_is_translation,
    )


def _subtitle_text_from_state(state: Dict[str, Any]) -> Optional[str]:
    """Texto real de los subtitulos generados, si el render ya existe."""
    try:
        path = Path(state["render"]["artifacts"]["subtitles"]["path"])
    except (KeyError, TypeError):
        return None
    if not path.exists():
        return None
    texts: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        if len(parts) != 10:
            continue
        body = re.sub(r"\{[^}]*\}", "", parts[9]).replace("\\N", " ")
        body = body.strip()
        if body:
            texts.append(body)
    return " ".join(texts) or None


def resolve_language_context(
    job: Dict[str, Any],
    transcription: Dict[str, Any],
    state: Dict[str, Any],
) -> Dict[str, Any]:
    """Reutiliza el contexto ya calculado o lo reconstruye desde la fuente."""
    existing = state.get("language")
    if existing:
        return existing
    context = build_job_language_context(
        job,
        transcription,
        subtitle_text=_subtitle_text_from_state(state),
    )
    state["language"] = context
    return context


def verify_deliverable_hash(
    job: Dict[str, Any], deliverable: Dict[str, Any]
) -> None:
    """Guardia: el MP4 entregado no puede cambiar entre etapas.

    Permite regenerar el SEO de un Short ya publicado sin volver a renderizar,
    detectando cualquier alteracion del MP4.
    """
    expected = job.get("expected_mp4_sha256")
    if not expected:
        return
    expected = str(expected).upper()
    path = Path(deliverable["path"])
    if not path.exists():
        raise DeliveryError(
            f"No existe el MP4 entregado: {path}",
            details={"path": str(path)},
        )
    actual = sha256_file(path)
    if actual != expected or str(deliverable.get("sha256", "")).upper() != actual:
        raise DeliveryError(
            "El MP4 entregado no coincide con el hash esperado; no se "
            "regenera el SEO sobre un archivo distinto.",
            details={
                "path": str(path),
                "expected": expected,
                "actual": actual,
                "state_sha256": deliverable.get("sha256"),
            },
        )


def stage_seo(ctx: Dict[str, Any]) -> Dict[str, Any]:
    job = ctx["job"]
    state = ctx["state"]
    work = ctx["work"]
    deliverable = state.get("deliverable")
    if not deliverable:
        raise DeliveryError("Falta el entregable validado para generar el SEO.")
    verify_deliverable_hash(job, deliverable)

    transcription = _read_json(Path(job["transcription_path"]))
    try:
        language_context = resolve_language_context(job, transcription, state)
        # Si el idioma no es seguro, se detiene solo el SEO (needs_review).
        require_seo_language(language_context)
        markdown = build_seo(job, deliverable, language_context=language_context)
    except SeoLanguageError as exc:
        raise DeliveryError(
            str(exc),
            details={
                **exc.details,
                "stage": "seo",
                "needs_review": True,
                "render_preserved": True,
            },
        ) from exc
    work_path = work / "seo.md"
    work_path.write_text(markdown, encoding="utf-8")
    _write_json(work / "language_evidence.json", language_context)
    state["seo"] = {
        "finished_at": _now(),
        "work_path": str(work_path),
        "sha256": sha256_file(work_path),
        "bytes": work_path.stat().st_size,
        "language_context": language_context,
        "validation": None,
    }
    return {
        "work_path": str(work_path),
        "bytes": state["seo"]["bytes"],
        "seo_language": language_context["seo_language"],
        "seo_language_decision": language_context["seo_language_decision"],
    }


def stage_validate_seo(ctx: Dict[str, Any]) -> Dict[str, Any]:
    job = ctx["job"]
    state = ctx["state"]
    work = ctx["work"]
    output_dir = ctx["output_dir"]
    if not state.get("seo"):
        raise DeliveryError("Falta la etapa seo en el estado.")
    deliverable = state["deliverable"]
    verify_deliverable_hash(job, deliverable)
    markdown = Path(state["seo"]["work_path"]).read_text(encoding="utf-8")
    transcription = _read_json(Path(job["transcription_path"]))
    language_context = resolve_language_context(job, transcription, state)
    try:
        result = validate_seo(
            markdown,
            segments=transcription["segments"],
            deliverable=deliverable,
            verified_sources=[s["url"] for s in job["seo"].get("sources", [])],
            language_context=language_context,
        )
    except SeoLanguageError as exc:
        raise DeliveryError(
            str(exc),
            details={
                **exc.details,
                "stage": "validate_seo",
                "needs_review": True,
                "render_preserved": True,
            },
        ) from exc
    state["validate_seo"] = {"finished_at": _now(), "result": result}
    state["seo"]["validation"] = result
    if not result["valid"]:
        raise DeliveryError(
            "El SEO no es fiel a la fuente o al entregable.",
            details={"errors": result["errors"], "checks": result["checks"]},
        )

    seo_name = job.get("seo_output_name") or deliverable["file_name"].replace(
        ".mp4", ".seo.md"
    )
    published = Path(output_dir) / str(seo_name)
    _publish_exclusive(Path(state["seo"]["work_path"]), published)
    state["seo"]["output_path"] = str(published)
    state["seo"]["sha256"] = sha256_file(published)
    state["seo"]["bytes"] = published.stat().st_size
    state["seo"]["language_context"] = language_context
    return {
        "published": str(published),
        "valid": result["valid"],
        "seo_language": result["language"]["seo_language"],
    }



# ---------------------------------------------------------------------------
# Etapa 4: manifiesto y evidencia
# ---------------------------------------------------------------------------


def _ad_evidence(job: Dict[str, Any], transcription: Dict[str, Any]) -> Dict[str, Any]:
    """Registra la evidencia literal de los anuncios conocidos (fixtures negativos)."""
    segments = {s["segment_id"]: s for s in transcription["segments"]}
    entries = []
    for fixture in job.get("negative_fixtures") or []:
        evidence = fixture.get("evidence") or []
        if not evidence:
            raise DeliveryError(
                f"El fixture {fixture.get('clip_id')!r} no declara evidencia."
            )
        quotes = []
        for item in evidence:
            ref = item["segment_id"]
            segment = segments.get(ref)
            if segment is None:
                raise DeliveryError(f"Fixture publicitario con segmento inexistente: {ref}")
            quote = str(item["quote"]).strip()
            normalized_segment = " ".join(segment["text"].lower().split())
            if " ".join(quote.lower().split()) not in normalized_segment:
                raise DeliveryError(
                    f"La cita del fixture no es literal en {ref}: {quote!r}"
                )
            quotes.append({
                "segment_id": ref,
                "quote": quote,
                "start_seconds": segment["start_seconds"],
                "end_seconds": segment["end_seconds"],
                "text": segment["text"],
            })
        if len(quotes) < 2:
            raise DeliveryError(
                f"El fixture {fixture.get('clip_id')!r} necesita al menos dos citas."
            )
        entries.append({
            "clip_id": fixture["clip_id"],
            "ad_classification": fixture["ad_classification"],
            "editorial_decision": "reject",
            "brand_or_topic": fixture.get("brand_or_topic"),
            "protected_file": fixture.get("protected_file"),
            "source_range_seconds": fixture.get("source_range_seconds"),
            "evidence": quotes,
            "used_as": "fixture negativo: no debe pasar el filtro anti-anuncios",
        })
    return {
        "method": "Evidencia literal en la transcripcion real del proyecto",
        "entries": entries,
    }


def stage_manifest(ctx: Dict[str, Any]) -> Dict[str, Any]:
    job = ctx["job"]
    state = ctx["state"]
    work = ctx["work"]
    output_dir = ctx["output_dir"]
    if not state.get("validate_seo", {}).get("result", {}).get("valid"):
        raise DeliveryError("No se puede emitir el manifiesto sin SEO valido.")

    protected_after = baselines.check_protected(output_dir)
    if not protected_after["all_ok"]:
        raise DeliveryError(
            "Los entregables protegidos cambiaron durante la entrega.",
            details={"protected": protected_after},
        )

    transcription = _read_json(Path(job["transcription_path"]))
    evidence = _ad_evidence(job, transcription)
    evidence_path = work / "evidence" / "ad_classification_evidence.json"
    _write_json(evidence_path, evidence)

    reproduction = [
        "python -m services.delivery --job <ruta_job.json> --stage render",
        "python -m services.delivery --job <ruta_job.json> --stage validate_mp4",
        "python -m services.delivery --job <ruta_job.json> --stage seo",
        "python -m services.delivery --job <ruta_job.json> --stage validate_seo",
        "python -m services.delivery --job <ruta_job.json> --stage manifest",
        "Cada etapa se omite si consta como succeeded en work/<job_id>/state.json.",
    ]

    manifest = build_manifest(
        job,
        state,
        root=ROOT,
        protected=protected_after,
        reproduction=reproduction,
    )
    manifest["evidence"] = {
        "ad_classification": str(evidence_path.relative_to(ROOT).as_posix()),
        "negative_fixtures": len(evidence["entries"]),
    }
    manifest["job"]["job_file"] = str(
        Path(ctx["job_path"]).resolve().relative_to(ROOT).as_posix()
    )
    manifest["pipeline"] = {
        "entrypoint": "services.delivery.pipeline",
        "stages": list(STAGES),
        "state_file": str((work / "state.json").relative_to(ROOT).as_posix()),
    }

    work_manifest = work / "manifest.json"
    _write_json(work_manifest, manifest)

    required = (
        "schema", "status", "review_status", "deliverable", "provenance",
        "verification", "protected_baselines",
    )
    missing = [key for key in required if not manifest.get(key)]
    if missing:
        raise DeliveryError(
            f"Manifiesto incompleto: faltan {missing}", details={"missing": missing}
        )
    if manifest["review_status"] != "pending_user_review":
        raise DeliveryError("El manifiesto debe quedar pendiente de revision humana.")

    stem = str(job["output_stem"])
    published = Path(output_dir) / f"{stem}.manifest.json"
    _publish_exclusive(work_manifest, published)
    state["manifest"] = {
        "finished_at": _now(),
        "work_path": str(work_manifest),
        "output_path": str(published),
        "sha256": sha256_file(published),
        "bytes": published.stat().st_size,
        "protected_after": protected_after,
    }
    return {"published": str(published), "sha256": state["manifest"]["sha256"]}


STAGE_FUNCS = {
    "render": stage_render,
    "validate_mp4": stage_validate_mp4,
    "seo": stage_seo,
    "validate_seo": stage_validate_seo,
    "manifest": stage_manifest,
}


# ---------------------------------------------------------------------------
# Runner reanudable
# ---------------------------------------------------------------------------


def _parse_stages(value: Any) -> List[str]:
    if value in (None, "", "all"):
        return list(STAGES)
    if isinstance(value, (list, tuple)):
        requested = [str(v).strip() for v in value if str(v).strip()]
    else:
        requested = [part.strip() for part in str(value).split(",") if part.strip()]
    unknown = [name for name in requested if name not in STAGE_FUNCS]
    if unknown:
        raise DeliveryError(
            f"Etapas desconocidas: {unknown}. Validas: {list(STAGES)}",
            details={"stages": requested},
        )
    return requested


def run_job(
    job_path: Path,
    *,
    stages: Any = "all",
    force: bool = False,
    output_dir: Optional[Path] = None,
    work_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Ejecuta (o reanuda) las etapas solicitadas. Devuelve un resumen."""
    job_path = Path(job_path)
    if not job_path.exists():
        raise DeliveryError(f"No existe el job: {job_path}")
    job = _read_json(job_path)
    for key in ("job_id", "project_id", "source_asset_id", "output_stem",
                "source", "candidate", "transcription_path", "seo"):
        if key not in job:
            raise DeliveryError(f"El job no declara el campo obligatorio {key!r}.")

    output_dir = (
        Path(output_dir).resolve() if output_dir else DEFAULT_OUTPUT_DIR
    )
    work = (
        Path(work_dir).resolve()
        if work_dir
        else DEFAULT_WORK_DIR / str(job["job_id"])
    )
    work.mkdir(parents=True, exist_ok=True)
    state_path = work / "state.json"

    if state_path.exists():
        state = _read_json(state_path)
    else:
        state = {
            "job_id": job["job_id"],
            "created_at": _now(),
            "stages": {},
        }
    state.setdefault("stages", {})

    ctx = {
        "job": job,
        "job_path": job_path,
        "work": work,
        "output_dir": output_dir,
        "state": state,
    }
    selected = _parse_stages(stages)
    summary: List[Dict[str, Any]] = []
    for name in selected:
        previous = state["stages"].get(name, {})
        if previous.get("status") == "succeeded" and not force:
            summary.append({"stage": name, "status": "skipped_succeeded"})
            continue

        started_at = _now()
        state["stages"][name] = {"status": "running", "started_at": started_at}
        _write_json(state_path, state)
        try:
            result = STAGE_FUNCS[name](ctx)
        except Exception as exc:  # noqa: BLE001 - se registra y se propaga
            state["stages"][name] = {
                "status": "failed",
                "started_at": started_at,
                "finished_at": _now(),
                "error": str(exc),
                "details": getattr(exc, "details", {}),
                "traceback": traceback.format_exc()[-4000:],
            }
            _write_json(state_path, state)
            raise

        state["stages"][name] = {
            "status": "succeeded",
            "started_at": started_at,
            "finished_at": _now(),
            "result": result,
        }
        _write_json(state_path, state)
        summary.append({"stage": name, "status": "succeeded", "result": result})

    return {
        "job_id": job["job_id"],
        "work_dir": str(work),
        "state_path": str(state_path),
        "stages": summary,
        "state": state,
    }


__all__ = [
    "DeliveryError",
    "EXPECTED_CONTAINER",
    "STAGES",
    "build_job_language_context",
    "container_checks",
    "decode_integrity",
    "measure_alignment",
    "resolve_language_context",
    "run_job",
    "sha256_file",
    "stage_manifest",
    "stage_render",
    "stage_seo",
    "stage_validate_mp4",
    "stage_validate_seo",
    "validate_candidate",
    "verify_deliverable_hash",
]
