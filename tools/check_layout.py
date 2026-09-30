"""Layout del overlay: renderiza las props REALES sobre un fondo plano.

Restar el render de su master no funciona con video real: entre el master
(CRF 16) y el final (CRF 20) el recompresion mueve pixeles de verdad por todo
el cuadro, y un 8% de los pixeles supera un diff de 40. Anyadir un umbral mas
alto no lo arregla porque se come el texto y porque el resultado depende del
contenido, no de la maquetacion.

La forma limpia es quitar el problema: si el fondo es un color plano, CUALQUIER
pixel que no sea ese color lo ha dibujado la composicion. Se mide entonces el
texto real, con su caja y su Times real, sin interferencia del video.

Usa las props del clip real (mismos cues, mismo hook, misma duracion), asi que
lo que se mide es el layout de produccion, no uno inventado.

Uso:
    python tools/check_layout.py <props.json> [carpeta]
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import config as config_module  # noqa: E402
from src.media import ffmpeg  # noqa: E402
from src.media import render as render_module  # noqa: E402

WIDTH, HEIGHT, FPS = 1080, 1920, 30
BACKGROUND_HEX = 0x404040
# Los fotogramas se leen en gris de 8 bits, asi que la referencia es el canal
# gris del color, no el entero RGB (0x404040 no cabe en 0-255).
BACKGROUND = 0x40
TOLERANCE = 18


def build_flat_source(dest: Path, seconds: float) -> None:
    """Video de un solo color con audio: cualquier otro pixel es de la UI."""
    ffmpeg.run(
        [
            ffmpeg.require("ffmpeg"), "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={BACKGROUND_HEX:#06x}:size={WIDTH}x{HEIGHT}:rate={FPS}:duration={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(dest),
        ]
    )


def gray_frame(video: Path, seconds: float) -> np.ndarray:
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "f.gray"
        ffmpeg.run(
            [
                ffmpeg.require("ffmpeg"), "-y", "-loglevel", "error",
                "-ss", f"{seconds:.3f}", "-i", str(video), "-frames:v", "1",
                "-vf", "format=gray", "-f", "rawvideo", "-pix_fmt", "gray", str(raw),
            ]
        )
        data = np.frombuffer(raw.read_bytes(), dtype=np.uint8)
    return data.reshape(HEIGHT, WIDTH).astype(np.int16)


def drawn(video: Path, seconds: float) -> np.ndarray:
    """Mascara de lo que la composicion escribio sobre el fondo."""
    frame = gray_frame(video, seconds)
    return np.abs(frame - BACKGROUND) > TOLERANCE


def bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    rows = np.where(mask.any(axis=1))[0]
    cols = np.where(mask.any(axis=0))[0]
    if rows.size == 0:
        return None
    return int(rows[0]), int(rows[-1]), int(cols[0]), int(cols[-1])


def report(mask: np.ndarray, label: str) -> bool:
    box = bbox(mask)
    if box is None:
        print(f"  {label:<14} NO SE DIBUJA NADA")
        return False
    y0, y1, x0, x1 = box
    top, bottom, left, right = y0, y1 + 1, x0, x1 + 1
    print(
        f"  {label:<14} y={top:>4}-{bottom:<4} x={left:>4}-{right:<4} "
        f"alto={bottom - top:>3}px  ancho={right - left:>4}px"
    )
    ok = True
    if left < 0 or right > WIDTH:
        print(f"    [FALLO] se sale por los lados (borde a {right} de {WIDTH})")
        ok = False
    if bottom > HEIGHT:
        print(f"    [FALLO] se sale por abajo ({bottom} > {HEIGHT})")
        ok = False
    if top < 0:
        print(f"    [FALLO] se sale por arriba ({top} < 0)")
        ok = False
    if bottom == HEIGHT:
        print(f"    [AVISO] toca el borde inferior")
    return ok


def main() -> int:
    props_path = Path(sys.argv[1])
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else None

    props = json.loads(props_path.read_text(encoding="utf-8"))
    seconds = float(props["durationInSeconds"])
    cues = props.get("subtitles") or []
    title = props.get("title")

    config = config_module.load()
    remotion_dir = config_module.resolve_path(config, "remotion_dir")

    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        source = out_dir / "flat.mp4"
        props_file = out_dir / "flat.props.json"
        output = out_dir / "flat.mp4.out.mp4"
    else:
        tmp = Path(tempfile.mkdtemp())
        source = tmp / "flat.mp4"
        props_file = tmp / "flat.props.json"
        output = tmp / "out.mp4"
        print(f"  (temporal en {tmp})")

    print(f"props  {props_path.name}")
    print(f"duracion {seconds:.3f}s  fps {props.get('fps')}  cues {len(cues)}  hook {title!r}\n")

    print(f"1/3 generando fondo plano {WIDTH}x{HEIGHT}...")
    build_flat_source(source, seconds)

    # El src va como nombre pelado: lo sirve --public-dir (mismo camino que el
    # render de produccion, asi que tambien se prueba el public-dir).
    flat_props = dict(props, videoSrc=source.name)
    props_file.write_text(json.dumps(flat_props, ensure_ascii=False, indent=2), encoding="utf-8")

    print("2/3 renderizando sobre el fondo plano...")
    argv = render_module.build_remotion_args(
        render_module.shutil.which("node") or "node",
        render_module.remotion_entry(remotion_dir),
        component_id="Clip",
        props_path=props_file,
        output_path=output,
        crf=20,
        public_dir=source.parent,
        browser_executable=render_module.resolve_browser_executable(config),
    )
    ffmpeg.run(argv, cwd=remotion_dir, timeout=3600)

    print("3/3 midiendo el overlay:\n")
    failures = 0

    if title:
        mask = drawn(output, 0.6)
        print("HOOK (t=0.6s)")
        if not report(mask, "titulo"):
            failures += 1
        last = drawn(output, seconds - 0.1)
        if last[:600].any():
            print("    [FALLO] el titulo sigue visible al final del clip")
            failures += 1
        else:
            print("    OK: el titulo se retira antes del final")

    # El hook y los subtitulos se miden por separado: comparten encuadre y el
    # titulo vive arriba mientras el texto va abajo. La linea de corte esta en
    # el medio, donde no dibuja ninguna de las dos capas.
    TITLE_BAND = slice(0, 900)
    SUBTITLE_BAND = slice(900, HEIGHT)

    if title:
        print("COBERTURA DEL HOOK (debe ser solo al principio):")
        MAX_TITLE_COVERAGE = 0.25
        leaks = 0
        for t in (0.6, 2.0, 5.0, seconds * 0.5, seconds * 0.75, seconds - 0.5):
            box = bbox(drawn(output, t)[TITLE_BAND])
            pct = 100 * t / seconds
            if box is None:
                print(f"  t={t:6.2f}s ({pct:5.1f}% del clip)  no dibujado")
            else:
                print(
                    f"  t={t:6.2f}s ({pct:5.1f}% del clip)  VISIBLE  "
                    f"y={box[0]}-{box[1] + 1}  x={box[2]}-{box[3] + 1}"
                )
                if t / seconds > MAX_TITLE_COVERAGE:
                    leaks += 1
        if leaks:
            print(
                f"  [FALLO] el titulo sigue en pantalla {leaks} veces despues del "
                f"{int(MAX_TITLE_COVERAGE * 100)}% del clip: es un rotulo, no un cartel"
            )
            failures += leaks
        else:
            print(f"  OK: el titulo se retira antes del {int(MAX_TITLE_COVERAGE * 100)}% del clip")

    worst = None
    print("\nCADA CUE (solo la banda de abajo, sin el titulo):")
    for i, cue in enumerate(cues):
        t = (cue["start"] + cue["end"]) / 2
        mask = drawn(output, t)
        box = bbox(mask[SUBTITLE_BAND])
        if box is None:
            print(f"  cue {i:>2} t={t:6.2f}  [FALLO] no se dibuja  {cue['text']!r}")
            failures += 1
            continue
        y0, y1, x0, x1 = box
        y0 += SUBTITLE_BAND.start
        y1 += SUBTITLE_BAND.start
        margin = HEIGHT - (y1 + 1)
        side = min(x0, WIDTH - (x1 + 1))
        status = "ok"
        if y1 + 1 > HEIGHT or x0 < 0 or x1 + 1 > WIDTH:
            status = "[FALLO] se sale"
            failures += 1
        elif side < 8:
            status = "[AVISO] al borde lateral"
        print(
            f"  cue {i:>2} t={t:6.2f}  y={y0:>4}-{y1 + 1:<4} x={x0:>4}-{x1 + 1:<4} "
            f"alto={y1 - y0 + 1:>3}px  margen_inf={margin:>3}px  {status}  {cue['text']!r}"
        )
        if worst is None or y1 > worst[0]:
            worst = (y1, i, cue, t, x0, x1)

    if worst:
        y1, i, cue, t, x0, x1 = worst
        print("\nCUE MAS BAJO (el que mas se acerca al borde):")
        print(f"  cue {i}  t={t:.2f}  {cue['text']!r}")
        report(drawn(output, t), "subtitulo")
        margin = HEIGHT - (y1 + 1)
        print(f"\n  margen inferior del texto mas bajo = {margin}px de {HEIGHT}")

    # Ancho de las dos lineas: es lo que decide si una linea se parte feo.
    if cues:
        t = (cues[0]["start"] + cues[0]["end"]) / 2
        box = bbox(drawn(output, t))
        if box:
            print(f"  ancho del subtitulo mas ancho medido = {box[3] - box[2] + 1}px de {WIDTH}")

    print(f"\n{'FALLOS: ' + str(failures) if failures else 'LAYOUT OK: nada se sale del encuadre'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
