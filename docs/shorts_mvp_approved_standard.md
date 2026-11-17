# Estándar aprobado de Shorts MVP

**Versión:** 1.0 — 2026-10-28
**Ámbito:** Shorts verticales 1080×1920 del proyecto Local Video Studio
**Base:** los cuatro entregables `output/clip_0001..0004_shorts_mvp_20261028.mp4`, sus manifiestos y las verificaciones de esta ejecución.
**Estado:** documento de referencia. No modifica el pipeline ni el código; describe el contrato que debe cumplir cada nueva ejecución.

> Regla de lectura: este documento separa lo que el usuario fijó como requisito, lo que se midió en los renders reales, lo que debe comprobarse en cada ejecución y lo que sigue sin confirmar. No se debe presentar una convención como requisito aprobado ni una validación automática como aprobación humana.

---

## 1. Entregables de referencia

| Clip | Archivo | Bytes | SHA-256 | Duración (contenedor) | Frames |
|---|---|---|---|---|---|
| Short 1 | `clip_0001_shorts_mvp_20261028.mp4` | 42.790.148 | `EBBD2270DAC8A1D06FF82AB16F35F8E42E58BB9A1EEA14B32FFB05F2191D95DC` | 58,158100 s | 1743 |
| Short 2 | `clip_0002_shorts_mvp_20261028.mp4` | 19.794.327 | `8A45FFA4983E0705F2BDC9D761A35CE7339FFCBF8CF83CE58E0A20C85B09B311` | 31,865167 s | 955 |
| Short 3 | `clip_0003_shorts_mvp_20261028.mp4` | 32.355.578 | `089A99DEFE7CB2777FF470EFDD8FE15A68B8E5BC90B1874AD0D33547E4D8B723` | 48,581867 s | 1456 |
| Short 4 | `clip_0004_shorts_mvp_20261028.mp4` | 31.754.469 | `1362571FB103895D5548177B0429896CB5CC7A1AF3EC6F37AD0093C1458C2E22` | 44,720998 s | 1340 |

Hashes recalculados sobre los archivos reales el 2026-10-28; coinciden con los manifiestos y con los valores esperados.

Rangos de origen medidos (inicio real + duración del contenedor):

| Clip | Inicio real (s) | Fin real (s) | Referencia de origen |
|---|---|---|---|
| Short 1 | 526,791111 | 584,949211 | `clip_0001` |
| Short 2 | 1099,488073 | 1131,353240 | `clip_0002` |
| Short 3 | 780,770975 | 829,352842 | `clip_0003` |
| Short 4 | 1131,485170 | ~1176,206 | `clip_0004` |

Los números de archivo **no** indican orden cronológico: 1 → 3 → 2 → 4.

---

## 2. Requisitos fijados por el usuario

Estos puntos se consideran cerrados para nuevas ejecuciones; no se rediseñan sin una decisión explícita nueva.

1. **Formato vertical 1080×1920.**
2. **H.264 High, yuv420p, ~29,97 fps.**
3. **Audio AAC-LC estéreo a 44,1 kHz.**
4. **Fondo desenfocado con banda vertical central nítida.**
5. **Marco neón azul/cian segmentado, delgado, limitado al borde del video.**
6. **Subtítulos centrados en la zona inferior.**
7. **Énfasis contextuales legibles, centrados y sin tapar la acción.**
8. **Tarjetas CTA contextuales, centradas y con tiempos precisos.**
9. **No eliminar CTAs, énfasis ni elementos narrativos para simplificar el render.**
10. **No rediseñar los elementos ya aprobados.**
11. **Audio original intacto fuera de las ventanas de efectos, cuando el método lo permita.**
12. **Clic de referencia con +4 dB respecto al nivel base.**
13. **No elevar el pico del programa por encima de la mezcla de referencia.**
14. **Sin ducking ni atenuación por tramos.**
15. **Verificar y documentar la sincronización de cada clic con su tarjeta.**
16. **Cada Short nuevo queda en `pending_user_review` hasta revisión humana.**

