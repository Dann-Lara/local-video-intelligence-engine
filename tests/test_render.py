"""Render de clips (Sprint 6): builders puros + smoke real de FFmpeg/libass."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.media import ffmpeg  # noqa: E402
from src.media import render as render_module  # noqa: E402
from src.subtitles import cues as cues_module  # noqa: E402


def _plan(
    source: Path,
    *,
    clip_id: str = "clip_001",
    start: float = 0.0,
    end: float = 3.0,
    renderer: str = "ffmpeg_libass",
    enabled: bool = True,
    container: str = "mp4",
    video_codec: str = "h264_nvenc",
) -> dict:
    return {
        "schema_version": "1.0",
        "clip_id": clip_id,
        "job_id": "job_001",
        "candidate_id": clip_id,
        "source": {"file": str(source), "start": start, "end": end, "duration": round(end - start, 3)},
        "output": {
            "width": 1080,
            "height": 1920,
            "fps": 30,
            "format": "9:16",
            "video_codec": video_codec,
            "crf": 20,
            "preset": "p4",
            "pixel_format": "yuv420p",
            "audio_codec": "aac",
            "audio_bitrate": "192k",
            "container": container,
        },
        "crop": {"mode": "center", "background": "crop"},
        "subtitles": {
            "enabled": enabled,
            "style": "default",
            "language": None,
            "max_words_per_line": 4,
            "max_lines": 2,
            "max_chars_per_line": 22,
            "min_duration_ms": 800,
            "max_duration_ms": 4000,
            "position": "lower_center",
            "renderer": renderer,
        },
        "hook": {"type": "none", "origin": "original", "text": None, "requires_generated_hook": False, "displayed": False, "integrity_note": None},
        "audio": {
            "normalize": True,
            "target_lufs": -14.0,
            "true_peak_db": -1.5,
            "fade_in_seconds": 0.0,
            "fade_out_seconds": 0.15,
            "music": None,
        },
        "effects": [],
    }


def _transcript() -> dict:
    word_tuples = [
        (0.2, 0.6, "esto"),
        (0.6, 1.0, "es"),
        (1.0, 1.5, "un"),
        (1.5, 2.1, "test"),
        (2.1, 2.6, "de"),
        (2.6, 3.0, "subtitulos"),
    ]
    words = [{"word": w, "start": s, "end": e, "probability": 0.9} for s, e, w in word_tuples]
    return {
        "schema_version": "1.0",
        "language": "es",
        "duration": 3.0,
        "segments": [{"id": 0, "start": 0.0, "end": 3.0, "text": "esto es un test de subtitulos", "words": words}],
    }


def _ffprobe(path: Path) -> dict:
    result = ffmpeg.run(
        [ffmpeg.require("ffprobe"), "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)]
    )
    return result.json()


_ffmpeg_skip = pytest.mark.skipif(
    ffmpeg.which("ffmpeg") is None or ffmpeg.which("ffprobe") is None,
    reason="FFmpeg/FFprobe no estan en PATH",
)


# --- builders puros ----------------------------------------------------------


def test_build_ffmpeg_clip_args_es_determinista(portrait_video, monkeypatch):
    monkeypatch.setattr(ffmpeg, "require", lambda name: name)
    plan = _plan(portrait_video)
    args = render_module.build_ffmpeg_clip_args(plan, output_path="out/x.mp4", subtitles_name="clip_001.ass")
    joined = " ".join(args)
    assert "-ss 0.000" in joined
    assert "-t 3.000" in joined
    assert "scale=1080:1920:force_original_aspect_ratio=increase:force_divisible_by=2,crop=1080:1920,subtitles=clip_001.ass" in joined
    assert "loudnorm=I=-14:TP=-1.5:LRA=11" in joined
    assert "afade=t=out:st=2.85:d=0.15" in joined
    assert "-map_metadata -1" in joined
    assert "-sn" in joined
    assert "+faststart" in joined
    assert str(portrait_video) in joined
    again = render_module.build_ffmpeg_clip_args(plan, output_path="out/x.mp4", subtitles_name="clip_001.ass")
    assert again == args


def test_ventana_vacia_rebotada(portrait_video):
    plan = _plan(portrait_video, start=2.0, end=2.0)
    with pytest.raises(render_module.RenderError) as exc:
        render_module.build_ffmpeg_clip_args(plan, output_path="out/x.mp4")
    assert exc.value.code == "empty_window"


def test_cae_a_libx264_sin_nvenc(portrait_video, monkeypatch):
    monkeypatch.setattr(ffmpeg, "require", lambda name: name)
    monkeypatch.setattr(render_module, "_nvenc_available", lambda: False)
    args = render_module.build_ffmpeg_clip_args(_plan(portrait_video), output_path="out/x.mp4")
    assert "-c:v libx264" in " ".join(args)
    assert " -preset medium " in " ".join(args)


@_ffmpeg_skip
def test_resolve_video_encoder_devuelve_el_codec_del_plan():
    assert render_module.resolve_video_encoder({"video_codec": "h264_nvenc"}) == "h264_nvenc"
    assert render_module.resolve_video_encoder({"video_codec": "libx264"}) == "libx264"


def test_compose_remotion_props(portrait_video):
    plan = _plan(portrait_video, renderer="remotion", clip_id="cand_001")
    plan["hook"] = {"type": "question", "origin": "original", "text": "sabes algo?"}
    cues = [cues_module.SubtitleCue(start=0.0, end=1.0, text="hola")]
    props = render_module.compose_remotion_props(plan, video_src=portrait_video, cues=cues, config={})
    # Solo el nombre: `OffthreadVideo` se baja por HTTP y el proxy de Remotion
    # rechaza file://, asi que la ruta absoluta llega como videoSrc y falla.
    assert props["videoSrc"] == portrait_video.name
    assert "\\\\" not in props["videoSrc"] and ":" not in props["videoSrc"]
    assert props["durationInSeconds"] == pytest.approx(3.0)
    assert props["fps"] == 30
    assert props["title"] == "sabes algo?"
    assert props["fit"] == "cover"
    assert props["subtitles"] == [{"start": 0.0, "end": 1.0, "text": "hola"}]


def test_remotion_args_sirven_el_master_y_reusan_el_navegador(tmp_path):
    """Sin --public-dir el master es inaccesible; sin browser, Remotion descarga."""
    argv = render_module.build_remotion_args(
        "C:/fake/node.exe",
        tmp_path / "remotion-cli.js",
        component_id="Clip",
        props_path=tmp_path / "c.props.json",
        output_path=tmp_path / "c.mp4",
        crf=20,
        public_dir=tmp_path / "renders",
        browser_executable="C:/Program Files/Google/Chrome/Application/chrome.exe",
    )
    joined = " ".join(argv)
    assert "--public-dir" in argv and str(tmp_path / "renders") in argv
    assert "--browser-executable" in joined and "chrome.exe" in joined
    # Sin esto Remotion emite yuvj420p (full range) en vez del yuv420p del plan.
    assert argv[argv.index("--color-space") + 1] == "bt709"
    # Sin nada de eso se omiten los flags, no se mandan vacios.
    bare = render_module.build_remotion_args(
        "node", tmp_path / "e.js", component_id="Clip",
        props_path=tmp_path / "p.json", output_path=tmp_path / "o.mp4", crf=20,
    )
    assert "--public-dir" not in bare and "--browser-executable" not in bare


def test_hook_none_no_genera_titulo(portrait_video):
    props = render_module.compose_remotion_props(
        _plan(portrait_video, renderer="remotion"), video_src="x.mp4", cues=[], config={}
    )
    assert props["title"] is None


def test_duracion_en_frames_exactos(portrait_video):
    plan = _plan(portrait_video, start=0.0, end=0.5)
    props = render_module.compose_remotion_props(plan, video_src="x.mp4", cues=[], config={})
    assert props["durationInSeconds"] == pytest.approx(15 / 30)


def test_build_remotion_args(tmp_path):
    args = render_module.build_remotion_args(
        "node", "entry/remotion-cli.js", component_id="Clip",
        props_path="a.json", output_path="out.mp4", crf=20,
    )
    assert "render" in args
    assert "Clip" in args
    assert "--props" in args and "a.json" in args
    assert "--codec" in args and "h264" in args
    assert "--audio-codec" in args and "aac" in args


# --- smoke real: FFmpeg + libass --------------------------------------------


@_ffmpeg_skip
def test_render_libass_produce_un_clip_valido(portrait_video, tmp_path, config):
    plan = _plan(portrait_video, clip_id="cand_001", renderer="ffmpeg_libass")
    result = render_module.render_plan(
        plan, config, transcript=_transcript(), output_dir=tmp_path / "renders"
    )
    assert result.output_path == str(tmp_path / "renders" / "cand_001.mp4")
    assert result.reused is False
    assert result.renderer == "ffmpeg_libass"
    assert result.subtitle_count > 0

    out = Path(result.output_path)
    assert out.is_file()
    aux = [Path(p) for p in result.aux_paths]
    assert any(p.name == "cand_001.ass" and p.is_file() for p in aux)

    media = _ffprobe(out)
    assert abs(float(media["format"]["duration"]) - 3.0) < 0.4
    video = next(s for s in media["streams"] if s["codec_type"] == "video")
    audio = next(s for s in media["streams"] if s["codec_type"] == "audio")
    assert video["width"] == 1080 and video["height"] == 1920
    assert audio["codec_name"] == "aac"


@_ffmpeg_skip
def test_render_es_idempotente(portrait_video, tmp_path, config):
    plan = _plan(portrait_video, clip_id="cand_002")
    first = render_module.render_plan(plan, config, transcript=None, output_dir=tmp_path / "r")
    second = render_module.render_plan(plan, config, transcript=None, output_dir=tmp_path / "r")
    assert first.reused is False and second.reused is True
    assert first.sha256 == second.sha256


@_ffmpeg_skip
def test_render_sin_subtitulos_no_genera_ass(portrait_video, tmp_path, config):
    plan = _plan(portrait_video, clip_id="cand_003", renderer="none")
    result = render_module.render_plan(plan, config, transcript=_transcript(), output_dir=tmp_path / "r")
    assert result.subtitle_count == 0
    assert result.aux_paths == []


def test_fuente_inexistente_rebota(tmp_path, config):
    plan = _plan(tmp_path / "no-existe.mp4")
    with pytest.raises(render_module.RenderError) as exc:
        render_module.render_plan(plan, config, transcript=None, output_dir=tmp_path / "r")
    assert exc.value.code == "source_missing"


# --- camino Remotion: composicion, sin render real ---------------------------

class _FakeShutil:
    def __init__(self, node: str | None):
        self._node = node

    def which(self, name: str) -> str | None:
        return self._node if name == "node" else None


def _fake_remotion_env(
    monkeypatch, tmp_path: Path, *, node: str | None = "C:/fake/node/node.exe"
) -> tuple[Path, list]:
    """Directorio de Remotion falso con el entry del CLI creado de verdad.

    El entry tiene que existir EN DISCO (``entry.is_file()`` lo comprueba); lo
    unico que se simula es que no se ejecuta ningun proceso: `_run` se sustituye
    por un captor y `shutil.which("node")` por un stub.
    """
    monkeypatch.setattr(render_module, "shutil", _FakeShutil(node))
    calls: list = []

    def fake_run(argv: list[str], *, cwd: Path, timeout=None) -> None:
        calls.append((argv, cwd))
        # El comando real deja el fichero de salida y sin esto el sha256 final no
        # tendria que leer. La salida es el ULTIMO arg en ffmpeg y el que sigue al
        # id de composicion en remotion: se localizan por posicion, nunca por
        # sufijo, o el stub pisaria el video de entrada de la sesion.
        out = Path(argv[-1]) if argv[-1].endswith(".mp4") else Path(argv[4])
        # Guarda contra un argv mal interpretado: sin esto, un "-loglevel error"
        # reinterpretado como salida deja ficheros basura en la raiz del repo.
        assert out.suffix == ".mp4" and out.is_absolute(), f"stub: salida sospechosa {out}"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"render-simulado")

    monkeypatch.setattr(render_module, "_run", fake_run)

    remotion_dir = tmp_path / "remotion"
    entry = remotion_dir / "node_modules" / "@remotion" / "cli" / "remotion-cli.js"
    entry.parent.mkdir(parents=True)
    entry.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    return remotion_dir, calls


@_ffmpeg_skip
def test_render_remotion_compone_sobre_el_master(portrait_video, tmp_path, config, monkeypatch):
    remotion_dir, calls = _fake_remotion_env(monkeypatch, tmp_path)
    plan = _plan(portrait_video, clip_id="cand_004", renderer="remotion", video_codec="h264_nvenc")
    result = render_module.render_plan(
        plan, config, transcript=_transcript(), output_dir=tmp_path / "renders", remotion_dir=remotion_dir
    )

    assert len(calls) == 2

    master_argv, master_cwd = calls[0]
    assert str(tmp_path / "renders" / "cand_004_master.mp4") in master_argv
    assert master_cwd == tmp_path / "renders"
    assert "-c:v h264_nvenc" in " ".join(master_argv)

    props_path = tmp_path / "renders" / "cand_004.props.json"
    props = json.loads(props_path.read_text(encoding="utf-8"))
    assert len(props["subtitles"]) > 0
    assert props["videoSrc"] == "cand_004_master.mp4"

    remo_argv, remo_cwd = calls[1]
    assert "render" in remo_argv
    assert "-c:v" not in " ".join(remo_argv)
    assert str(props_path) in remo_argv
    # El master se sirve por HTTP con --public-dir: es lo que hace que
    # OffthreadVideo pueda leerlo en vez de recibir un file://.
    assert "--public-dir" in remo_argv
    assert str(tmp_path / "renders") in remo_argv
    assert remo_cwd == remotion_dir

    assert result.renderer == "remotion"
    assert result.aux_paths == [
        str(tmp_path / "renders" / "cand_004_master.mp4"),
        str(props_path),
    ]


def test_resolve_browser_executable_usa_la_config_si_existe(tmp_path):
    """Con render.browser_executable se usa ese; si no existe, None (no un
    path inventado: Remotion vuelve a su descarga y falla con su propio error)."""
    fake = tmp_path / "chrome.exe"
    fake.write_text("", encoding="utf-8")

    config = {"render": {"browser_executable": str(fake)}, "_project_root": str(tmp_path)}
    assert render_module.resolve_browser_executable(config) == fake

    missing = {"render": {"browser_executable": str(tmp_path / "nope.exe")}, "_project_root": str(tmp_path)}
    assert render_module.resolve_browser_executable(missing) is None

    # Sin config, la lista de candidatos del sistema. En Windows hay Chrome o
    # Edge; en un runner sin ninguno puede ser None, y ambos son validos.
    auto = render_module.resolve_browser_executable({})
    assert auto is None or str(auto) in render_module._BROWSER_CANDIDATES


def test_render_remotion_rechaza_contenedor_no_mp4(portrait_video, tmp_path, config, monkeypatch):
    plan = _plan(portrait_video, clip_id="cand_005", renderer="remotion", container="webm")
    with pytest.raises(render_module.RenderError) as exc:
        render_module.render_plan(
            plan, config, transcript=None, output_dir=tmp_path / "r", remotion_dir=tmp_path / "remotion"
        )
    assert exc.value.code == "unsupported_container"


def test_render_remotion_avisa_sin_node(portrait_video, tmp_path, config, monkeypatch):
    _fake_remotion_env(monkeypatch, tmp_path, node=None)
    plan = _plan(portrait_video, clip_id="cand_006", renderer="remotion")
    with pytest.raises(render_module.RenderError) as exc:
        render_module.render_plan(
            plan, config, transcript=None, output_dir=tmp_path / "r", remotion_dir=tmp_path / "remotion"
        )
    assert exc.value.code == "node_missing"