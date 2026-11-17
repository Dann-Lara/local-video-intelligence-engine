"""Auditoria objetiva del clic de notificaciones (solo lectura).

No modifica MP4, WAV de clic, codigo de mezcla ni configuracion. Extrae copias
temporales de audio a work/run_20261117_git_audio_audit/extracts/ para medir:
propiedades del clic, nivel con ganancia, sincronizacion, presencia en el
audio final y relacion con el audio original.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(r"C:\Users\SPARTAN PC\Documents\new_life\clipping")
RUN = ROOT / "work" / "run_20261116_new_source_full_pipeline"
AUDIT = ROOT / "work" / "run_20261117_git_audio_audit"
EXTRACTS = AUDIT / "extracts"
EXTRACTS.mkdir(parents=True, exist_ok=True)
OUTPUT = ROOT / "output"
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8")

CLICK = ROOT / "work" / "mvp_short_clip5_20261028_200526" / "audio" / "click_hf_v1_stereo.wav"
STEMS = [
    "a7ace6f1_clip_0001_es_20261117",
    "a7ace6f1_clip_0002_es_20261117",
    "a7ace6f1_clip_0003_es_20261117",
    "a7ace6f1_clip_0004_es_20261117",
    "a7ace6f1_clip_0008_es_20261117",
    "a7ace6f1_clip_0009_es_20261117",
]
SR = 44100


def ffmpeg_audio(path: Path, out: Path) -> Path:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", str(path),
         "-vn", "-ac", "2", "-ar", str(SR), "-c:a", "pcm_s16le", str(out)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg fallo {path}: {proc.stderr[-2000:]}")
    return out


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        ch = w.getnchannels()
        rate = w.getframerate()
        frames = w.readframes(w.getnframes())
    data = np.frombuffer(frames, dtype="<i2").astype(np.float64) / 32768.0
    if rate != SR:
        raise ValueError(f"sample rate inesperado {rate} en {path}")
    return data.reshape(-1, ch)


def stats(x: np.ndarray) -> dict:
    if x.size == 0:
        return {"rms": 0.0, "peak": 0.0, "rms_dbfs": None, "peak_dbfs": None}
    rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
    peak = float(np.max(np.abs(x)))
    return {
        "rms": round(rms, 6),
        "peak": round(peak, 6),
        "rms_dbfs": round(20 * np.log10(rms), 2) if rms > 0 else None,
        "peak_dbfs": round(20 * np.log10(peak), 2) if peak > 0 else None,
    }


def loudness(path: Path) -> dict:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
         "-filter_complex", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    text = proc.stderr + proc.stdout
    summary = text[text.rfind("Summary:"):] if "Summary:" in text else text[-3000:]
    result = {}
    for key, pattern in (
        ("integrated_lufs", r"I:\s*(-?[\d.]+) LUFS"),
        ("true_peak_dbtp", r"Peak:\s*(-?[\d.]+) dBFS"),
        ("lra_lu", r"LRA:\s*([\d.]+) LU"),
    ):
        m = re.search(pattern, summary)
        if m:
            result[key] = float(m.group(1))
    return result


def best_lag(reference: np.ndarray, target: np.ndarray, expected: int, radius: int) -> tuple[int, float]:
    """Lag (en muestras) que minimiza la diferencia en una ventana corta."""
    n = min(6000, reference.shape[0], target.shape[0])
    seg = reference[:n]
    best = None
    for lag in range(expected - radius, expected + radius + 1):
        if lag < 0 or lag + n > target.shape[0]:
            continue
        err = float(np.mean(np.square(seg - target[lag:lag + n])))
        if best is None or err < best[1]:
            best = (lag, err)
    return best if best else (0, float("inf"))


def band_signal(x: np.ndarray, sr: int, lo: float, hi: float) -> np.ndarray:
    """Reconstruye solo la banda [lo, hi] Hz para medir enmascaramiento."""
    n = x.shape[0]
    spec = np.fft.rfft(x, axis=0)
    freqs = np.fft.rfftfreq(n, 1 / sr)
    mask = (freqs >= lo) & (freqs <= hi)
    spec[~mask] = 0.0
    return np.fft.irfft(spec, n, axis=0)


def corr(a: np.ndarray, b: np.ndarray) -> float:
    if a.size != b.size or a.size == 0:
        return 0.0
    x = a - a.mean()
    y = b - b.mean()
    denom = float(np.sqrt(np.sum(x * x) * np.sum(y * y)))
    return float(np.sum(x * y) / denom) if denom > 0 else 0.0


def analyse() -> dict:
    report: dict = {
        "click_file": str(CLICK.relative_to(ROOT)).replace("\\", "/"),
        "click_props": {},
        "clips": [],
    }
    click_raw = read_wav(CLICK)
    report["click_props"]["file"] = stats(click_raw)
    report["click_props"]["loudness"] = loudness(CLICK)
    report["click_props"]["configured_gain_db"] = -2.0
    gain = 10 ** (-2.0 / 20.0)
    click_gained = click_raw * gain
    report["click_props"]["with_gain_minus_2db"] = stats(click_gained)
    report["click_props"]["gain_linear"] = round(gain, 6)

    for stem in STEMS:
        job = json.loads((RUN / "jobs" / f"{stem}.json").read_text(encoding="utf-8"))
        notes = job.get("notifications") or []
        enabled = [n for n in notes if n.get("click", {}).get("enabled_in_mix", True)]
        original_path = ffmpeg_audio(
            RUN / "delivery" / stem / "input_composed.mp4",
            EXTRACTS / f"{stem}_original.wav",
        )
        final_path = ffmpeg_audio(
            OUTPUT / f"{stem}.mp4",
            EXTRACTS / f"{stem}_final.wav",
        )
        original = read_wav(original_path)
        final = read_wav(final_path)
        # Alineacion gruesa: el audio final pasa por AAC 320k; buscar lag en
        # una ventana limpia (0.5-1.0 s) para compensar posibles retardos.
        marker = int(round(0.5 * SR))
        absolute_lag, err = best_lag(original[marker:marker + int(0.5 * SR)],
                                    final, marker, 2205)
        # `absolute_lag` apunta al sample del final que corresponde al marker.
        # El desplazamiento relativo desde el inicio es lo que hay que aplicar.
        lag = absolute_lag - marker
        if lag >= 0:
            aligned = final[lag:lag + original.shape[0]]
            if aligned.shape[0] < original.shape[0]:
                pad = np.zeros((original.shape[0] - aligned.shape[0], 2))
                aligned = np.vstack([aligned, pad])
        else:
            aligned = np.vstack([
                np.zeros((-lag, 2)),
                final[:original.shape[0] + lag],
            ])
        residual = aligned - original

        click_events = []
        for idx, note in enumerate(enabled):
            t0 = float(note["enter_start_seconds"])
            delay = float(note.get("click", {}).get("delay_s", 0.012))
            dur = float(note.get("click", {}).get("duration_s", 0.045))
            gain_db = float(note.get("click", {}).get("gain_db", -2.0))
            g = 10 ** (gain_db / 20.0)
            expected_click = click_raw * g
            expected_onset = int(round((t0 + delay) * SR))
            length = min(int(round(dur * SR)), expected_click.shape[0])
            # Buscar el clic real en el residuo alrededor del onset esperado.
            found_absolute, found_err = best_lag(
                expected_click[:length],
                residual, expected_onset, 2205,
            )
            actual_onset = found_absolute
            n = length
            click_window = residual[actual_onset:actual_onset + n]
            before = residual[max(0, actual_onset - int(0.15 * SR)):actual_onset]
            after = residual[actual_onset + n:actual_onset + n + int(0.15 * SR)]
            bg_before = original[max(0, actual_onset - int(0.15 * SR)):actual_onset]
            bg_after = original[actual_onset + n:actual_onset + n + int(0.15 * SR)]
            bg = np.vstack([bg_before, bg_after]) if bg_before.shape[0] and bg_after.shape[0] else bg_before
            # Comparacion en la banda disenada del clic (7-15 kHz): ahi es
            # donde debe competir con el audio original, no en RMS broadband.
            bg_equal = original[max(0, actual_onset - n):actual_onset]
            if bg_equal.shape[0] < n:
                bg_equal = np.vstack([
                    np.zeros((n - bg_equal.shape[0], 2)), bg_equal
                ])
            click_band = stats(band_signal(click_window, SR, 7000, 15000))
            bg_band = stats(band_signal(bg_equal, SR, 7000, 15000))
            final_band = stats(band_signal(aligned[actual_onset:actual_onset + n], SR, 7000, 15000))
            original_band = stats(band_signal(original[actual_onset:actual_onset + n], SR, 7000, 15000))
            margin = None
            if click_band["rms"] and bg_band["rms"]:
                margin = round(20 * np.log10(click_band["rms"] / bg_band["rms"]), 2)
            click_events.append({
                "index": idx + 1,
                "action": note.get("action"),
                "notification_enter_seconds": t0,
                "configured_delay_s": delay,
                "gain_db": gain_db,
                "expected_onset_seconds": round(expected_onset / SR, 6),
                "measured_onset_seconds": round(actual_onset / SR, 6),
                "onset_delta_ms": round((actual_onset - expected_onset) / SR * 1000, 3),
                "residual_click": stats(click_window),
                "residual_before": stats(before),
                "residual_after": stats(after),
                "background_original": stats(bg),
                "expected_click_with_gain": stats(expected_click[:n]),
                "band_7000_15000": {
                    "click_residual": click_band,
                    "background_before_same_length": bg_band,
                    "original_same_window": original_band,
                    "final_same_window": final_band,
                    "click_minus_background_db": margin,
                },
                "correlation_residual_vs_expected_click": round(
                    corr(click_window.ravel(), expected_click[:n].ravel()), 4),
                "best_fit_mse": round(found_err, 8),
            })

        item = {
            "stem": stem,
            "original": stats(original),
            "final": stats(final),
            "final_loudness": loudness(OUTPUT / f"{stem}.mp4"),
            "alignment_lag_samples": lag,
            "alignment_lag_ms": round(lag / SR * 1000, 3),
            "enabled_clicks": len(enabled),
            "click_events": click_events,
            "overall_residual": stats(residual),
        }
        report["clips"].append(item)
        print(stem, "clics", len(enabled),
              "residuo pico", item["overall_residual"]["peak_dbfs"],
              "clic pico final", [c["residual_click"]["peak_dbfs"] for c in click_events],
              "delta ms", [c["onset_delta_ms"] for c in click_events],
              "corr", [c["correlation_residual_vs_expected_click"] for c in click_events])

    out = AUDIT / "audio_measurements.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("escrito", out)
    return report


if __name__ == "__main__":
    analyse()
