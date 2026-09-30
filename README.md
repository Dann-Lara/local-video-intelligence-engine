# Local Video Intelligence Engine

Motor local que convierte un video largo en clips verticales con subtitulos,
con control de calidad y metadata de publicacion. Todo corre en local: el
transcriptor es Whisper y el modelo de lenguaje es Gemma servido por LM Studio.
No sale ningun video ni ninguna transcripcion a la nube.

## Estado

Sprint 7 (QC + SEO + report) implementado. El pipeline va de `INGESTING` a
`COMPLETED`. Los dos comandos que faltan (`process` y `performance sync`) son
siguiente sprint.

| Comando | Estado | Que hace |
| --- | --- | --- |
| `doctor` | hecho | Comprueba Python, Node, FFmpeg, CUDA, LM Studio y modelos |
| `analyze` | hecho | Ingesta + transcripcion + content map |
| `candidates` | hecho | Candidatos + ranking + edit plans |
| `render` | hecho | Renderiza los clips con FFmpeg + Remotion |
| `report` | hecho | QC tecnico y semantico + SEO por plataforma + report.json |
| `process` | pendiente | Atajo de todo lo anterior en un comando |
| `performance sync` | pendiente | Metricas de plataforma tras publicar |

## Requisitos

- Python 3.12 con las dependencias de `requirements.txt`
- FFmpeg y FFprobe en el `PATH`
- Node.js (para Remotion) + `npm install` dentro de `remotion/`
- LM Studio sirviendo un modelo cargado (Gemma 3 4B sirve)
- CUDA si se quiere `whisper.device: cuda`

Chrome o Edge instalado, o `LVI_RENDER__BROWSER_EXECUTABLE` apuntando a uno.
Sin navegador local Remotion intenta descargar el suyo y falla sin red.

## Instalacion

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
cd remotion && npm install && cd ..
.venv/Scripts/python.exe main.py doctor
```

`doctor` es el comando de referencia: dice que falta antes de que falle un
render a mitad.

## Uso

```bash
# 1. Ingesta, transcripcion y content map
.venv/Scripts/python.exe main.py analyze "input/mi_video.mp4"

# 2. Candidatos, ranking y planes de edicion
.venv/Scripts/python.exe main.py candidates

# 3. Render vertical con subtitulos quemados
.venv/Scripts/python.exe main.py render <job_id>

# 4. QC, SEO y report
.venv/Scripts/python.exe main.py report <job_id>
```

El `job_id` lo imprime cada comando y se puede recuperar con `main.py jobs`.

Para diagnostico, `report` tiene dos banderas utiles:

- `--skip-llm`: solo el QC tecnico (FFprobe y subtitulos). Distingue si un
  problema viene del render o del modelo sin pagar la llamada al LLM.
- `--overwrite`: vuelve a pedir el QC semantico y el SEO aunque esten cacheados.

Cualquier comando acepta `--help`. Los de la CLI devuelven codigos distintos
segun el resultado: `0` ok, `1` fallo, `2` comando todavia no implementado.

## Que escribe

Cada job tiene su workspace en `work/<nombre_del_video>/`:

| Artefacto | Contenido |
| --- | --- |
| `transcript.json` | Segmentos y palabras con tiempos, de Whisper |
| `normalized_transcript.json` | Parrafos, que es la unidad con la que razona Gemma |
| `content_map.json` | Entidades, claims, historias y momentos del material |
| `clip_candidates.json` | Candidatos con su score y el resultado del ranking |
| `edit_plans/` | Un plan por clip: trim, crop, formato, subtitulos, hook |
| `renders/` | Los MP4 finales, sus masters 9:16 y los props de Remotion |
| `clip_records/` | La ficha de cada clip: de donde salio y como paso el QC |
| `metadata/` | Titulo, descripcion y hashtags por plataforma |
| `qc/` | Cache de los veredictos semanticos y del SEO |

Y en la raiz del proyecto, `reports/report_<job_id>.json`: el resumen del job
(cuantos candidatos hubo, cuantos se aprobaron, cuantos clips pasaron el QC,
el score medio y los errores).

## Configuracion

La configuracion sale de `config/default.yaml` y se puede ajustar con
`config/local.yaml` o con variables de entorno. La precedencia es:

```
.env / variables de entorno  >  config/local.yaml  >  config/default.yaml
```

El separador de anidamiento es `__` (doble guion bajo), asi que
`LVI_OUTPUT__CRF=20` es `output.crf`. Las variables estan documentadas en
`.env.example`.

Lo que mas se toca:

- `output.*`: resolucion, fps, codec y CRF del clip final.
- `crop.mode`: `center`, `smart_vertical`, `face_tracking`, `speaker` o `manual`
  (seccion 29).
- `subtitles.*`: palabras por linea, lineas, duracion minima y maxima, estilo.
- `selection.*`: el score minimo y cuantos clips se seleccionan.
- `quality.*`: que comprobaciones se hacen, el score semantico minimo y la
  tolerancia de duracion.
- `llm.*`: la direccion de LM Studio y el modelo. Fijar el id exacto del
  modelo hace las ejecuciones reproducibles.

## Tests

```bash
.venv/Scripts/python.exe -m pytest -q
```

Los tests generan sus propios videos con FFmpeg en un temporal: no hay binarios
en el repo. Los que necesitan GPU o LM Studio estan marcados y se saltan solos.

Para el camino de render con Remotion hay un smoke test aparte que ademas
comprueba que las capas de texto existen de verdad, midiendo pixeles:

```bash
.venv/Scripts/python.exe scripts/render_smoke.py
```

Y para la geometria de un clip ya renderizado (que el hook no tape el contenido
y los subtitulos no se salgan):

```bash
.venv/Scripts/python.exe tools/check_layout.py <clip.props.json>
```

## Estructura

```
main.py            La CLI: un comando por etapa del pipeline
src/core/          Config, artefactos, schemas, invariantes, logs y la base de datos
src/media/         FFmpeg, FFprobe, crop y render
src/subtitles/     Construccion de cues
src/analysis/      Content map y generacion de candidatos
src/llm/           Cliente de LM Studio con reintentos y degradacion
src/quality/       QC tecnico, QC semantico, SEO y report
schemas/           Un JSON Schema por artefacto; se validan al escribir
remotion/          La composicion `Clip` que compone subtitulos y hook
tools/             Verificaciones de geometria sobre un render
```

## Notas de Windows

- La ruta del proyecto lleva un caracter acentuado (`dann´s`). FFmpeg y Remotion
  lo aguantan, pero si algo falla raro, `LVI_PATHS__WORK_ROOT` permite mover el
  trabajo a una ruta ASCII.
- Los comandos se invocan con `.venv/Scripts/python.exe` porque `python` puede
  apuntar a otra instalacion.

`blueprint.md` es la especificacion de la que sale todo el codigo; las decisiones
que no son obvias citan la seccion correspondiente.
