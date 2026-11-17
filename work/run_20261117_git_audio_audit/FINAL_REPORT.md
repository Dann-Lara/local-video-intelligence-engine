# Informe final — cierre de versión: Git + auditoría del sonido de notificaciones

Fecha: 2026-11-17. Ejecución aislada: `work/run_20261117_git_audio_audit/`.
Objeto: guardar el estado funcional en Git y auditar el clic de las
notificaciones **sin modificar audio, diseño, mezcla, tiempos ni MP4**.

---

## A. Auditoría Git

### A.1 Estado inicial y remote

| Campo | Valor |
|---|---|
| Raíz del repositorio | `C:/Users/SPARTAN PC/Documents/new_life/clipping` |
| Rama | `main` |
| HEAD inicial | `988f3c414ee594a1fa9ebd5ce6990556342126dd` |
| Remote | `origin` |
| URL | `https://github.com/Dann-Lara/local-video-intelligence-engine.git` |
| `origin/main` antes del push | `988f3c414ee594a1fa9ebd5ce6990556342126dd` (idéntico a local) |

El commit base era un reset con **0 archivos rastreados**, por lo que
`git diff` estaba vacío: la revisión se hizo leyendo los archivos directamente.
No aparecieron cambios remotos inesperados ni conflictos.

### A.2 Inventario y revisión de seguridad

- 1.592 archivos, 8.202.208.625 bytes (~8,2 GB).
- 20 archivos ≥ 50 MB: los dos videos de `input/`, cortes intermedios en
  `work/` y stems de audio WAV.
- 126 MP4, 122 PNG, 42 WAV, 12 MOV, 131 `.pyc`.
- Único archivo marcado por nombre sensible: `.env.example`. Revisado
  manualmente: solo nombres de variables y valores de ejemplo no sensibles
  (`127.0.0.1`, rutas relativas, modelo de ejemplo). **Incluido** en el commit.
- Escaneo de contenido sensible (claves, tokens, contraseñas, PEM, `ghp_`,
  `sk-`, `AKIA`): **0 coincidencias**.
- Git LFS `3.6.1` instalado y filtros globales configurados, pero **no existe
  `.gitattributes`** y no hay objetos LFS. Por esa razón no se agregaron
  binarios grandes como blobs normales.

### A.3 Archivos incluidos en el commit

Commit `f90ef9951d7af5df47979b6de28f9e9815404f75` — **222 archivos,
88.252 inserciones**, sin binarios:

- Contratos y configuración: `.env.example`, `.gitignore`, `AGENTS.md`,
  `BLUEPRINT.md`, `README.md`, `docs/` (3).
- Código: `schemas/` (4 módulos), `services/` (ingestión, transcripción,
  editorial, renderizado, entrega y `baselines.py`), `tests/` (17 módulos,
  incluido `tests/test_rendering_timing.py`).
- Trazabilidad local: `projects/` (JSON de proyectos, transcripciones,
  candidatos, diagnósticos y avisos *legacy*; sin WAV).
- Entregables de texto: 12 manifiestos y 8 archivos SEO de `output/`
  (incluidos los 6 pares del prefijo `a7ace6f1_*_es_20261117`).
- Reproducción: informes, scripts y estados de
  `work/run_20261116_new_source_full_pipeline/` y
  `work/run_20261116_timing_recovery/`.
- Verificación de esta auditoría: `verify_integrity.py`, `inventory_scan.py`,
  `hashes_snapshot.json` y `verification_result.json`.

Se preservaron expresamente la corrección temporal de
`services/rendering/renderer.py`, la trazabilidad del corte en
`services/delivery/manifest.py`, `tests/test_rendering_timing.py`, los scripts
de reproducción, `work/run_20261116_new_source_full_pipeline/FINAL_REPORT.md`
y los seis SEO/manifiestos.

### A.4 Archivos excluidos y motivo

| Excluido | Motivo |
|---|---|
| `input/*.mp4` (2 videos, 1,6 GB y 954 MB) | Originales locales; política *local-first*, no subir video fuente |
| Todos los `*.mp4`, `*.mov`, `*.wav`, `*.png`, `*.ass` | Binarios generados o stems; sin `.gitattributes`/LFS, no versionar como blobs normales |
| `projects/**/audio/audio.wav` (50 MB y 39 MB) | Audio extraído, reproducible; se conserva en disco |
| `work/run_20261117_git_audio_audit/inventory.json` | Inventario generado con rutas locales completas; no necesario para reproducir |
| `__pycache__/`, `*.pyc`, `.pytest_cache/`, `*.log` | Cachés y registros temporales |
| `logs/`, `.env` | Registros locales y posible configuración privada |
| Resto de `work/` (siglos de experimentos anteriores) | Artefactos históricos no necesarios para el estado funcional actual |

No se borró, movió ni modificó ningún original, transcripción, respaldo,
configuración ni salida.

### A.5 Pruebas e integridad antes del commit

