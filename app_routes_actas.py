# AGREGA ESTOS IMPORTS ARRIBA DE app.py
from flask import send_from_directory
from pathlib import Path
from services.pdf_generator import generar_acta_pdf


# AGREGA ESTAS RUTAS ANTES DEL if __name__ == "__main__":
@app.route("/actas")
def actas():
    lista_actas = consultar_tabla("actas", "*", "id")
    personas = consultar_tabla("personas", "*", "nombre")

    return render_template(
        "actas.html",
        actas=lista_actas,
        personas=personas
    )


@app.route("/actas/generar", methods=["POST"])
def generar_acta_web():
    persona_id = request.form.get("persona_id", type=int)
    tipo = request.form.get("tipo", "Entrega").strip()
    observaciones = request.form.get("observaciones", "").strip()

    if not persona_id:
        flash("Selecciona una persona.", "danger")
        return redirect(url_for("actas"))

    try:
        persona = obtener_registro("personas", persona_id)

        if persona is None:
            raise ValueError("La persona seleccionada no existe.")

        nombre = persona.get("nombre", "")

        activos_respuesta = (
            supabase
            .table("activos")
            .select("*")
            .eq("asignado_a", nombre)
            .execute()
        )
        activos_persona = activos_respuesta.data or []

        ruta_pdf, archivo_pdf = generar_acta_pdf(
            persona=persona,
            activos=activos_persona,
            tipo=tipo,
            observaciones=observaciones
        )

        datos_acta = {
            "persona_id": persona_id,
            "tipo": tipo,
            "ruta_pdf": archivo_pdf,
            "observaciones": observaciones or None
        }

        try:
            supabase.table("actas").insert(datos_acta).execute()
        except Exception as error_registro:
            print(f"PDF generado, pero no se registro el acta: {error_registro}")

        flash("Acta PDF generada correctamente.", "success")

        return redirect(
            url_for("descargar_acta", nombre_archivo=archivo_pdf)
        )

    except Exception as error:
        print(f"Error generando acta: {error}")
        flash(f"No fue posible generar el acta: {error}", "danger")
        return redirect(url_for("actas"))


@app.route("/actas/descargar/<path:nombre_archivo>")
def descargar_acta(nombre_archivo):
    return send_from_directory(
        Path("pdf").resolve(),
        nombre_archivo,
        as_attachment=True
    )