---

## 3. Estándar visual

### 3.1 Contenedor y codificación (verificado en los 4 renders)

| Propiedad | Valor verificado | Tolerancia / comprobación |
|---|---|---|
| Resolución | 1080×1920 | exacta |
| Códec de video | H.264, perfil High | exacto |
| Pix fmt | `yuv420p` | exacto |
| Frame rate | `30000/1001` (29,97 fps) | exacto |
| Contenedor | MP4 (`mov,mp4,m4a,3gp,3g2,mj2`) | MP4 reproducible |
| Audio | AAC-LC, 44100 Hz, 2 canales | exacto |
| Bitrate de audio | 320 kb/s nominal | informativo |
| Duración de audio vs video | diferencia entre −15 ms y +10 ms según clip | documentar el valor real; no “corregir” con recortes silenciosos |

### 3.2 Composición

- Fondo: el mismo cuadro de origen escalado y desenfocado, sin recortes que oculten acción.
- Banda central: recorte vertical nítido del original, centrado.
- La transición fondo/banda no debe introducir marcos opacos ni tapar subtítulos.
- Verificación objetiva usada en el MVP: nitidez por Laplaciano en bandas (centro > fondo). Repetir la medición en cada clip.

### 3.3 Marco neón

- Azul/cian, segmentado, fino, **solo en el borde** del lienzo.
- No debe invadir la banda central ni la zona de subtítulos/tarjetas.
- Generador de referencia: `make_neon_frame_adj.py` con semilla `20261020`, copiado dentro de la carpeta de la ejecución (no se modifica el generador global).
- Comprobación: píxeles cian medidos en fotogramas de control + inspección de que no hay neón en el interior de la banda.

### 3.4 Subtítulos

- Fuente: `Arial Black 96`, blanco con contorno negro.
- Máximo 2 líneas, centrados horizontalmente.
- Margen inferior ASS: `340` (zona inferior, por encima de la UI de la plataforma).
- Se generan desde la transcripción propia del proyecto, sin inventar tiempos por palabra.
- Sin `word_timestamps`: el primer subtítulo puede incluir palabras anteriores al corte. **No recortar por palabra inventando tiempos**; documentarlo como limitación del clip.

### 3.5 Énfasis contextuales

- Fuente: `Arial Black 84`, centrado en `x = 540`.
- Posición de referencia: `y ≈ 1010`, dentro de la banda `y 950–1070`.
- Animación: pop de escala 45 % → 100 % → 103 %.
- Texto: palabra o frase **literal** de la transcripción del clip. No añadir cifras, nombres ni conclusiones.
- Ventanas de referencia: 2,0 s alrededor de la frase; sin solaparse con tarjetas.
- Comprobación: presencia, centrado (`center_x`), banda vertical, ausencia de colisión con tarjetas, OCR cuando esté disponible.

### 3.6 Tarjetas CTA

- Tamaño de referencia: `932×312` px; `x = 76`; centro `x = 540`.
- Animación: entrada → hold → salida; tiempos declarados en el manifiesto con precisión de milisegundos.
- Contenido contextual: título corto + subtítulo en español; si es paráfrasis, registrarlo como **adaptación editorial**, no como cita.
- El texto debe estar respaldado por un segmento concreto (`evidence`) de la transcripción.
- Comprobación: bbox alfa durante el hold, centro horizontal, primer y último fotograma visibles, coincidencia con el clic.

---

## 4. Estándar de audio

### 4.1 Mezcla

- Audio base: PCM del clip vertical, sin recorte, sin limiter y sin normalización global.
- Efectos: clic sintético local `ui_click_hf_v1`, 45 ms, mono/stereo según activo de la ejecución.
- Mezcla: `amix=inputs=N:duration=first:dropout_transition=0:normalize=0`.
- Fuera de las ventanas de clic, el PCM mezclado debe ser **bit-idéntico** al audio base (delta máximo 0,0). Si no se puede garantizar por el método, documentarlo.
- Prohibido: ducking, atenuación por tramos, limiter, ganancia global no declarada.
- La recodificación AAC puede introducir diferencias de nivel (~−0,4 dB medidos). Se documentan; no se compensan con ganancia.

