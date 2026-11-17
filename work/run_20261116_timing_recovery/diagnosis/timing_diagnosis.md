# Diagnóstico de precisión temporal — recuperación `rec_0001`–`rec_0006`

- **Fecha:** 2026-11-16 (UTC 2026-11-17 para la ejecución del prototipo)
- **Run:** `work/run_20261116_timing_recovery/`
- **Alcance:** explicar por qué los cortes no empiezan en el segundo solicitado, con
  evidencia reproducible, y proponer una corrección **sin** cambiar el diseño visual
  ni los tiempos editoriales.
- **Estado:** causa raíz **confirmada**; corrección **demostrada en prototipo aislado**;
  el código compartido **no se ha modificado**.

---

## 1. Resumen ejecutivo

El corte del clip se hacía con `-ss` (entrada) **antes** de `-i` y con `-c copy`.
En un MP4 con GOP largos, un corte por copia solo puede empezar en un **keyframe**,
así que FFmpeg retrocede al keyframe anterior al segundo pedido. Además,
`-avoid_negative_ts make_zero` reajusta el origen al **paquete más temprano** del
contenedor resultante (habitualmente un paquete de audio, 16–18 ms antes del
keyframe de video). El resultado es doble:

1. **Desfase grande** (`0.56 s` a `2.98 s`): el salto al keyframe anterior.
2. **Desfase pequeño** (`~16 ms`): el reajuste a cero con origen en audio.

La duración también se estiraba, porque el tramo copiado arranca antes y conserva
su `-t`, dejando el final desplazado respecto al pedido.

La corrección consiste en **decodificar desde un keyframe anterior y recortar con
`-ss` de salida** (recodificando video), conservando el audio original con
`-c:a copy`. Medido en prototipo: el clip empieza en `73.003537 s` para un pedido
de `73.000000 s` (Δ **+3.5 ms**, menos de 1/8 de frame).

---

## 2. Síntomas observados en el run anterior (`2026-11-15`)

| Candidato | Pedido (s) | Inicio medido (s) | Δ (s) | Keyframe previo (s) | Salto (s) |
|---|---|---|---|---|---|
| `rec_0002` | 73.000 | 72.423039 | **−0.576961** | 72.439033 | 0.560967 |
| `rec_0003` | 153.000 | 150.000907 | **−2.999093** | 150.016533 | 2.983467 |
| `rec_0004` | 259.000 | 257.439637 | **−1.560363** | 257.457200 | 1.542800 |
| `rec_0005` | 678.060 | 675.422041 | **−2.637959** | 675.441433 | 2.618567 |
| `rec_0001` | 0.000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 (caso trivial) |

Los cuatro fallos de `start_matches_cut` venían de aquí. `rec_0001` pasaba porque
su inicio es `0.0`, que ya es un keyframe.

Fuente de los valores: `diagnosis/timing_evidence.json` (medido con NCC de audio
mono 44.1 kHz contra la fuente, `services/delivery/pipeline.py::measure_alignment`).

---

## 3. Reproducción exacta

### 3.1 Comando que producía el corte (incorrecto)

`services/rendering/renderer.py`, líneas **133–153** (`render_clip`):

```text
ffmpeg -y -ss <start> -i <fuente> -t <duracion> \
  -map 0:v:0 -map 0:a:0? \
  -c copy -avoid_negative_ts make_zero -movflags +faststart <salida>
```

Ejemplo real (`rec_0002`):

```text
ffmpeg -y -ss 73.000000 \
  -i "input/Escape 100 Cops, Win $500,000 (2160p_30fps_AV1-128kbit_AAC-inglés).mp4" \
  -t 61.000000 -map 0:v:0 -map 0:a:0? -c copy \
  -avoid_negative_ts make_zero -movflags +faststart <salida>
```

### 3.2 Comando corregido (prototipo)

`work/run_20261116_timing_recovery/prototype/accurate_cut.py`
(`accurate_cut_command`):

```text
ffmpeg -hide_banner -y \
  -ss <start - pre_roll> \
  -i <fuente> \
  -ss <pre_roll> \
  -t <fin - inicio> \
  -map 0:v:0 -map 0:a:0? \
  -c:v libx264 -preset veryfast -crf 14 -pix_fmt yuv420p \
  -c:a copy -movflags +faststart <salida>
```

Ejemplo real (`rec_0002`, `pre_roll = 10 s`):

