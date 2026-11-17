# Informe final — recuperación temporal de los seis Shorts (`rec_0001`–`rec_0006`)

- **Run:** `work/run_20261116_timing_recovery/`
- **Fecha:** 2026-11-16 / ejecución UTC 2026-11-17
- **Ámbito:** corregir el fallo de precisión temporal de los cortes, resolver el
  candidato `rec_0006`, validar y detenerse. Sin publicación, sin aprobación
  automática, sin cambios de diseño y sin modificar el código compartido.
- **Resultado:** causa raíz corregida y **demostrada en un prototipo aislado**;
  `rec_0006` es un **conflicto real de contenido** que requiere decisión humana;
  el render masivo **no se ejecutó** (bloqueado por ambos puntos).

---

## 1. Resumen ejecutivo

| Pregunta | Respuesta |
|---|---|
| ¿Se encontró la causa de los 4 fallos `start_matches_cut`? | **Sí.** Corte por copia con `-ss` de entrada: obliga a empezar en keyframe y `make_zero` reajusta el origen al audio. |
| ¿Está corregida? | **Sí, en prototipo aislado.** `actual_start = 73.003537` para un pedido de `73.000000` (Δ **+3.5 ms**). |
| ¿Se aplicó al código compartido? | **No.** Requiere autorización (`renderer.py` + `manifest.py`). |
| ¿Se puede renderizar `rec_0006`? | **No tal cual.** 97.16 % de su contenido ya está protegido por `clip_0002`/`clip_0004`. |
| ¿Se renderizaron los seis clips? | **No.** Solo el prototipo de `rec_0002` en directorio aislado. |
| ¿Hay algo publicado o aprobado? | **No.** Todo queda en `pending_user_review`, `human_review.approved = false`. |
| ¿Se tocaron originales, transcripción, backups o entregables previos? | **No.** 10/10 protegidos con hash intacto en `output/`. |

Decisión de integridad aplicada: **antes que entregar seis renders, detenerse con
evidencia**. El sexto candidato no es un problema de tiempos sino de contenido
duplicado, y la corrección temporal todavía no está autorizada para producción.

---

## 2. Diagnóstico (fase 1)

Detalle completo en `diagnosis/timing_diagnosis.md`. Resumen:

- **Causa raíz confirmada:** `services/rendering/renderer.py::render_clip`
  (líneas 133–153) usa `-ss` antes de `-i` con `-c copy` y
  `-avoid_negative_ts make_zero`.
  - `-c copy` solo puede empezar en keyframe → salto atrás de 0.56–2.98 s.
  - `make_zero` reajusta el origen al paquete más temprano (audio) → ~16 ms extra.
- **Prueba de frame:** el frame 0 del corte anterior tiene SSIM **1.000000** con el
  keyframe `72.439033` y **0.300472** con el instante pedido `73.000000`.
- **Descartes:** el medidor de alineación no es el culpable; los tiempos
  editoriales están bien; el desfase no es redondeo de frame ni el códec AV1; el
  desfase ya existe en el `*_source.mp4`, antes de `render_vertical`.

Evidencia: `diagnosis/timing_evidence.json`, `source_keyframes.json`,
`frame_comparison.json`, `frames/`.

---

## 3. Corrección temporal y prototipo (fases 2–5)

### 3.1 Corrección

Corte híbrido: seek de entrada a un keyframe anterior, **`-ss` de salida** para
recortar exacto, recodificación de video (`libx264`, `crf 14`, `yuv420p`,
`veryfast`) y **audio copiado** (`-c:a copy`).

```text
ffmpeg -hide_banner -y -ss 63.000000 -i <fuente> -ss 10.000000 -t 61.000000 \
  -map 0:v:0 -map 0:a:0? \
  -c:v libx264 -preset veryfast -crf 14 -pix_fmt yuv420p \
  -c:a copy -movflags +faststart <salida>
```

### 3.2 Elección de audio A/B (medida)

