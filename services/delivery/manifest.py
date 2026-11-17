"""Manifiesto de entrega con el contrato minimo del estandar aprobado.

No reescribe estados de revision ni convierte una validacion automatica en
aprobacion humana. `review_status` queda siempre en `pending_user_review` y
`human_review.approved` en `false`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

SCHEMA = "local_video_studio.mvp_deliverable.v1"


def _rel(path: Any, root: Path) -> Any:
    if not isinstance(path, str):
        return path
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve())).replace("\\", "/")
    except ValueError:
        return path


def _probe_parts(probe: Dict[str, Any]) -> tuple:
    """Devuelve (video, audio, format) tanto del probe normalizado como del crudo.

    `stage_validate_mp4` guarda el JSON crudo de FFprobe (`streams`), mientras
    que otras rutas guardan claves planas. Sin esta normalizacion el manifiesto
    declararia codec/geometria en `null`.
    """
    if probe.get("video") or probe.get("audio"):
        return (
            probe.get("video") or {},
            probe.get("audio") or {},
            probe.get("format") or {},
        )
    streams = probe.get("streams") or []
    video = next(
        (s for s in streams if s.get("codec_type") == "video"), {}
    )
    audio = next(
        (s for s in streams if s.get("codec_type") == "audio"), {}
    )
    return video, audio, probe.get("format") or {}


def _localization_block(
    state: Dict[str, Any], render: Dict[str, Any]
) -> Dict[str, Any]:
    """Resume la separacion de idiomas y la localizacion real de las tarjetas."""
    context = state.get("language") or {}
    cards = render.get("cards_anim", [])
    return {
        "rule": (
            "Los overlays (tarjetas y énfasis) y el SEO usan el idioma del audio "
            "original; los subtitulos conservan su propio idioma."
        ),
        "source_audio_language": context.get("source_audio_language"),
        "source_audio_language_source": context.get(
            "source_audio_language_source"
        ),
        "transcript_language": context.get("transcript_language"),
        "transcript_is_translation": context.get("transcript_is_translation"),
        "subtitle_language": context.get("subtitle_language"),
        "notification_language": context.get("notification_language"),
        "emphasis_language": context.get("emphasis_language"),
        "overlay_language": context.get("overlay_language"),
        "seo_language": context.get("seo_language"),
        "decision": context.get("overlay_language_decision"),
        "cards": [
            {
                "action": card.get("action"),
                "language": card.get("language"),
                "title": card.get("title_text"),
                "subtitle": card.get("subtitle_text"),
                "subtitle_source": card.get("subtitle_source"),
                "fits": card.get("fits"),
            }
            for card in cards
        ],
    }


def build_manifest(
    job: Dict[str, Any],
    state: Dict[str, Any],
    *,
    root: Path,
    protected: Dict[str, Any],
    reproduction: list,
) -> Dict[str, Any]:
    render = state["render"]["artifacts"]
    validation = state["validate_mp4"]
    seo_state = state["seo"]
    seo_validation = state["validate_seo"]["result"]
    deliverable = state["deliverable"]
    candidate = job["candidate"]
    source = job["source"]

    probe = validation.get("probe", {})
    video, audio, fmt = _probe_parts(probe)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    return {
        "schema": SCHEMA,
        "created_at": now,
        "status": "delivered_pending_human_review",
        "review_status": "pending_user_review",
        "human_review": {
            "approved": False,
            "note": (
                "Ningun filtro automatico sustituye la aprobacion humana. "
                "Este entregable no debe publicarse sin revision."
            ),
        },
        "job": {
            "job_id": job["job_id"],
            "project_id": job["project_id"],
            "source_asset_id": job["source_asset_id"],
            "output_stem": job["output_stem"],
        },
        "deliverable": {
            "file": _rel(deliverable["path"], root),
            "bytes": deliverable["bytes"],
            "sha256": deliverable["sha256"],
            "duration_seconds": deliverable["duration_seconds"],
            "video_duration_seconds": video.get("duration"),
            "audio_duration_seconds": audio.get("duration"),
            "video": {
                "codec": video.get("codec_name"),
                "profile": video.get("profile"),
                "pix_fmt": video.get("pix_fmt"),
                "width": video.get("width"),
                "height": video.get("height"),
                "r_frame_rate": video.get("r_frame_rate"),
                "nb_frames": video.get("nb_frames"),
            },
            "audio": {
                "codec": audio.get("codec_name"),
                "profile": audio.get("profile"),
                "sample_rate": audio.get("sample_rate"),
                "channels": audio.get("channels"),
                "bit_rate": audio.get("bit_rate"),
            },
            "container": fmt.get("format_name"),
        },
        "provenance": {
            "source_asset": {
                "path": source["path"],
                "bytes": source["bytes"],
                "sha256": source["sha256"],
                "project_id": job["project_id"],
                "source_asset_id": job["source_asset_id"],
                "transcription": job["transcription_path"],
            },
            "source_cut": {
                **render["source_cut"],
                "method": (
                    "FFmpeg seek a keyframe previo + -ss de salida "
                    "(video recodificado en frame exacto, audio copiado)"
                ),
            },
            "vertical_source_clip": {
                **render["vertical"],
                "method": "services.rendering.renderer.render_vertical",
            },
            "editorial_selection": {
                "clip_id": candidate["clip_id"],
                "title": candidate["title"],
                "origin": candidate.get("origin", "transcripcion_real_sin_lm_studio"),
                "requested_range_seconds": [
                    candidate["requested_start_seconds"],
                    candidate["requested_end_seconds"],
                ],
                "cut_range_seconds": [
                    candidate["start_seconds"],
                    candidate["end_seconds"],
                ],
                "actual_start_seconds": state["timing_alignment"][
                    "clip_zero_maps_to_source_seconds"
                ],
                "segments": candidate["evidence_refs"],
                "evidence_text": candidate["evidence_text"],
                "ad_classification": candidate["ad_classification"],
                "ad_evidence": candidate.get("ad_evidence", ""),
                "editorial_decision": candidate["editorial_decision"],
                "editorial_decision_reason": candidate.get("reason", ""),
                "boundary_evaluation": candidate.get("boundary_evaluation", {}),
                "confidence": candidate.get("confidence"),
                "review_status": "pending_user_review",
                "independence": {
                    "overlap_seconds": state["overlap_seconds"],
                    "zero_overlap": all(
                        v == 0.0 for v in state["overlap_seconds"].values()
                    ),
                },
            },
        },
        "timing_alignment": state["timing_alignment"],
        "animations": {
            "fps": "30000/1001",
            "cards": render.get("cards_anim", []),
            "approach": (
                "Tarjetas rasterizadas y animadas por frame en overlay .mov RGBA; "
                "misma implementacion del MVP aprobado."
            ),
        },
        "overlays": {
            "subtitles": {
                **render["subtitles"],
                "style": {
                    "font": "Arial Black 96",
                    "colour": "blanco con contorno negro",
                    "max_lines": 2,
                    "margin_bottom": 340,
                    "alignment": "centro inferior",
                },
                "limitations": [
                    "sin word_timestamps: los cortes de subtitulo son por segmento",
                    "el primer subtitulo puede incluir palabras anteriores al corte",
                ],
            },
            "emphasis": {
                **render["emphasis"],
                "style": {
                    "font": "Arial Black 84",
                    "position": {"x": 540, "y": 1010},
                    "animation": "escala 45% -> 100% -> 103%",
                },
            },
            "neon_frame": {
                **render["neon"],
                "design": "tiras azul electrico/cian segmentadas, solo en el borde",
                "seed": 20261020,
            },
            "cards": render.get("cards_anim", []),
            "notifications": render["notifications"],
            "click": render["click"],
        },
        "audio_mix": {
            **render["audio_mix"],
            "filter": (
                "atrim + asetpts + adelay por clic; "
                "amix=inputs=N:duration=first:dropout_transition=0:normalize=0"
            ),
            "original_audio_preserved": True,
            "limiter": False,
            "ducking": False,
            "global_gain": False,
            "aac_bitrate": "320k",
        },
        "seo": {
            "file": _rel(seo_state["output_path"], root),
            "work_copy": _rel(seo_state["work_path"], root),
            "sha256": seo_state["sha256"],
            "bytes": seo_state["bytes"],
            "validation": seo_validation,
            "language": (state.get("language") or {}).get("seo_language"),
            "publication": "not_performed",
        },
        "language": state.get("language") or {},
        "localization": _localization_block(state, render),
        "verification": {
            "container_checks": validation["checks"],
            "all_container_checks_ok": validation["all_ok"],
            "ffprobe": probe,
            "decode_integrity": validation["decode_integrity"],
            "protected_before": state.get("protected_before", {}),
            "protected_after": protected,
            "all_protected_unchanged": protected.get("all_ok", False),
            "seo_valid": seo_validation.get("valid", False),
        },
        "protected_baselines": protected,
        "reproduction": reproduction,
        "limitations": [
            "Sin word_timestamps: enfasis y subtitulos usan tiempos de segmento.",
            "La seleccion editorial se deriva de la transcripcion real; no se ejecuto LM Studio.",
            "El audio fuente puede superar 0 dBFS; no se aplica limiter ni normalizacion.",
            "La aprobacion humana esta pendiente: no se publica automaticamente.",
            "La duracion de audio y video puede diferir unos milisegundos.",
            "No se ha verificado percepcion humana del clic; solo niveles medidos.",
        ],
    }


__all__ = ["SCHEMA", "build_manifest"]
