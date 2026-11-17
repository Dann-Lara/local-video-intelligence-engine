# Informe final - pipeline completo sobre fuente española nueva

Fecha: 2026-11-17. Ejecución aislada: `work/run_20261116_new_source_full_pipeline/`.
Estado final: **6 Shorts publicados en `output/`, todos en `pending_user_review` y con `human_review.approved=false`**. No se publicó, subió ni programó nada.

## 1. Objetivo y alcance real

Se corrigió de forma permanente la precisión temporal del pipeline y se ejecutó el flujo completo para el único video nuevo elegible: `input/¿2 TURBOS SON MEJORES QUE 1¿ (2160p_30fps_AV1-128kbit_AAC-español).mp4`. Se obtuvieron metadatos y transcripción local, propuestas editoriales de LM Studio, selección humana, 6 renders, SEO, manifiestos y verificación final. No se instalaron ni actualizaron dependencias; no se usaron APIs de pago ni servicios externos.

## 2. Estado inicial e integridad de `output/`

- Branch `main`, HEAD `988f3c414ee594a1fa9ebd5ce6990556342126dd` (repo reiniciado, archivos sin seguimiento; no hay `git diff` útil).
- Suite base: `457 passed, 33 subtests passed in 7.56s`.
- Inventario inicial: 10 entregables protegidos, `baselines.check_protected()` OK.
- Fuente: SHA-256 `75DDAE1B7B1830FC96491D97B066E02150C51CD4420E5E535730DF6023EDB50C`, 954.138.171 bytes, 1586,340862 s, 3840×2160, AV1, AAC LC 44,1 kHz estéreo, 30/1 fps. Hash verificado al final sin cambios.
- Ningún original, transcripción, config ni entregable previo fue modificado o borrado.

## 3. Causa raíz temporal y corrección permanente

El fallo histórico era `-ss` antes de `-i` combinado con `-c copy`: forzaba keyframe y rebase, desplazando el inicio real. En `services/rendering/renderer.py` se sustituyó por seek a keyframe previo, `-ss` de salida, recodificación de video (`libx264 -crf 14 -preset veryfast -pix_fmt yuv420p`) y audio copiado (`-c:a copy`). `services/delivery/manifest.py` registra ahora la cadena real del método de corte. No se tocó `services/delivery/baselines.py`, `PROTECTED_RANGES`, tolerancias ni validaciones.

## 4. Pruebas de regresión temporal

Nuevo `tests/test_rendering_timing.py` con 4 casos históricos, caso real no-keyframe y trazabilidad:

- Regresión: `6 passed, 4 subtests passed in 1.08s`.
- Conjunto relevante: `193 passed, 37 subtests passed in 3.01s`.
- Suite completa tras el cambio: `463 passed, 37 subtests passed in 6.97s`.
- Suite final, sin cambios de código posteriores: `463 passed, 37 subtests passed in 6.93s`.

Verificación real con el código compartido sobre `rec_0002` 73→134 s: `actual_start=73.003537` (Δ `+0,003537 s`), NCC `0,99992915`, SSIM de frame real `0,991508` frente a `0,302908` del keyframe. Evidencia: `work/run_20261116_new_source_full_pipeline/verification_real_source.json`.

## 5. Ingesta y transcripción (Etapa A)

Proyecto `a7ace6f1-43b8-4769-9b67-58693575c499`. Transcripción local real: idioma `es`, **959 segmentos**, **0 word timestamps**, modelo `large-v3-turbo`, backend `cuda`, `float16`, `inference_completed=true`. Tiempos de Whisper conservados como referencia; no se inventaron palabras ni sincronizaciones. Nota menor: `stage_a.json` dice `prior_transcription="reused"` aunque el log indica transcripción nueva; el artefacto sí fue generado.

## 6. Etapa B editorial y calibración

LM Studio local `http://127.0.0.1:1234/v1`, modelo `google/gemma-3-4b`. El primer intento falló: ventanas de ~275 segmentos/~7 min, el 4B volcaba todos los `segment_id`, `max_tokens=3000` truncaba el JSON y algunas respuestas no contenían objeto JSON. Se recalibró **solo la configuración del script aislado**: `temperature=0`, `max_tokens=6000`, `request_timeout=300`, `max_repair_attempts=3`, `window_target_chars=900`, `window_hard_max_chars=1400`, `overlap_segments=1`, `context_segments=1`, `max_candidates_per_window=1`, `max_candidates_total=20`. No se modificó el código editorial compartido.

Resultado: tarea `succeeded`, **26 ventanas**, **9 candidatos válidos**, todos `accept` y `ad_classification=none`. Artefactos: `stage_b.json` y `projects/a7ace6f1-.../editorial/candidates.json`.

## 7. Selección humana, exclusiones y recortes

Seleccionados: `clip_0001`, `0002`, `0003`, `0004`, `0008`, `0009`. Excluidos por solape con `PROTECTED_RANGES` de la fuente inglesa anterior, sin modificar `baselines.py`: `0005` (16,009 s), `0006` (30,573 s y 5,295 s), `0007` (38,602 s). Recortes editoriales humanos sobre límites reales de Whisper: `clip_0004` fin `743,78` (quita `seg_0335` "Únicamente"); `clip_0008` fin `1312,78` (quita enumeración colgante `seg_0806`-`0811`). `clip_0009` se extendió hasta `1531,52` (`seg_0928`-`seg_0934`) para dar cierre natural y reunir evidencia lingüística suficiente; el detector no fue relajado. `emphasis=[]` porque no hay word timestamps. Todo registrado en `selection.json`.

## 8. Preparación de jobs y preflight