| Variante | Inicio medido (s) | NCC global | Coste |
|---|---|---|---|
| **A — `-c:a copy`** (elegida) | **73.003537** | 0.99992915 | 0 generaciones extra de audio; conserva el AAC original |
| B — `-c:a aac 192k` | 73.000000 | 0.99835358 | una recodificación adicional de audio |

Δ de A = +3.5 ms (≈ 1/9 de frame a 29.97 fps). Se elige A por fidelidad de audio y
menor procesamiento; el residual es inaudible y está muy dentro de tolerancia.

### 3.3 Prototipo aislado (fase 5)

- Job: `work/run_20261116_timing_recovery/prototype/jobs/clip_0002_timingfix_20261116.json`
- Runner: `prototype/run_prototype.py` (monkeypatch en runtime de
  `services.delivery.pipeline.render_clip`; **ningún archivo de `services/` editado**)
- Módulo de corte: `prototype/accurate_cut.py`
- Salida: `prototype/output/clip_0002_timingfix_20261116.{mp4,seo.md,manifest.json}`
- Directorio de trabajo: `prototype/work/` (aislado; exige `--resume` para reusarse)

Las **cinco etapas** del pipeline real terminaron en `succeeded`:

| Etapa | Estado | Inicio (UTC) | Fin (UTC) |
|---|---|---|---|
| render | succeeded | 02:20:06 | 02:24:25 |
| validate_mp4 | succeeded | 02:24:25 | 02:24:30 |
| seo | succeeded | 02:24:59 | 02:24:59 |
| validate_seo | succeeded | 02:24:59 | 02:24:59 |
| manifest | succeeded | 02:24:59 | 02:25:00 |

Nota: el primer intento falló en `validate_seo` porque los apóstrofos de
`group's` / `line's` en la descripción se interpretaban como comillas y disparaban
la comprobación de literalidad. Es un problema de **texto del job**, no del clip;
se reformuló la descripción sin cambiar ninguna afirmación (misma clase de ajuste
que el "43" de `clip_0001`) y se reanudó desde `seo`. **No se relajó ningún
validador.**

---

## 4. Candidato `rec_0006` (bloqueo real)

`rec_0006` = 1097.26–1175.82 s (78.56 s).

| Clip protegido | Rango protegido (s) | Solape | % del protegido | % de `rec_0006` |
|---|---|---|---|---|
| `clip_0002` | 1099.488073–1131.353240 | 31.865167 s | **100.00 %** | 40.56 % |
| `clip_0004` | 1131.485170–1176.206168 | 44.334830 s | **99.14 %** | 56.43 % |
| **Unión** | 1099.488073–1176.206168 | **76.2 s** | — | **97.16 %** |

Texto: Jaccard de palabras **1.0**, `SequenceMatcher` **0.7254**
(`diagnosis/candidate6_overlap.json`).

No es un error de interpretación temporal: es el **mismo contenido**. Renderizarlo
requiere tocar `PROTECTED_RANGES` (`services/delivery/baselines.py:39-45`) o crear
una excepción; ambas son decisiones humanas y de configuración protegida.

**Opciones (el usuario decide):**

1. **Excepción autorizada** para `rec_0006` (cambio explícito en `baselines.py`,
   con justificación y prueba de que no debilita la protección de los demás).
2. **Descartar `rec_0006`** por duplicado: los clips 2 y 4 ya entregan ese material.
3. **Re-elegir otro tramo** de la fuente para el sexto Short (nuevo análisis
   editorial, no un cambio de timestamp del mismo contenido).

---

## 5. Entregables producidos (fase 6, alcance real)

Solo existe **un** entregable nuevo, el prototipo, en directorio aislado:

| Archivo | Bytes | SHA-256 |
|---|---|---|
| `prototype/output/clip_0002_timingfix_20261116.mp4` | 47 985 523 | `EC2A6F6AE8BA551E9F338FBAFE8C9751E52A3FA5ABCDCDD0F286FFF24A2AAC63` |
| `prototype/output/clip_0002_timingfix_20261116.seo.md` | 3 490 | `5B43100AF81F14E93DB44BED35FE26E676EA9CF0FC1D618358F7D340A5C277E8` |
| `prototype/output/clip_0002_timingfix_20261116.manifest.json` | 43 213 | `0D5E971EB8EEF48DC2DB1EDC22DC7046AB6D1ED0AD502893678B07E39C6C724E` |