```text
ffmpeg -hide_banner -y -ss 63.000000 \
  -i "input/Escape 100 Cops, Win $500,000 (2160p_30fps_AV1-128kbit_AAC-inglés).mp4" \
  -ss 10.000000 -t 61.000000 \
  -map 0:v:0 -map 0:a:0? \
  -c:v libx264 -preset veryfast -crf 14 -pix_fmt yuv420p \
  -c:a copy -movflags +faststart \
  work/run_20261116_timing_recovery/prototype/cut_test/rec_0002_A_audio_copy.mp4
```

**Por qué funciona**

1. `-ss 63` antes de `-i` es un *seek* rápido: no importa la exactitud aquí, solo
   posicionarse en un keyframe seguro anterior al objetivo.
2. `-ss 10` **después** de `-i` descarta frames ya decodificados hasta el instante
   exacto `73.000`. Con salida recodificada, el primer frame de salida puede ser
   cualquiera, no solo un keyframe.
3. `-t 61` fija la duración editorial exacta desde ese punto.
4. `-c:a copy` evita una generación extra de AAC. El único desplazamiento residual
   es el origen del primer paquete de audio (~3 ms), muy por debajo del umbral
   perceptible y de la tolerancia de un frame (33.37 ms a 29.97 fps).

### 3.3 Comando de verificación de keyframes

```text
ffprobe -v error -select_streams v:0 -show_entries packet=pts_time,flags \
  -of json "input/Escape 100 Cops, Win $500,000 (2160p_30fps_AV1-128kbit_AAC-inglés).mp4"
```

Resultado: **36 813** paquetes de video, **672** keyframes
(`diagnosis/source_keyframes.json`).

---

## 4. Prueba a nivel de frame (no solo de audio)

Se extrajo el frame 0 del corte anterior de `rec_0002` y se comparó por SSIM con
la fuente en dos instantes candidatos (`diagnosis/frame_compare.py`):

| Comparación | SSIM |
|---|---|
| frame 0 del corte vs. fuente en el keyframe `72.439033` | **1.000000** |
| frame 0 del corte vs. fuente en el instante pedido `73.000000` | **0.300472** |

El frame inicial del clip es **exactamente** el frame del keyframe anterior, no el
del segundo solicitado. Esto descarta que el desfase sea un artefacto de medición
de audio: el video mismo empieza en otro sitio.

Mismo control sobre el corte corregido (`prototype/cut_test/cut_test_results.json`):

| Comparación (corte corregido) | SSIM |
|---|---|
| frame 0 vs. fuente en `73.006270` (frame real más cercano) | **0.991508** |
| frame 0 vs. fuente en el keyframe `72.439033` | 0.302908 |

Es decir: el corte corregido **sí** cae en el instante pedido.

---

## 5. Causa raíz confirmada

**Confirmado:** el corte usa `-ss` de entrada + `-c copy`, lo que obliga a empezar
en keyframe; `-avoid_negative_ts make_zero` reajusta el origen al paquete más
temprano (audio), añadiendo el desfase de ~16 ms.

Cadena causal, con referencias:

| # | Hecho | Referencia |
|---|---|---|
| 1 | `render_clip` coloca `-ss <start>` antes de `-i` y usa `-c copy` | `services/rendering/renderer.py:133-153` |
| 2 | `-avoid_negative_ts make_zero` reajusta el origen del contenedor | `services/rendering/renderer.py:148-149` |
| 3 | Un corte por copia solo puede comenzar en keyframe (restricción del formato, no del código) | evidencia §3.3 y §4 |
| 4 | El primer paquete de video del corte anterior tiene `pts_time` > 0 y su origen global quedó en audio | `diagnosis/timing_evidence.json` → `previous_cut.first_video_packet` |
| 5 | `measure_alignment` mide el instante 0 del clip contra la fuente y expone el desfase | `services/delivery/pipeline.py:306-361` |
| 6 | `start_matches_cut` compara ese valor con el inicio del corte y falla | `services/delivery/pipeline.py:712-735` (`container_checks`) |
| 7 | El manifiesto declaraba un método que ya no describe lo que hace el código | `services/delivery/manifest.py:161` |

### 5.1 Por qué el desfase medido es ~16 ms mayor que el salto al keyframe

En `rec_0002`: keyframe `72.439033` vs. inicio medido `72.423039` → diferencia
`0.015994 s`. Ese resto no es error aleatorio: es la diferencia entre el `pts` del
primer paquete de audio y el del keyframe de video, tras `make_zero`. Aparece en
los cuatro fallos con magnitud similar (15–19 ms).