`build_new_jobs.py` generó 6 jobs con nombres únicos `a7ace6f1_clip_000{1,2,3,4,8,9}_es_20261117`, SEO español literal, CTA local, `approved=false`. `preflight_jobs.py` verificó: 6 jobs, rangos válidos, **cero solapes**, evidencia dentro del clip, **todos los facts citados literalmente**, `validate_candidate()` sin bloqueos y hashes protegidos OK. `clip_0002` conserva un hueco sin transcripción `63,88–78,46 s`, documentado como punto de revisión humana.

## 9. Render secuencial y normalización de FPS

Los 6 jobs se renderizaron **uno a la vez**, en directorios aislados `work/run_20261116_new_source_full_pipeline/delivery/<stem>/`. Cada primer intento terminó solo con `failed: ["frame_rate"]`: la fuente es 30/1 y el contrato de entrega exige 30000/1001. No se relajó el contrato: `normalize_fps.py` re-codificó únicamente el artefacto de trabajo con `ffmpeg -vf fps=30000/1001 -c:v libx264 -crf 18 -preset veryfast -pix_fmt yuv420p -c:a copy`, actualizó el `state.json` aislado con la nota `fps_normalization` (`contract_relaxed: false`, `shared_code_modified: false`) y se reanudó `validate_mp4,seo,validate_seo,manifest`. El audio original se conservó copiado.

## 10. Validación técnica por clip

Todos: H.264 High, yuv420p, 1080×1920, 30000/1001, AAC LC 44,1 kHz estéreo, decodificación íntegra, **22/22 checks MP4**, **41/41 checks SEO**, solape cero, `pending_user_review`, `approved=false`.

| Clip | Duración (s) | Δ inicio (s) | NCC inicio | NCC final | SHA-256 MP4 |
|---|---:|---:|---:|---:|---|
| 0001 | 16,483 | 0,000000 | 1,000000 | 0,999938 | `554146E6…00843` |
| 0002 | 32,966 | +0,017823 | 0,999994 | 0,999930 | `9B580C52…E37159C32` |
| 0003 | 29,489 | +0,008707 | 0,999432 | 0,999394 | `A8B9F2CD…97F5A68` |
| 0004 | 22,987 | +0,013832 | 0,999998 | 0,999959 | `21F86DF4…8A44B` |
| 0008 | 33,994 | +0,012562 | 0,999506 | 0,999482 | `ADF2A108…482D20` |
| 0009 | 43,243 | +0,009433 | 0,999808 | 0,999611 | `5FB683C7…D3654` |

Evidencia Completa: `work/run_20261116_new_source_full_pipeline/verification_final.json`.

## 11. Comprobaciones visuales y de audio

El modelo no puede recibir imágenes, por lo que la revisión visual humana sigue pendiente (estado exigido). Se sustituyó por medidas automáticas (`visual_checks.py`): encuadre fuente↔vertical NCC `0,999338–0,999760`, MAE `0,698–1,061`; overlays vertical↔final con `14–21 %` de píxeles modificados; fracción blanca en franja de subtítulos `0,047–0,159`; región de tarjeta `0,0017–0,127`. **6/6 PASS**.

Audio (`audio_checks.py`): RMS `0,173–0,249`, pico `0,89–0,96`, silencio ≤ `0,43 %`, clic presente en el tiempo de la tarjeta, decodificación OK, NCC ≥ `0,9994`. **6/6 PASS**. No se realizó audición humana; no se inventó contenido auditivo.

## 12. SEO, manifiestos y trazabilidad

SEO en español para los 6 clips, con citas y cifras literales respaldadas por segmentos reales (`clip_0002`: 33, 6000, 290; `clip_0009`: 16, 432). Fuentes clasificadas como `official_requirement`, `editorial_recommendation` e `hypothesis`, sin promesas de resultados. `validate_seo` devolvió `valid=true` y 41/41 checks por clip. Manifiestos con `review_status=pending_user_review`, `human_review.approved=false`, `cut_method` frame-accurate, pre-roll/output-seek documentados, aligned NCC y ausencia de solapes. `baselines.check_protected(output/)` sigue en `all_ok=true` tras cada publicación y al final. Inventario final: 31 archivos (13 previos + 18 nuevos: 6×MP4/SEO/manifiesto).

## 13. Estado final, riesgos y siguiente paso

- **Comandos ejecutados** (resultados reales): `build_new_jobs.py` → 6 jobs; `preflight_jobs.py` → 0 errores; renderer vía `python -m services.delivery --job … --stage all` (6× fallo FPS conocido); `normalize_fps.py` (6× OK); reanudación `validate_mp4,seo,validate_seo,manifest` (6× OK); `final_verify.py` → `ok=true`, 0 errores; `visual_checks.py` → 6/6; `audio_checks.py` → 6/6; `python -m pytest -q` → `463 passed, 37 subtests passed in 6.93s`.
- **Riesgos/limitaciones**: no hay word timestamps (subtítulos por segmento); `clip_0002` tiene hueco real de transcripción; el tag de idioma del contenedor es `und` pero ASR confirmó `es`; la conversión 30→29,97 puede descartar/duplicar un frame en clips largos, con desviación temporal ≤0,05 s medida; la revisión visual/auditiva humana aún no se realizó.
- **No hecho**: publicación, subida, programación, aprobación ni cambios en `baselines.py`, `PROTECTED_RANGES` o umbrales.
- **Siguiente paso**: revisar los 6 Shorts en el panel, escuchar/ver los fragmentos (especialmente `clip_0002` en `63,88–78,46 s`), aprobar o pedir ajustes. La aprobación humana debe registrarse explícitamente antes de cualquier exportación final o publicación.