- `python -m pytest -q` → **463 passed, 37 subtests passed in 6.75 s**.
- Hashes antes/después de 30 archivos (6 MP4 + 6 SEO + 6 manifiestos + 10
  protegidos + fuente española + `verification_final.json`):
  **idénticos**, sin altas, bajas ni cambios (`hashes_identical=true`).
- Los seis entregables `a7ace6f1_*_es_20261117`: **22/22** checks MP4,
  **41/41** checks SEO, `fps=30000/1001`, decodificación íntegra, solape cero,
  `review_status=pending_user_review`, `human_review.approved=false`.
- `baselines.check_protected(output/)` → **10/10 archivos OK, `all_ok=true`**.
- `git diff --cached --check` solo reportó CRLF heredados en archivos ya
  existentes/generados; **0 binarios en el staging**.

### A.6 Commit y push

- Mensaje: `fix: precise video cuts and complete new source pipeline`.
- Hash: `f90ef9951d7af5df47979b6de28f9e9815404f75`.
- Remote verificado justo antes del push: `origin/main = 988f3c4`.
- Resultado: `988f3c4..f90ef99  main -> main` (fast-forward, sin force push).
- Verificación posterior: `refs/heads/main = f90ef9951d7af5df47979b6de28f9e9815404f75`.
- No hubo conflictos, reescritura ni reset destructivo.

---

## B. Auditoría del sonido de clic

### B.1 Archivo exacto

| Campo | Valor |
|---|---|
| Ruta | `work/mvp_short_clip5_20261028_200526/audio/click_hf_v1_stereo.wav` |
| Nombre | `click_hf_v1_stereo.wav` |
| SHA-256 | `CE60E1062DBD849EA0B22043B89AA2329EAA2C4855E74D8890C095101CF52C83` |
| Origen | Sintetizado localmente, determinista (`synth_click_hf.py`, semilla `20261028`); sin descargas |
| Formato / codec | WAV / PCM signed 16-bit little-endian (`pcm_s16le`) |
| Duración | `0,044989 s` (1.984 muestras) |
| Frecuencia de muestreo | 44.100 Hz |
| Canales | 2 (estéreo, idénticos) |
| Bitrate | 1.411.200 bit/s de audio PCM (1.419.013 bit/s en el contenedor) |
| RMS | `0,052774` → **−25,55 dBFS** |
| Pico máximo | `0,299988` → **−10,46 dBFS** |
| LUFS integrado | **−70,0 LUFS**, *true peak* −10,2 dBTP (ebur128). Valor no fiable como medida perceptiva: el archivo dura 45 ms y la ventana de gating de LUFS es mucho mayor |
| Ganancia configurada | **−2,0 dB** (factor lineal `0,794328`) |
| Nivel tras ganancia | RMS `0,041920` (**−27,55 dBFS**), pico `0,238289` (**−12,46 dBFS**) |

El generador aplica paso alto a 7 kHz, refuerzos tonales de 8,8 / 11,2 /
13,6 kHz, envolvente de ataque rápido y caída exponencial, `hanning**0,15` y
un **fundido de salida explícito de 8 ms**; se normaliza a pico `0,30` antes
de la ganancia de mezcla.

### B.2 Dónde se carga y cómo se dispara

- `services/delivery/pipeline.py` (`stage_render`, líneas 615–624) lee
  `job["click_sound"]["path"]`, verifica que exista y llama a `mix_with_clicks`.
- Los seis jobs de `work/run_20261116_new_source_full_pipeline/jobs/` apuntan
  **todos** al mismo WAV, con `gain_db=-2.0`, `delay_s=0.012`,
  `duration_s=0.045`. Hay **6 notificaciones y 6 clics habilitados**
  (`enabled_in_mix=true`), uno por clip.
- `services/rendering/overlays.py` (`mix_with_clicks`, líneas 217–282)
  construye por clic: `atrim=0:0.045, asetpts=PTS-STARTPTS,
  adelay=<(enter_start_seconds+0.012)*1000>|<...>, volume=0.794328`,
  y mezcla con `amix=inputs=N+1:duration=first:dropout_transition=0:normalize=0`.
  Salida AAC a 320 kbit/s. La tarjeta se anima con `enter_start_seconds`; el
  clic entra **12 ms después**, dentro del primer ~4 % del fade-in visual de
  300 ms. Coincide con la aparición de la notificación.
- `services/delivery/pipeline.py` fija por defecto `enabled_in_mix=true`,
  `gain_db=-2.0`, `duration_s=0.045`; `services/delivery/manifest.py` declara
  `ducking=false`, `limiter=false`, `global_gain=false` y el filtro usado.

### B.3 Fades, ducking y mezcla

- **Fades de video:** entrada 300 ms / salida 320 ms, desplazamiento 150 px
  (`overlays.animation_segments`). No afectan al audio.
- **Fades de audio:** solo los internos del WAV (envolvente exponencial y
  fade-out de 8 ms). El filtro de mezcla **no aplica fade-in ni fade-out**.
