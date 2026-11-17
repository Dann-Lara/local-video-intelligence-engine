"""Construye los jobs de entrega del video nuevo (fuente espanola).

Seleccion humana (no automatizada):
- Se descartan clip_0005/0006/0007 porque sus rangos se solapan con
  PROTECTED_RANGES de la fuente inglesa anterior. No se modifica baselines.py
  ni se relaja la validacion de solapes.
- Se recortan los finales colgantes de clip_0004 ("Únicamente") y clip_0008
  ("El turbo", dentro de una enumeracion) usando limites reales de Whisper.

Todo el texto SEO es espanol y cada dato/cita procede de la transcripcion real.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\SPARTAN PC\Documents\new_life\clipping")
RUN = ROOT / "work" / "run_20261116_new_source_full_pipeline"
JOBS = RUN / "jobs"
PROJECT_ID = "a7ace6f1-43b8-4769-9b67-58693575c499"
SOURCE_ASSET_ID = "sha256-75ddae1b7b1830fc96491d97b066e02150c51cd4420e5e535730df6023edb50c"
SOURCE = ROOT / "input" / "¿2 TURBOS SON MEJORES QUE 1¿ (2160p_30fps_AV1-128kbit_AAC-español).mp4"
SOURCE_SHA = "75DDAE1B7B1830FC96491D97B066E02150C51CD4420E5E535730DF6023EDB50C"
SOURCE_BYTES = 954138171
TRANS = ROOT / "projects" / PROJECT_ID / "transcription" / "transcription.json"
CLICK = ROOT / "work" / "mvp_short_clip5_20261028_200526" / "audio" / "click_hf_v1_stereo.wav"
DATE = "20261117"

SEO_SOURCES = [
    {"classification": "official_requirement",
     "url": "https://support.google.com/youtube/answer/10059070",
     "checked_on": "2026-11-15",
     "claim": "Shorts can be up to 3 minutes long."},
    {"classification": "official_requirement",
     "url": "https://support.google.com/youtube/answer/57407",
     "checked_on": "2026-11-15",
     "claim": "The title allows up to 100 characters and the description up to 5,000."},
    {"classification": "official_requirement",
     "url": "https://support.google.com/youtube/answer/6390658",
     "checked_on": "2026-11-15",
     "claim": "Up to three hashtags from the description can appear next to the title; they cannot contain spaces and more than 60 are ignored."},
    {"classification": "editorial_recommendation",
     "url": "https://support.google.com/youtube/answer/57407",
     "checked_on": "2026-11-15",
     "claim": "YouTube states that tags play a minimal role in discovery; they are used only as descriptors."},
    {"classification": "hypothesis",
     "url": "https://support.google.com/youtube/answer/6390658",
     "checked_on": "2026-11-15",
     "claim": "Hipotesis propia, no verificada: #Shorts mas dos etiquetas tematicas puede ayudar a clasificar el contenido; no garantiza resultados."},
]

# Recortes editoriales humanos: (clip_id, nuevo_fin, motivo).
TRIMS = {
    "clip_0004": (743.78, "Se elimina el segmento final colgante seg_0335 ('Únicamente'); el nuevo fin es el limite real de seg_0334."),
    "clip_0008": (1312.78, "Se elimina el final colgante seg_0806-0811 (inicio de enumeracion); el nuevo fin es el limite real de seg_0805."),
}

SEO = {
    "clip_0001": {
        "title": "¿Qué tipos de turbo hay en un Supra?",
        "alt_titles": ["Twin turbo, big turbo y un Supra de agencia",
                       "Así empieza el experimento con el Toyota Supra"],
        "description": (
            "Fragmento inicial del video: se plantea la pregunta sobre instalar un sistema turbo y se presenta "
            "el Toyota Supra usado en el experimento.\n\n"
            "Citas literales de la transcripción: "
            "\"¿Estás pensando en meterle un sistema turbo a tu carro?\" y "
            "\"Para eso vamos a utilizar este Toyota Supra, el cual trae un twin turbo de agencia.\"\n\n"
            "Texto extraído del video original. Sin promesas de resultados. Pendiente de revisión humana."
        ),
        "hashtags": ["#Shorts", "#Turbo", "#Supra"],
        "tags": ["turbo", "supra", "motor", "twin turbo", "automóvil"],
        "pinned": "¿Qué sistema turbo te interesa más? Cuéntanos en los comentarios.",
        "thumbnail_text": "TIPOS DE TURBO",
        "facts": [
            ("¿Estás pensando en meterle un sistema turbo a tu carro?", "seg_0001"),
            ("En este video vamos a ver cómo funcionan los diferentes tipos de sistema.", "seg_0003"),
            ("Para eso vamos a utilizar este Toyota Supra, el cual trae un twin turbo de agencia.", "seg_0004"),
        ],
        "notification_subtitle": "¿QUÉ TURBO PREFIERES?",
    },
    "clip_0002": {
        "title": "Motor del Supra: 290 caballos después de 33 años",
        "alt_titles": ["Así responde el motor original del Supra",
                       "La medición del motor después de 33 años"],
        "description": (
            "El fragmento muestra la evaluación del motor original del Toyota Supra y una medición en el dinamómetro.\n\n"
            "Citas literales de la transcripción: "
            "\"Vamos a ver qué tal se desempeña un motor después de 33 años de ese formado original.\" y "
            "\"290 caballos, nada mal.\"\n\n"
            "Texto extraído del video original. Sin promesas de resultados. Pendiente de revisión humana."
        ),
        "hashtags": ["#Shorts", "#Supra", "#Motor"],
        "tags": ["supra", "motor", "dinamómetro", "caballos", "automóvil"],
        "pinned": "¿Qué te parece la medición del motor original? Opina en los comentarios.",
        "thumbnail_text": "290 CABALLOS",
        "facts": [
            ("Vamos a ver qué tal se desempeña un motor después de 33 años de ese formado original.", "seg_0014"),
            ("Bueno, aquí al parecer tuvimos un problema.", "seg_0015"),
            ("290 caballos, nada mal.", "seg_0016"),
        ],
        "notification_subtitle": "¿QUÉ TE PARECE LA MEDICIÓN?",
    },
    "clip_0003": {
        "title": "Así funciona el twin turbo secuencial",
        "alt_titles": ["Primero un turbo y luego los dos",
                       "La válvula que controla el flujo del twin turbo"],
        "description": (
            "Explicación literal del funcionamiento de un sistema Twin Turbo secuencial y de la válvula que reparte el flujo.\n\n"
            "Citas de la transcripción: "
            "\"Este de aquí precisamente es un sistema Twin Turbo secuencial, lo que significa que primero funciona uno y luego funciona el otro.\" y "
            "\"Llegando así a tener los dos turbos funcionando, pero primero uno y luego los dos.\"\n\n"
            "Texto extraído del video original. Sin promesas de resultados. Pendiente de revisión humana."
        ),
        "hashtags": ["#Shorts", "#Turbo", "#Supra"],
        "tags": ["twin turbo", "supra", "motor", "válvula", "automóvil"],
        "pinned": "¿Conocías este sistema de turbos? Déjalo en los comentarios.",
        "thumbnail_text": "TWIN TURBO",
        "facts": [
            ("Este de aquí precisamente es un sistema Twin Turbo secuencial, lo que significa que primero funciona uno y luego funciona el otro.", "seg_0045"),
            ("esa es una válvula, aquí adentro tiene una mariposa que restringe el flujo y no deja que este funcione.", "seg_0047"),
            ("Llegando así a tener los dos turbos funcionando, pero primero uno y luego los dos.", "seg_0049"),
        ],
        "notification_subtitle": "¿CONOCÍAS ESTE SISTEMA?",
    },
    "clip_0004": {
        "title": "Más aire exige más gasolina",
        "alt_titles": ["Admisión más grande y inyectores de mayor volumen",
                       "Por qué cambiar los inyectores al subir el boost"],
        "description": (
            "El fragmento explica la relación entre una admisión de mayor volumen, la pérdida de torque en bajas "
            "revoluciones y la necesidad de más gasolina al aumentar el boost.\n\n"
            "Citas literales de la transcripción: "
            "\"Tiendes a perder un poquito\" , \"De torque en bajas revoluciones\" y "
            "\"Muchísima más gasolina\".\n\n"
            "Texto extraído del video original. Sin promesas de resultados. Pendiente de revisión humana."
        ),
        "hashtags": ["#Shorts", "#Turbo", "#Motor"],
        "tags": ["admisión", "inyectores", "boost", "motor", "automóvil"],
        "pinned": "¿Has cambiado inyectores en tu motor? Cuéntanos en los comentarios.",
        "thumbnail_text": "MÁS AIRE, MÁS GASOLINA",
        "facts": [
            ("De torque en bajas revoluciones", "seg_0316"),
            ("Muchísima más gasolina", "seg_0332"),
            ("Cuando metamos más boost", "seg_0333"),
        ],
        "notification_subtitle": "¿CAMBIARÍAS LOS INYECTORES?",
    },
    "clip_0008": {
        "title": "Por qué el sonido del turbo puede ser un problema",
        "alt_titles": ["Estrés axial y válvula de alivio",
                       "El aire que entra choca con el aire que sale"],
        "description": (
            "Explicación del ruido del turbo: el choque entre el aire que entra y el que sale produce estrés axial, "
            "y se muestra una válvula de alivio como forma de mitigarlo.\n\n"
            "Citas literales de la transcripción: "
            "\"A todos nos encanta ese sonido\" , \"Es un estrés axial\" y "
            "\"Es utilizar una válvula de alivio\".\n\n"
            "Texto extraído del video original. Sin promesas de resultados. Pendiente de revisión humana."
        ),
        "hashtags": ["#Shorts", "#Turbo", "#Motor"],
        "tags": ["turbo", "válvula de alivio", "sonido", "motor", "automóvil"],
        "pinned": "¿Te gusta el sonido del turbo? Dinóslo en los comentarios.",
        "thumbnail_text": "EL SONIDO DEL TURBO",
        "facts": [
            ("Choca con el aire que está saliendo", "seg_0784"),
            ("A todos nos encanta ese sonido", "seg_0785"),
            ("Es un estrés axial", "seg_0789"),
            ("Es utilizar una válvula de alivio", "seg_0798"),
        ],
        "notification_subtitle": "¿TE GUSTA ESE SONIDO?",
    },
    "clip_0009": {
        "title": "De 290 a 432 caballos en la medición final",
        "alt_titles": ["La medición final del Supra turbo",
                       "Se fue a 16 libras y dio 432 caballos"],
        "description": (
            "Momento final de la medición del vehículo con el sistema turbo instalado, con cifras tomadas literalmente "
            "de la transcripción.\n\n"
            "Citas literales: \"Se fue a 16 libras\" y \"Y nos dio 432 caballos\".\n\n"
            "Texto extraído del video original. Sin promesas de resultados. Pendiente de revisión humana."
        ),
        "hashtags": ["#Shorts", "#Supra", "#Turbo"],
        "tags": ["supra", "turbo", "caballos", "medición", "automóvil"],
        "pinned": "¿Qué te parece la cifra final? Comenta tu opinión.",
        "thumbnail_text": "432 CABALLOS",
        "facts": [
            ("Se fue a 16 libras", "seg_0925"),
            ("Y nos dio 432 caballos", "seg_0926"),
            ("Ya estamos del otro lado", "seg_0927"),
        ],
        "notification_subtitle": "¿QUÉ TE PARECE LA CIFRA?",
    },
}

ORDER = ["clip_0001", "clip_0002", "clip_0003", "clip_0004", "clip_0008", "clip_0009"]


def main() -> int:
    JOBS.mkdir(parents=True, exist_ok=True)
    tr = json.loads(TRANS.read_text(encoding="utf-8"))
    by_id = {s["segment_id"]: s for s in tr["segments"]}
    b = json.loads((RUN / "stage_b.json").read_text(encoding="utf-8"))
    cands = {c["clip_id"]: c for c in b["candidates"]}

    created = []
    for idx, clip_id in enumerate(ORDER, start=1):
        c = cands[clip_id]
        start = float(c["start_seconds"])
        end = float(c["end_seconds"])
        trim_note = ""
        if clip_id in TRIMS:
            end, trim_note = TRIMS[clip_id]
        refs = [r for r in c["transcript_refs"]
                if float(by_id[r]["end_seconds"]) <= end + 1e-6]
        if not refs:
            raise SystemExit(f"{clip_id}: sin refs tras el recorte")
        evidence = by_id[refs[0]]["text"].strip()
        s = SEO[clip_id]
        stem = f"a7ace6f1_{clip_id}_es_{DATE}"
        dur = end - start
        enter = min(6.0, max(1.0, dur * 0.15))
        hold_start = round(enter + 0.52, 3)
        hold_end = round(min(enter + 4.0, dur - 1.0), 3)
        exit_end = round(hold_end + 0.42, 3)

        candidate = {
            "clip_id": clip_id,
            "title": c["title"],
            "origin": "stage_b_editorial_20261117",
            "requested_start_seconds": start,
            "requested_end_seconds": end,
            "start_seconds": start,
            "end_seconds": end,
            "evidence_refs": refs,
            "evidence_text": evidence,
            "ad_classification": c["ad_classification"],
            "ad_evidence": "",
            "editorial_decision": "accept",
            "reason": c["editorial_reason"],
            "boundary_evaluation": {
                **c["boundary_evaluation"],
                "notes": (
                    str(c["boundary_evaluation"].get("notes", ""))
                    + (" " + trim_note if trim_note else "")
                ).strip(),
            },
            "confidence": c["confidence"],
            "human_review": {
                "approved": False,
                "status": "pending_user_review",
                "trim_applied": bool(trim_note),
                "trim_reason": trim_note,
            },
        }
        seo_data = {
            "title": s["title"],
            "alt_titles": s["alt_titles"],
            "description": s["description"],
            "hashtags": s["hashtags"],
            "tags": s["tags"],
            "pinned_comment": s["pinned"],
            "thumbnail_text": s["thumbnail_text"],
            "language": "es",
            "audience": "not_made_for_kids",
            "facts": [{"claim": cl, "segment_id": sid} for cl, sid in s["facts"]],
            "sources": SEO_SOURCES,
            "notes": (
                "SEO generado exclusivamente con citas y datos de la transcripción real del proyecto "
                + PROJECT_ID + ". Sin publicación automática. Pendiente de revisión humana."
            ),
        }
        job = {
            "job_id": f"{stem}_delivery",
            "project_id": PROJECT_ID,
            "source_asset_id": SOURCE_ASSET_ID,
            "output_stem": stem,
            "source": {"path": str(SOURCE).replace("\\", "/"),
                       "bytes": SOURCE_BYTES, "sha256": SOURCE_SHA},
            "transcription_path": str(TRANS).replace("\\", "/"),
            "click_sound": {
                "path": str(CLICK).replace("\\", "/"),
                "source": "synthetic_hf_v1",
                "gain_db": -2.0, "delay_s": 0.012, "duration_s": 0.045,
            },
            "candidate": candidate,
            "notifications": [{
                "action": "comment",
                "subtitle_text": s["notification_subtitle"],
                "enter_start_seconds": round(enter, 3),
                "hold_start_seconds": hold_start,
                "hold_end_seconds": hold_end,
                "exit_end_seconds": exit_end,
                "evidence_refs": [refs[0]],
                "reason": "CTA en español, sin afirmaciones nuevas; idioma del audio original.",
                "click": {"enabled_in_mix": True, "gain_db": -2.0,
                          "delay_s": 0.012, "duration_s": 0.045},
            }],
            "emphasis": [],
            "seo": seo_data,
            "negative_fixtures": [],
            "source_audio_language": "es",
            "source_audio_language_source": "job",
            "transcript_is_translation": False,
        }
        path = JOBS / f"{stem}.json"
        path.write_text(json.dumps(job, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        created.append({
            "clip_id": clip_id, "job": str(path), "stem": stem,
            "start_seconds": start, "end_seconds": end,
            "duration_seconds": round(dur, 3), "refs": len(refs),
            "trim": trim_note,
        })
        print(json.dumps(created[-1], ensure_ascii=False))

    selection = {
        "source_project": PROJECT_ID,
        "candidates_generated": len(cands),
        "selected": created,
        "excluded": [
            {"clip_id": "clip_0005", "reason": "Solape de 16.009 s con PROTECTED_RANGES['clip_0003'] de la fuente inglesa anterior; no se modifica baselines.py."},
            {"clip_id": "clip_0006", "reason": "Solape de 30.573 s y 5.295 s con PROTECTED_RANGES de la fuente inglesa anterior."},
            {"clip_id": "clip_0007", "reason": "Solape de 38.602 s con PROTECTED_RANGES['clip_0005'] de la fuente inglesa anterior."},
        ],
        "human_trims": TRIMS,
        "note": "Los rangos protegidos pertenecen a otra fuente; se evita cualquier solape sin relajar la validación.",
    }
    (RUN / "selection.json").write_text(
        json.dumps(selection, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print("OK", len(created), "jobs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
