"""Composicion de overlays animados y mezcla de audio (heredado del MVP).

Porta el comportamiento exacto de `render_timing.py` y `mix_audio.py` del
pipeline aprobado:

- las tarjetas PNG se animan (entrada/hold/salida) frame a frame en un `.mov`
  RGBA con canal alfa (QuickTime `qtrle`);
- el `.mov` se superpone al video base con FFmpeg;
- el clic sintetico se mezcla con `amix` sin limiter, sin ducking y sin
  ganancia global.

No se cambia ningun parametro aprobado: 150 px de desplazamiento, fade de
300/320 ms, `enter_ms=520`, `hold_ms=3600`, `exit_ms=420`.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import numpy as np
from PIL import Image

FPS = 30000 / 1001


def run(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )


def video_duration(path: Path) -> float:
    proc = run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=duration", "-of", "default=nw=1:nk=1", str(path),
    ])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-2000:])
    return float(proc.stdout.strip())


def ease_out_cubic(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return 1 - (1 - p) ** 3


def ease_in_cubic(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return p ** 3


def animation_segments(notifications: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    segs = []
    for n in notifications:
        t0 = float(n["enter_start_seconds"])
        segs.append({
            "action": n["action"],
            "start": t0,
            "enter_end": float(n["hold_start_seconds"]),
            "hold_end": float(n["hold_end_seconds"]),
            "exit_end": float(n["exit_end_seconds"]),
            "enter_dy": 150.0,
            "exit_dy": 150.0,
            "fade_in": 0.3,
            "fade_out": 0.32,
        })
    return segs


def offset_alpha(t: float, s: Dict[str, Any]) -> tuple[float, float]:
    if t < s["start"]:
        return 0.0, 0.0
    if t < s["enter_end"]:
        p = (t - s["start"]) / max(1e-6, s["enter_end"] - s["start"])
        return (s["enter_dy"] * (1 - ease_out_cubic(p)),
                min(1.0, (t - s["start"]) / s["fade_in"]))
    if t < s["hold_end"]:
        return 0.0, 1.0
    if t < s["exit_end"]:
        p = (t - s["hold_end"]) / max(1e-6, s["exit_end"] - s["hold_end"])
        dy = -s["exit_dy"] * ease_in_cubic(p)
        fade_start = s["exit_end"] - s["fade_out"]
        alpha = 1.0 if t < fade_start else max(
            0.0, 1.0 - (t - fade_start) / s["fade_out"]
        )
        return dy, alpha
    return -s["exit_dy"], 0.0


def render_overlay_mov(
    cards: List[Image.Image],
    notifications: List[Dict[str, Any]],
    *,
    duration: float,
    out_mov: Path,
    base_x: int = 100,
    base_y: int = 150,
    glow: int = 26,
    width: int = 1080,
    height: int = 1920,
    fps: float = FPS,
) -> Dict[str, Any]:
    """Escribe el `.mov` RGBA animado y devuelve metadatos reales."""
    if len(cards) != len(notifications):
        raise ValueError("cards y notifications deben tener la misma longitud")
    out_mov = Path(out_mov)
    out_mov.parent.mkdir(parents=True, exist_ok=True)

    anim = animation_segments(notifications)
    n_frames = int(round(duration * fps))
    card_np = [np.array(c).astype(np.float32) for c in cards]
    x = int(round(base_x - glow))
    y_base = int(round(base_y - glow))

    cmd = [
        "ffmpeg", "-hide_banner", "-y", "-f", "rawvideo", "-pix_fmt", "rgba",
        "-s", f"{width}x{height}", "-r", "30000/1001", "-i", "-",
        "-c:v", "qtrle", "-pix_fmt", "argb", str(out_mov),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for i in range(n_frames):
            t = i / fps
            frame = np.zeros((height, width, 4), dtype=np.uint8)
            for idx, seg in enumerate(anim):
                dy, alpha = offset_alpha(t, seg)
                if alpha <= 0.0:
                    continue
                cnp = card_np[idx]
                cw, ch = cards[idx].size
                y = int(round(y_base + dy))
                sx0 = max(0, -x)
                sy0 = max(0, -y)
                dx0 = max(0, x)
                dy0 = max(0, y)
                w = min(cw - sx0, width - dx0)
                h = min(ch - sy0, height - dy0)
                if w <= 0 or h <= 0:
                    continue
                tile = cnp[sy0:sy0 + h, sx0:sx0 + w].copy()
                tile[:, :, 3] = np.clip(tile[:, :, 3] * alpha, 0, 255)
                region = frame[dy0:dy0 + h, dx0:dx0 + w].astype(np.float32)
                a = tile[:, :, 3:4] / 255.0
                region = tile[:, :, :3] * a + region[:, :, :3] * (1 - a)
                frame[dy0:dy0 + h, dx0:dx0 + w, :3] = region.astype(np.uint8)
                frame[dy0:dy0 + h, dx0:dx0 + w, 3] = np.maximum(
                    frame[dy0:dy0 + h, dx0:dx0 + w, 3], tile[:, :, 3]
                ).astype(np.uint8)
            proc.stdin.write(frame.tobytes())
    finally:
        if proc.stdin:
            proc.stdin.close()
        stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg fallo generando overlay: {stderr[-2000:]}")
    return {
        "path": str(out_mov),
        "bytes": out_mov.stat().st_size,
        "frames": n_frames,
        "fps": "30000/1001",
        "width": width,
        "height": height,
        "notifications": len(notifications),
    }


def compose_overlay(
    base_video: Path,
    overlay_mov: Path,
    out_video: Path,
    *,
    crf: int = 18,
    preset: str = "veryfast",
) -> Dict[str, Any]:
    out_video = Path(out_video)
    out_video.parent.mkdir(parents=True, exist_ok=True)
    proc = run([
        "ffmpeg", "-hide_banner", "-y", "-i", str(base_video), "-i", str(overlay_mov),
        "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto,format=yuv420p",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-an", str(out_video),
    ])
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg fallo componiendo overlay: {proc.stderr[-2000:]}")
    return {"path": str(out_video), "bytes": out_video.stat().st_size}


def measure_loudness(infile: Path) -> Dict[str, float]:
    proc = run([
        "ffmpeg", "-hide_banner", "-i", str(infile),
        "-af", "volumedetect", "-f", "null", "-",
    ])
    text = proc.stderr + proc.stdout
    result: Dict[str, float] = {}
    for line in text.splitlines():
        if "mean_volume" in line:
            try:
                result["mean_volume_dB"] = float(
                    line.split("mean_volume:")[1].split("dB")[0].strip()
                )
            except (IndexError, ValueError):
                pass
        elif "max_volume" in line:
            try:
                result["max_volume_dB"] = float(
                    line.split("max_volume:")[1].split("dB")[0].strip()
                )
            except (IndexError, ValueError):
                pass
    return result


def mix_with_clicks(
    src_video: Path,
    audio_source: Path,
    notifications: List[Dict[str, Any]],
    click_wav: Path,
    out_video: Path,
    *,
    click_gain_db: float = -2.0,
    click_duration_s: float = 0.045,
    delay_s: float = 0.012,
    aac_bitrate: str = "320k",
) -> Dict[str, Any]:
    """Mezcla los clics de cada tarjeta sin limiter, ducking ni ganancia global."""
    enabled = [
        n for n in notifications
        if n.get("click", {}).get("enabled_in_mix", True)
    ]
    if not enabled:
        proc = run([
            "ffmpeg", "-hide_banner", "-y", "-i", str(src_video), "-i", str(audio_source),
            "-c:v", "copy", "-c:a", "aac", "-b:a", aac_bitrate,
            "-map", "0:v:0", "-map", "1:a:0", "-shortest", str(out_video),
        ])
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr[-2000:])
        return {"mode": "copy_with_source_audio", "clicks": 0}

    inputs = ["-i", str(src_video), "-i", str(audio_source), "-i", str(click_wav)]
    filters = []
    for i, n in enumerate(enabled):
        target = float(n.get("enter_start_seconds", 0.0)) + delay_s
        delay_ms = int(round(target * 1000.0))
        gain_db = float(n.get("click", {}).get("gain_db", click_gain_db))
        gain = 10 ** (gain_db / 20.0)
        duration = float(n.get("click", {}).get("duration_s", click_duration_s))
        filters.append(
            f"[2:a]atrim=0:{duration},asetpts=PTS-STARTPTS,"
            f"adelay={delay_ms}|{delay_ms},volume={gain:.6f}[c{i}]"
        )
    if len(enabled) == 1:
        mix = "[1:a][c0]amix=inputs=2:duration=first:dropout_transition=0:normalize=0"
    else:
        inputs_str = "[1:a]" + "".join(f"[c{i}]" for i in range(len(enabled)))
        mix = (
            f"{inputs_str}amix=inputs={len(enabled) + 1}:duration=first:"
            "dropout_transition=0:normalize=0"
        )
    filter_complex = ";".join(filters) + ";" + mix + "[aout]"
    proc = run([
        "ffmpeg", "-hide_banner", "-y", *inputs,
        "-filter_complex", filter_complex,
        "-map", "0:v:0", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", aac_bitrate, str(out_video),
    ])
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg fallo mezclando clics: {proc.stderr[-2000:]}")
    return {
        "mode": "mix_clicks",
        "clicks": len(enabled),
        "delay_s": delay_s,
        "gain_db": click_gain_db,
        "no_limiter": True,
        "no_ducking": True,
        "no_global_gain": True,
        "measurements": measure_loudness(Path(out_video)),
    }


def write_plan_manifest(path: Path, plan: Dict[str, Any]) -> None:
    Path(path).write_text(
        json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8"
    )


__all__ = [
    "FPS",
    "animation_segments",
    "compose_overlay",
    "measure_loudness",
    "mix_with_clicks",
    "offset_alpha",
    "render_overlay_mov",
    "video_duration",
]