- **Ducking / atenuación / ganancia global:** ninguno. `amix` usa
  `normalize=0`, no hay compresor ni limiter y el audio original se conserva.
  `compose_overlay` usa `-an`, así que no hay duplicado de audio base.
- **Diferencia configurado vs efectivo:** **ninguna**. Configurado −2,0 dB;
  medido en el residuo final −12,38 a −12,56 dBFS de pico, frente a −10,46
  dBFS del archivo: exactamente −2 dB.

### B.4 Comparación auditiva objetiva

Se extrajeron copias temporales de `input_composed.mp4` (audio original antes
del clic) y de los seis MP4 finales. Se alinearon por correlación y se aisló
`residuo = final − original`. Resultados en `audio_measurements.json`:

| Clip | Pico clic final | Δ onset vs. esperado | Correlación clic esperado vs. residuo | RMS clic (banda 7–15 kHz) | Fondo inmediato (misma banda) | Margen |
|---|---:|---:|---:|---:|---:|---:|
| 0001 | −12,46 dBFS | −0,023 ms | 0,9886 | −29,80 dBFS | −35,86 dBFS | **+6,06 dB** |
| 0002 | −12,56 dBFS | 0,000 ms | 0,9719 | −29,74 dBFS | −38,55 dBFS | **+8,81 dB** |
| 0003 | −12,47 dBFS | 0,000 ms | 0,9999 | −29,69 dBFS | −62,46 dBFS | **+32,77 dB** |
| 0004 | −12,44 dBFS | 0,000 ms | 0,9967 | −29,73 dBFS | −54,52 dBFS | **+24,79 dB** |
| 0008 | −12,38 dBFS | 0,000 ms | 0,9853 | −29,72 dBFS | −38,36 dBFS | **+8,64 dB** |
| 0009 | −11,92 dBFS | −0,023 ms | 0,9598 | −29,81 dBFS | −23,16 dBFS | **−6,65 dB** |

Otros datos:

- El clic **está presente en los seis MP4**: un evento por clip, con
  desfase ≤ 0,023 ms y correlación 0,96–0,9999 contra el WAV con ganancia
  aplicada. No está ausente, no se pierde por sincronización y no se corta: el
  residuo conserva los 45 ms completos.
- En banda ancha, el clic (RMS ≈ −27,5 dBFS) queda **9–17 dB por debajo del
  fondo inmediato** (RMS −18,02 a −10,85 dBFS); el pico del programa está entre
  −0,18 y −0,95 dBFS y el *true peak* final entre −0,2 y −0,9 dBTP.
- En su banda de diseño (7–15 kHz) **supera al fondo en cinco de seis clips**
  entre +6 y +33 dB, lo que explica que pueda oírse pese al RMS bajo en banda
  ancha. En `clip_0009` el fondo tiene mucha energía aguda y el clic queda
  6,65 dB **por debajo**: es el caso con mayor riesgo de enmascaramiento.
- La audición humana sigue pendiente: el modelo no puede escuchar audio. Las
  conclusiones son mediciones objetivas, no una percepción inventada.

### B.5 Diagnóstico de la causa probable

No hay fallo de reproducción, sincronización ni de aplicación de ganancia. La
causa de un clic percibido como bajo es **amplitud del propio archivo** (pico
0,30 / −10,46 dBFS y RMS −25,55 dBFS) más una ganancia de mezcla moderada
(−2 dB), combinada con un programa de fondo muy cercano a 0 dBFS. En cinco
clips la banda de 7–15 kHz compensa esa diferencia; en `clip_0009` el fondo
agudo probablemente lo enmascara.

No se declaró "demasiado bajo" sin evidencia: la evidencia muestra que está
presente y sincronizado; el nivel **absoluto** es bajo por diseño y el
**relativo** depende del contenido. Subir la ganancia sin más tiene poco
margen: el *true peak* final ya está entre −0,2 y −0,9 dBTP y no hay limiter.

---

## C. Conclusión final

**Qué sonido se está usando:** el clic sintetizado local
`work/mvp_short_clip5_20261028_200526/audio/click_hf_v1_stereo.wav`
(PCM 16 bits, 44,1 kHz, estéreo, 45 ms, pico −10,46 dBFS), con −2 dB de
ganancia, 12 ms de retardo respecto a la entrada de la tarjeta y mezcla sin
ducking ni limiter. Está presente y sincronizado en los seis Shorts.

**Qué debe corregirse para alcanzar el volumen esperado (no ejecutado):**
definir un nivel objetivo de clic y ajustar la **amplitud del archivo o la
ganancia por notificación**, con gestión de headroom (limiter o ducking) para
no superar 0 dBTP; revisar especialmente `clip_0009`, donde el clic queda
6,65 dB por debajo del fondo en su propia banda. La corrección debe hacerse en
una fase posterior, con aprobación humana y sin alterar los seis MP4 ya
entregados.

**Estado final:** Git actualizado y pusheado (`main` en `f90ef99`); seis Shorts
intactos y pendientes de revisión; audio sin modificar; ningún contenido
publicado, subido ni programado.
