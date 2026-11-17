"""Pruebas de regresion del corte preciso (fallo historico `start_matches_cut`).

Cubren:

1. Los cuatro casos temporales del run 2026-11-15 (inicios 73, 153, 259, 678.06),
   que antes caian en el keyframe anterior.
2. Un inicio en 0 y un clip mas corto que el pre-roll.
3. Que `render_clip` usa el plan y publica su trazabilidad.
4. Un corte REAL sobre un video sintetico local con keyframes cada 2 s y un
   inicio que NO es keyframe (3.7 s). El frame 0 debe coincidir con el frame
   3.7 de la fuente, no con el keyframe 2.0.
5. Nombres de archivo con espacios y acentos.

Las pruebas reales omiten si FFmpeg/FFprobe no estan disponibles. No contactan
redes, no leen `output/` y no modifican videos originales.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.rendering import renderer  # noqa: E402
from services.rendering.renderer import (  # noqa: E402
    PRE_ROLL_SECONDS,
    build_cut_plan,
    render_clip,
)

SOURCE = Path("C:/videos/fuente con acentos áéí.mp4")
OUTPUT = Path("C:/work/clip salida ñ.mp4")

# (id, inicio, fin, seek de entrada esperado) del run fallido.
HISTORICAL_CASES = [
    ("rec_0002", 73.0, 134.0, 63.0),
    ("rec_0003", 153.0, 187.0, 143.0),
    ("rec_0004", 259.0, 284.0, 249.0),
    ("rec_0005", 678.06, 730.16, 668.06),
]


def _value_after(cmd, flag):
    return cmd[cmd.index(flag) + 1]


def _assert_precise_plan(test, plan, *, start, end, input_seek, pre_roll=10.0):
    cmd = plan["command"]
    test.assertEqual(cmd[0], renderer.DEFAULT_FFMPEG)
    test.assertEqual(_value_after(cmd, "-ss"), f"{input_seek:.6f}")
    test.assertEqual(cmd[cmd.index("-i") + 1], str(SOURCE))
    # El segundo -ss (salida) es el que recorta con precision.
    output_ss = cmd.index("-ss", cmd.index("-i"))
    test.assertEqual(cmd[output_ss + 1], f"{pre_roll:.6f}")
    test.assertEqual(_value_after(cmd, "-t"), f"{end - start:.6f}")
    test.assertEqual(cmd[-1], str(OUTPUT))
    # El error historico: -ss unico + -c copy + make_zero. Debe estar ausente.
    test.assertEqual(cmd.count("-ss"), 2)
    test.assertNotIn("-avoid_negative_ts", cmd)
    test.assertNotIn("make_zero", cmd)
    test.assertNotIn("-c", cmd)
    test.assertEqual(cmd[cmd.index("-c:v") + 1], "libx264")
    test.assertEqual(cmd[cmd.index("-c:a") + 1], "copy")
    test.assertEqual(plan["input_seek_seconds"], input_seek)
    test.assertEqual(plan["output_seek_seconds"], pre_roll)
    test.assertEqual(plan["pre_roll_seconds"], pre_roll)
    test.assertEqual(plan["requested_duration_seconds"], end - start)


class CutPlanTests(unittest.TestCase):
    def test_four_historical_non_keyframe_starts(self):
        for clip_id, start, end, input_seek in HISTORICAL_CASES:
            with self.subTest(clip=clip_id):
                plan = build_cut_plan(
                    ffmpeg=renderer.DEFAULT_FFMPEG,
                    source_path=SOURCE,
                    start_seconds=start,
                    end_seconds=end,
                    output_path=OUTPUT,
                )
                _assert_precise_plan(
                    self, plan, start=start, end=end, input_seek=input_seek
                )

    def test_start_zero_does_not_input_seek(self):
        plan = build_cut_plan(
            ffmpeg=renderer.DEFAULT_FFMPEG,
            source_path=SOURCE,
            start_seconds=0.0,
            end_seconds=43.0,
            output_path=OUTPUT,
        )
        _assert_precise_plan(
            self, plan, start=0.0, end=43.0, input_seek=0.0, pre_roll=0.0
        )

    def test_pre_roll_is_clamped_to_start(self):
        plan = build_cut_plan(
            ffmpeg=renderer.DEFAULT_FFMPEG,
            source_path=SOURCE,
            start_seconds=2.0,
            end_seconds=5.0,
            output_path=OUTPUT,
        )
        _assert_precise_plan(
            self, plan, start=2.0, end=5.0, input_seek=0.0, pre_roll=2.0
        )

    def test_render_clip_uses_plan_and_exposes_traceability(self):
        fake_probe = {
            "format": {"duration": 200.0, "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 3840,
                 "height": 2160},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "fuente con acento á.mp4"
            out = Path(tmp) / "salida ñ.mp4"
            src.write_bytes(b"fake source")
            out.write_bytes(b"fake output")
            with mock.patch.object(renderer, "probe", return_value=fake_probe), \
                 mock.patch.object(renderer, "_run") as run:
                run.return_value = mock.Mock(returncode=0, stderr=b"")
                meta = render_clip(
                    source_path=src, start_seconds=73.0, end_seconds=134.0,
                    output_path=out,
                )
            cmd = run.call_args.args[0]
            self.assertEqual(_value_after(cmd, "-ss"), "63.000000")
            self.assertEqual(meta["cut_method"],
                             "frame_accurate_hybrid_decode_trim_reencode_video_audio_copy")
            self.assertEqual(meta["pre_roll_seconds"], PRE_ROLL_SECONDS)
            self.assertEqual(meta["input_seek_seconds"], 63.0)
            self.assertEqual(meta["output_seek_seconds"], PRE_ROLL_SECONDS)
            self.assertEqual(meta["ffmpeg_command"], cmd)
            self.assertTrue(meta["has_audio"])


@unittest.skipUnless(
    shutil.which("ffmpeg") and shutil.which("ffprobe"),
    "FFmpeg/FFprobe no disponibles",
)
class RealCutTests(unittest.TestCase):
    TARGET = 3.7          # no es keyframe: los keyframes estan en 0, 2, 4, 6...
    KEYFRAME = 2.0
    DURATION = 8.0

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._tmp.name)
        cls.source = cls.dir / "video con espacios y acentos áéí.mp4"
        cmd = [
            "ffmpeg", "-hide_banner", "-v", "error", "-y",
            "-f", "lavfi", "-i",
            f"testsrc2=size=320x180:rate=30:duration={cls.DURATION}",
            "-f", "lavfi", "-i",
            f"sine=frequency=440:sample_rate=44100:duration={cls.DURATION}",
            "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(cls.source),
        ]
        subprocess.run(cmd, check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _frame(self, path, timestamp, tag):
        out = self.dir / f"{tag}.png"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-v", "error", "-y",
             "-ss", f"{timestamp:.3f}", "-i", str(path),
             "-frames:v", "1", str(out)],
            check=True, capture_output=True,
        )
        from PIL import Image
        import numpy as np
        return np.asarray(Image.open(out).convert("RGB")).astype(np.int16)

    def test_real_cut_starts_at_requested_frame_not_keyframe(self):
        out = self.dir / "corte real ñ.mp4"
        meta = render_clip(
            source_path=self.source,
            start_seconds=self.TARGET,
            end_seconds=self.TARGET + 3.0,
            output_path=out,
            timeout=600.0,
        )
        self.assertTrue(meta["has_audio"])
        self.assertLess(abs(meta["duration_delta_seconds"]), 0.1)

        cut_first = self._frame(out, 0.0, "cut_first")
        source_target = self._frame(self.source, self.TARGET, "source_target")
        source_keyframe = self._frame(self.source, self.KEYFRAME, "source_keyframe")

        import numpy as np
        delta_target = float(np.abs(cut_first - source_target).mean())
        delta_keyframe = float(np.abs(cut_first - source_keyframe).mean())
        self.assertLess(delta_target, 18.0)
        self.assertGreater(delta_keyframe, delta_target + 5.0)

    def test_real_cut_keeps_audio_and_frame_rate(self):
        out = self.dir / "corte audio ñ.mp4"
        meta = render_clip(
            source_path=self.source,
            start_seconds=self.TARGET,
            end_seconds=self.TARGET + 2.0,
            output_path=out,
            timeout=600.0,
        )
        self.assertEqual(meta["video_codec"], "h264")
        self.assertEqual(meta["audio_codec"], "aac")
        self.assertEqual((meta["width"], meta["height"]), (320, 180))


if __name__ == "__main__":
    unittest.main()