### 4.2 Clic de referencia

| Parámetro | Valor de referencia |
|---|---|
| Sonido | `ui_click_hf_v1` (síntesis local, semilla 20261028) |
| Duración | 0,045 s |
| Ganancia | −2,0 dB en la mezcla |
| Relación con el nivel base | **+4,0 dB** respecto al clic antiguo de −6,0 dB |
| Retardo de onset | 12 ms (0,36 frame) para no elevar el pico del programa |
| Pico aislado tras ganancia | −12,4579 dBFS |
| Sincronía con la tarjeta | desfase < 1 frame; medido y declarado |

El clic debe empezar dentro del primer frame en que la tarjeta es visible o antes de él por menos de un frame. Se registra el desfase en milisegundos y en frames.

### 4.3 Pico

- El pico del programa **no debe subir** respecto al audio base.
- El material fuente ya puede superar 0 dBFS en float; el entregable conserva ese exceso si viene de la fuente. No se “arregla” con limiter por diseño; se documenta el número de muestras > 1,0 y la diferencia de pico.
- `true_peak` final debe ser menor o igual al de la fuente (los cuatro clips lo cumplen).

---

## 5. Manifiesto mínimo obligatorio

Cada Short nuevo debe entregar un `.manifest.json` con, como mínimo:

1. `schema` (usar `local_video_studio.mvp_deliverable.v1` o su versión vigente).
2. `created_at`, `status`, `review_status`.
3. `deliverable`: archivo, bytes, SHA-256, descripción de video y audio, duración de contenedor, duración de video y de audio.
4. `provenance.source_asset`: ruta, bytes, SHA-256 y metadatos del medio fuente.
5. `provenance.source_cut` y `vertical_source_clip`: ruta, bytes, SHA-256, rango solicitado, rango real y método de corte.
6. `provenance.editorial_selection`: ID, título, origen de la selección, rango fuente, rango de corte, inicio real, segmentos usados, confianza, motivo y estado de revisión.
7. `timing_alignment`: mapeo clip 0 → tiempo de fuente, método, deriva máxima, tramos comprobados y evidencia.
8. `overlays.subtitles`: archivo + hash, estilo, número de cues, primer y último cue, limitaciones.
9. `overlays.emphasis`: archivo + hash, estilo, eventos con texto, tiempos, segmento de evidencia y posición.
10. `overlays.neon_frame`: archivo + hash, generador/semilla, duración, píxeles medidos.
11. `overlays.notifications`: acción, título, subtítulo, tiempos de entrada/hold/salida, tamaño, centro, evidencia, motivo y `click_seconds`.
12. `overlays.click`: activo + hash, `sound_id`, tiempos, ganancia, relación con el nivel base, onset, pico aislado, desfases medidos.
13. `audio_mix`: filtro exacto, conservación del audio original, flags de limiter/ducking/ganancia, bitrate AAC, picos y notas de nivel.
14. `verification`: FFprobe real, checks de contenedor, overlays, audio, sincronía, geometría de tarjetas, hashes de la evidencia.
15. `protected_baselines`: hashes de los entregables previos que no deben cambiar.
16. `reproduction`: comandos ejecutables en orden.
17. `limitations`: lista honesta de lo no verificado o no garantizado.

El manifiesto **no** se reescribe para cambiar un estado de revisión. Las aprobaciones humanas se registran en un documento de decisión aparte, fechado y trazable.

---

## 6. Convenciones a comprobar en cada ejecución