- Nombre nuevo, directorio nuevo, sin reutilizar ningún MP4 previo.
- `status = delivered_pending_human_review`, `review_status = pending_user_review`,
  `human_review.approved = false`.
- Sin archivos `.partial` residuales.
- El manifiesto **conserva** la cadena antigua `"method": "FFmpeg -ss/-t -c copy
  (corte en keyframe)"` en `provenance.source_cut.method` (deuda conocida: se
  corrige con el parche de `manifest.py:161`). Los campos correctos
  `cut_method`, `pre_roll_seconds`, `output_seek_seconds` y `ffmpeg_command` sí
  están presentes y describen el corte real.

**No se renderizaron** `rec_0003`, `rec_0004`, `rec_0005` ni `rec_0006`.

---

## 6. Validación separada (fase 7)

> La validación **visual humana no se declara**. Las comprobaciones automáticas
> miden geometría y píxeles; no sustituyen la revisión de una persona. Frames y
> hoja de contactos quedan disponibles para ello.

### 6.1 Técnica (`prototype/validation.json`, `state.json`)

| Comprobación | Resultado |
|---|---|
| 22 checks de contenedor (`container_checks`) | **22/22 true** |
| `start_matches_cut` | true (73.003537 vs 73.0) |
| `end_matches_request`, `duration_matches_expected` | true; 61.027633 s |
| `alignment_confident` | true; NCC global **0.99992915** |
| Alineación final del publicado | NCC **0.99941015**, derivación 0.0 s |
| Δ de duración A/V | 0.027633 s (1 frame) |
| Integridad de decodificación | returncode 0, sin líneas de error |
| Video | H.264 High, yuv420p, 1080×1920, 30000/1001, 1829 frames |
| Audio | AAC LC, 44100 Hz, estéreo, 323 423 bps |
| `blackdetect` (≥0.10 s) | sin negros |
| `freezedetect` (≥1.0 s) | sin congelados |
| Oscuridad al final (59–60.5 s) | proviene de la fuente vertical (contenido), no es artefacto |

Los 22 checks cubren también `no_protected_overlap` y `has_frames`.

### 6.2 Audición / audio (medido, no "escuchado")

- La diferencia entre el clip final y un control **sin clic** codificado con el
  mismo encoder es **exactamente 0** fuera de 6.012–6.057 s; dentro, RMS 0.045 y
  pico 0.234 (coherente con el clic sintetizado: máx −10.5 dB, ganancia −2 dB).
  Desfase 0 muestras.
- No hay clipping nuevo: el final tiene **98** muestras ≥ 1.0 frente a **1455** del
  compuesto previo al clic. Los entregables aprobados también reportan máximo
  0.0 dB.
- Sin limiter, sin ducking, sin ganancia global (`audio_mix.no_limiter/no_ducking/
  no_global_gain = true`).

### 6.3 Visual / geometría (medido, no "visto")

| Elemento | Medición | Referencia de diseño |
|---|---|---|
| Tarjeta (alfa) | bbox x 78–1002, y 128–432; centro_x **540.0** | 932×312 centrada, `base_x=100`, `base_y=150`, `glow=26` |
| Animación de entrada | t=6.1 → y 204 (alfa 90); t=6.3 → y 138; t=6.52 → y 128 (alfa 255) | 150 px de movimiento, fades 300/320 ms |
| Salida | t=10.3 → y 68, alfa 87; t=10.5 → ausente | entra 6.0, sale 10.42 |
| Subtítulos | bbox x 187–893, y 1251–1580; centro_x **540.0**; margen inferior **340 px** | PlayRes 1080×1920, MarginV 340, Arial Black 96, outline 5 |
| Marco neón | bbox x 3–1075, y 14–1905 | anillo perimetral (MARGIN 18 + halo 7) |
| Overlays vs. control | los píxeles cambiados (>60 suma RGB) quedan en el anillo de 40 px + banda de subtítulos; píxeles en la zona media 0–109 | sin alteraciones en el centro |
| Copia final vs. `overlaid` | `max_abs_delta = 0` | el paso de audio no altera píxeles |
| Solape tarjeta/subtítulos | ninguno | tarjeta 128–432 vs. subtítulos 1251–1580 |