---

## 6. Hipótesis descartadas

| Hipótesis | Veredicto | Evidencia |
|---|---|---|
| El error está en `measure_alignment` (mide mal) | **Descartada** | SSIM = 1.0 entre frame 0 del corte y el keyframe de la fuente (§4). El desfase es real, no de medición. |
| Los tiempos editoriales (`start_seconds`) están mal capturados | **Descartada** | Los cuatro valores solicitados son los del `candidates_recovered.json` original; el corte anterior empieza en el keyframe anterior, no en un tiempo distinto "parecido". |
| El problema es la tasa de frames / redondeo a frame | **Descartada como causa principal** | Los saltos son de 0.56–2.98 s, cientos de veces un frame (33 ms). El redondeo a frame explica como máximo ±1 frame. |
| El problema es el audio (AAC priming / encoder delay) | **Parcial, secundaria** | Explica los ~16 ms residuales, no los 0.56–2.98 s. Con `-c:a copy` el residual baja a +3.5 ms. |
| El problema es la fuente AV1 / 2160p | **Descartada** | El corte por copia es independiente del códec; el GOP es la causa. La recodificación a 1080×1920 ya ocurría después. |
| `render_vertical` introduce el desfase | **Descartada** | El `vertical` del run anterior conserva duración y arranca en 0; el desfase ya existe en `*_source.mp4` (§2, `previous_cut`). |

---

## 7. Corrección propuesta para el código compartido

> **No aplicada.** Requiere autorización explícita (ver §7.3).
> Mientras no se autorice, los renders restantes usan el módulo aislado
> `prototype/accurate_cut.py` inyectado en tiempo de ejecución.

### 7.1 Cambio en `services/rendering/renderer.py::render_clip`

Sustituir el bloque de las líneas **130–153** por un corte híbrido:

```python
    duration = end - start
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Seek de entrada a un keyframe anterior (rapido) y recorte exacto con
    # -ss de salida. El video se recodifica porque un corte por copia solo
    # puede empezar en keyframe.
    pre_roll = max(0.0, min(PRE_ROLL_SECONDS, start))
    seek_in = start - pre_roll

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-y",
        "-ss",
        f"{seek_in:.6f}",
        "-i",
        str(source_path),
        "-ss",
        f"{pre_roll:.6f}",
        "-t",
        f"{duration:.6f}",
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "14",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
```

Y añadir en el `return` los campos que hoy no existen:

```python
        "cut_method": "frame_accurate_hybrid_decode_trim_reencode_video_audio_copy",
        "pre_roll_seconds": pre_roll,
        "output_seek_seconds": pre_roll,
        "ffmpeg_command": cmd,
```

Constante nueva junto a `DEFAULT_TIMEOUT`:

```python
PRE_ROLL_SECONDS = 10.0
```

### 7.2 Cambio en `services/delivery/manifest.py:161`

```python
                "method": "FFmpeg -ss/-t -c copy (corte en keyframe)",
```

→

```python
                "method": (
                    "FFmpeg seek a keyframe previo + -ss de salida "
                    "(recodificacion de video, audio copiado)"
                ),
```

Sin este cambio, el manifiesto seguiría declarando un método obsoleto aunque el
corte sea exacto (es lo único pendiente en el manifiesto del prototipo).

### 7.3 Riesgos

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Recodificación de video (antes era copia) | Más tiempo de CPU y una generación extra de video antes de `render_vertical` | `-crf 14` + `veryfast`; medido ~4 min 19 s para 61 s de clip. Con `crf 14` la pérdida es despreciable frente a la recodificación posterior a 1080×1920. |
| Cambio de comportamiento global del pipeline | Afecta a cualquier job futuro | Es exactamente lo deseado (precisión), pero por eso requiere autorización y pruebas. |
| `-c:a copy` deja +3.5 ms | Residual imperceptible | Documentado; alternativa B (`-c:a aac 192k`) da 73.000 exacto a cambio de una generación extra de audio. |
| `pre_roll` fijo de 10 s | Si un GOP midiera >10 s, el seek de entrada podría no garantizar keyframe | En esta fuente el GOP máximo observado es 3.7 s; añadir una prueba que verifique que el primer `-ss` cae antes de un keyframe si se quiere generalizar. |
| `start = 0` | `pre_roll = 0`, igual que hoy | Caso `rec_0001` cubierto por la prueba. |