1. Carpeta de ejecución nueva y aislada bajo `work/`, con fecha y hora.
2. Un solo render a la vez.
3. Corte en keyframe; rango real medido y declarado (no confundir rango solicitado con rango real).
4. Cero solapamiento con los rangos reales de los Shorts existentes.
5. Inicio comprensible y cierre razonable; documentar si el corte cae a mitad de frase.
6. Subtítulos legibles, centrados, dentro de la zona inferior.
7. Énfasis literales, centrados, sin colisión con tarjetas.
8. Neón solo en el borde.
9. Tarjetas centradas, con tiempos y clic sincronizados (< 1 frame).
10. Audio original bit-idéntico fuera de las ventanas de clic.
11. Pico del programa no superior al de la referencia.
12. Hashes de los protegidos sin cambios.
13. Manifiesto completo y `review_status: pending_user_review`.
14. Evidencia visual y de audio conservada en la carpeta de la ejecución.

---

## 7. Excepciones y variantes por clip

| Clip | Excepción documentada |
|---|---|
| Short 1 | Clic a −6,0 dB (nivel antiguo, anterior al estándar +4 dB). No se re-renderiza para no alterar el entregable. El audio dura 15 ms menos que el video. |
| Short 2 | Clic a −6,0 dB (mismo caso heredado). Manifiesto con menos checks explícitos que 3 y 4. El audio dura ~8 ms menos que el video. |
| Short 3 | Clic a −2,0 dB (+4 dB). El clic 2 empieza 10,1 ms antes del primer frame con alfa de tarjeta (0,30 frame); aceptado por estar dentro de 1 frame. El audio dura 5,858 ms menos que el video. |
| Short 4 | Clic a −2,0 dB (+4 dB). El corte empieza en 1131,485170 s (keyframe 1131,497033) para no solapar con el Short 2, aunque el rango editorial pedido empezaba en 1131,3 s. El corte cae a mitad de frase y el primer subtítulo incluye palabras anteriores. El audio dura 9,665 ms más que el video. |

Estas excepciones pertenecen a los entregables ya cerrados. **No se corrigen retroactivamente.**

---

## 8. Aspectos no confirmados

- No existen `word_timestamps`: la posición de énfasis y el recorte por palabra son proporcionales al segmento.
- No se ha verificado percepción humana del clic; solo evidencia espectral y muestras A/B.
- No hay aprobación humana registrada, elemento por elemento, para los Shorts 1, 2 y 3. Sus manifiestos siguen en `pending_user_review`.
- El Short 4 tiene una aprobación verbal del usuario registrada aparte (ver `docs/decisions/20261028_short4_aprobacion_usuario.md`); su manifiesto no se modificó.
- El audio fuente ya supera 0 dBFS; el estándar conserva el material y documenta el exceso, pero no garantiza cumplimiento de normas de loudness de plataforma.
- La duración de audio y video no coincide exactamente en ningún clip; la tolerancia observada es de ±20 ms.
- No se ha definido un mínimo/máximo de duración: los cuatro van de 31,9 s a 58,2 s. La duración se subordina a que la idea esté completa.
- No se ha validado el estándar con un render nuevo todavía; el Short 5 es la primera aplicación y queda pendiente de revisión humana.

---

## 9. Registro de decisiones

| Fecha | Decisión | Procedencia | Evidencia |
|---|---|---|---|
| 2026-10-28 | Se fija este estándar como referencia para nuevos Shorts | Operación de cierre MVP | Este documento + 4 manifiestos + `shorts_verification.json` |
| 2026-10-28 | El MP4 del Short 4 se considera visualmente correcto por el usuario (“el MP4 está genial”) | Mensaje del usuario en la sesión de OpenCode | `docs/decisions/20261028_short4_aprobacion_usuario.md` |
| 2026-10-29 | Se crea `docs/` como ubicación documental del proyecto | Esta ejecución | Este archivo |

---

## 10. Historial

- **1.0 (2026-10-28):** versión inicial basada en los Shorts 1–4 verificados por SHA-256 y FFprobe.