Material para revisión humana: `prototype/frames/contact_sheet.png` y
`prototype/output/clip_0002_timingfix_20261116.mp4` (mostrados en el panel Review).

### 6.4 SEO (`prototype/output/*.seo.md`, `state.json`)

- `validate_seo`: **41 checks, todos true**, `valid = true`.
- Sin promesas de resultado, sin cifras sin respaldo, sin citas inventadas.
- Sin cambios de idioma, etiquetas ni afirmaciones editoriales.

---

## 7. Integridad y no destructividad

| Garantía | Verificación |
|---|---|
| Originales intactos | solo lectura sobre `input/` |
| Transcripción/proyectos intactos | solo lectura |
| 10 archivos protegidos de `output/` | `check_protected` → **10/10 ok** antes y después |
| `clip_0001_recovered_20261115.*` | sin cambios |
| Backups del run anterior | no modificados |
| `PROTECTED_RANGES` / `PROTECTED_SHA256` | no modificados |
| Código compartido (`services/`) | no modificado (inyección en runtime) |
| Configuración de estilos/exportación | no modificada |
| Nada publicado / aprobado / programado | manifiesto `approved = false` |
| Repositorio git | `main` en `988f3c4`; el trabajo sigue sin commits |

`output/` contiene 13 archivos: los 10 protegidos + los 3 de
`clip_0001_recovered_20261115` (MP4, SEO, manifiesto). Ninguno fue tocado.

---

## 8. Estado final, decisiones pendientes y siguiente paso

### 8.1 Estado

- **Diagnóstico:** completo y con prueba de frame.
- **Corrección temporal:** probada de extremo a extremo en prototipo aislado.
- **Candidato 6:** caracterizado; **bloqueado** por decisión humana.
- **Render masivo:** **no ejecutado**.
- **Validación visual humana:** **pendiente** (no se declara).

### 8.2 Decisiones que necesita el usuario

1. **`rec_0006`** — elegir una de las tres opciones de §4.
2. **Adopción de la corrección** — autorizar el parche de `renderer.py::render_clip`
   + `manifest.py:161` (propuesta exacta, riesgos y pruebas en
   `diagnosis/timing_diagnosis.md` §7), **o** aceptar explícitamente que los
   renders restantes usen el wrapper aislado de `prototype/accurate_cut.py`.
3. **Revisión visual** del prototipo `clip_0002_timingfix_20261116` (frames y MP4)
   antes de dar por válido el resultado visual.

### 8.3 Siguiente paso técnico (tras las decisiones)

1. Renderizar en secuencia, **uno a uno**, `rec_0003`, `rec_0004`, `rec_0005`
   (y `rec_0006` solo si se resuelve), con los tiempos originales de
   `candidates_recovered.json`, nombres nuevos y directorios aislados.
2. Ejecutar las cinco etapas por clip y validar técnica, visual/auditiva y SEO por
   separado.
3. Emitir un informe por clip y dejar todo en `pending_user_review`.

### 8.4 Riesgos y limitaciones abiertos

- La recodificación de video añade tiempo de CPU (~4 min 19 s por 61 s de clip).
- El residual de audio con `-c:a copy` es +3.5 ms (inaudible, documentado).
- El manifiesto del prototipo conserva la cadena de método antigua hasta que se
  autorice el cambio de `manifest.py`.
- `pre_roll` fijo de 10 s es seguro para esta fuente (GOP máximo 3.70 s); si se
  generaliza el pipeline, conviene una prueba que lo garantice.
- La calidad visual final (estética, legibilidad, naturalidad del corte) requiere
  ojo humano: este informe solo aporta medidas.