### 7.4 Pruebas propuestas (a añadir antes de adoptar)

1. `start_matches_cut` con Δ ≤ 0.034 s (1 frame) para un candidato cuyo inicio no
   es keyframe, con fixture de GOP conocido.
2. `start = 0` sigue funcionando y produce corte por copia-equivalente.
3. Duración de salida = duración pedida ± 1 frame.
4. El manifiesto declara `cut_method` y `ffmpeg_command`, y `method` describe el
   corte real.
5. Nombres con espacios/acentos (la fuente real ya los tiene: el comando se pasa
   como lista de argumentos, no por shell).

### 7.5 Alternativa aislada ya demostrada (sin tocar código compartido)

`prototype/accurate_cut.py` + `prototype/run_prototype.py` inyectan
`render_clip_frame_accurate` sobre `services.delivery.pipeline.render_clip` en
tiempo de ejecución. El prototipo completó las cinco etapas del pipeline real
(render → validate_mp4 → seo → validate_seo → manifest) sobre `rec_0002`, con
`actual_start = 73.003537`. **Ningún archivo de `services/` fue modificado.**

---

## 8. Candidato `rec_0006`: conflicto real de contenido (no de tiempos)

`rec_0006` (1097.26–1175.82 s, 78.56 s) se solapa con rangos ya protegidos:

| Clip protegido | Rango protegido (s) | Solape (s) | % del rango protegido | % de `rec_0006` |
|---|---|---|---|---|
| `clip_0002` | 1099.488073–1131.353240 | 31.865167 | **100.00 %** | 40.56 % |
| `clip_0004` | 1131.485170–1176.206168 | 44.334830 | **99.14 %** | 56.43 % |
| **Unión** | 1099.488073–1176.206168 | **76.2 s** | — | **97.16 %** |

Similitud de texto con los segmentos protegidos: Jaccard de palabras **1.0**,
`SequenceMatcher` **0.7254** (`diagnosis/candidate6_overlap.json`).

Conclusión: no es una mala interpretación de timestamps; es **reutilización real
del mismo contenido**. Renderizarlo exigiría cambiar `PROTECTED_RANGES`
(`services/delivery/baselines.py:39-45`) o añadir una excepción explícita, ambas
cosas sobre código/configuración protegida. **No se hace sin decisión humana.**
Opciones para el usuario:

1. Excepción documentada y autorizada para `rec_0006` (cambio en `baselines.py`).
2. Descartarlo por duplicado (compilación de material ya entregado).
3. Re-elegir otro tramo de la fuente para el sexto Short.

---

## 9. Artefactos y reproducción

| Artefacto | Contenido |
|---|---|
| `diagnosis/timing_evidence.json` | Keyframes, cortes previos, desfases por candidato |
| `diagnosis/source_keyframes.json` | 36 813 paquetes, 672 keyframes |
| `diagnosis/frame_comparison.json` | SSIM frame 0 vs. keyframe vs. instante pedido |
| `diagnosis/frames/*.png` | Frames usados en la comparación |
| `diagnosis/candidate6_overlap.json` | Solapes y similitud de `rec_0006` |
| `prototype/cut_test/cut_test_results.json` | A/B de audio sobre `rec_0002` |
| `prototype/accurate_cut.py` | Implementación aislada de la corrección |
| `prototype/run_prototype.py` | Runner del pipeline completo con inyección runtime |
| `prototype/validation.json`, `visual_checks.json`, `control_checks.json` | Mediciones técnicas/audio/visuales |

Reproducir el diagnóstico:

```text
python work/run_20261116_timing_recovery/diagnosis/diagnose_timing.py
python work/run_20261116_timing_recovery/diagnosis/frame_compare.py
python work/run_20261116_timing_recovery/diagnosis/candidate6_overlap.py
python work/run_20261116_timing_recovery/prototype/accurate_cut_test.py
```

---

## 10. Limitaciones

- La precisión de `-ss` de salida se apoya en la decodificación desde el keyframe;
  si un GOP excediera el `pre_roll`, el resultado podría degradarse. En esta fuente
  el intervalo máximo de keyframe observado es 3.70 s, muy por debajo de 10 s.
- El residual de `-c:a copy` (+3.5 ms) es una propiedad del empaquetado AAC, no un
  error del corte. La alternativa B lo elimina a cambio de otra generación de audio.
- Este diagnóstico no declara validación visual humana: los frames y la hoja de
  contactos quedan para revisión de la persona (ver `FINAL_REPORT.md`).
