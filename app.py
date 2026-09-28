from pathlib import Path
import os

from dotenv import load_dotenv
from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from supabase_client import supabase
from services.pdf_generator import generar_acta_pdf


load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv(
    "SECRET_KEY",
    "gestion_activos_ti_2026"
)


# ==================================================
# FUNCIONES GENERALES
# ==================================================

def consultar_tabla(
    nombre_tabla,
    columnas="*",
    ordenar_por=None
):
    try:
        consulta = supabase.table(nombre_tabla).select(columnas)

        if ordenar_por:
            consulta = consulta.order(ordenar_por)

        resultado = consulta.execute()
        return resultado.data or []

    except Exception as error:
        print(f"Error consultando {nombre_tabla}: {error}")
        return []


def obtener_registro(nombre_tabla, registro_id):
    resultado = (
        supabase
        .table(nombre_tabla)
        .select("*")
        .eq("id", registro_id)
        .limit(1)
        .execute()
    )

    if not resultado.data:
        return None

    return resultado.data[0]


def estado_operativo(activo):
    disponibilidad = str(
        activo.get("disponibilidad") or ""
    ).strip().lower()

    estado = str(
        activo.get("estado") or ""
    ).strip().lower()

    return disponibilidad or estado


def registrar_movimiento(
    activo_id,
    persona_id,
    accion,
    observacion=None
):
    datos = {
        "activo_id": activo_id,
        "persona_id": persona_id,
        "accion": accion,
        "observacion": observacion or None,
    }

    return (
        supabase
        .table("movimientos")
        .insert(datos)
        .execute()
    )


def registrar_historial(activo_id, accion, detalle):
    datos = {
        "activo_id": activo_id,
        "accion": accion,
        "detalle": detalle,
    }

    return (
        supabase
        .table("historial")
        .insert(datos)
        .execute()
    )


# ==================================================
# DASHBOARD
# ==================================================

@app.route("/")
def dashboard():
    activos = consultar_tabla("activos", "*", "id")

    total = len(activos)
    disponibles = sum(
        1 for activo in activos
        if estado_operativo(activo) == "disponible"
    )
    asignados = sum(
        1 for activo in activos
        if estado_operativo(activo) == "asignado"
    )
    reparacion = sum(
        1 for activo in activos
        if "repar" in estado_operativo(activo)
    )
    bajas = sum(
        1 for activo in activos
        if "baja" in estado_operativo(activo)
    )

    return render_template(
        "dashboard.html",
        total=total,
        disponibles=disponibles,
        asignados=asignados,
        reparacion=reparacion,
        bajas=bajas,
    )


# ==================================================
# INVENTARIO
# ==================================================

@app.route("/inventario")
def inventario():
    activos = consultar_tabla("activos", "*", "codigo")
    return render_template("activos.html", activos=activos)


# ==================================================
# PERSONAS
# ==================================================

@app.route("/personas")
def personas():
    lista_personas = consultar_tabla("personas", "*", "nombre")
    return render_template("personas.html", personas=lista_personas)


@app.route("/persona/crear", methods=["POST"])
@app.route("/personas/crear", methods=["POST"])
def crear_persona_web():
    nombre = request.form.get("nombre", "").strip()
    documento = request.form.get("documento", "").strip()
    correo = request.form.get("correo", "").strip()
    cargo = request.form.get("cargo", "").strip()
    area = request.form.get("area", "").strip()

    if not nombre:
        flash("El nombre es obligatorio.", "danger")
        return redirect(url_for("personas"))

    try:
        supabase.table("personas").insert({
            "nombre": nombre,
            "documento": documento or None,
            "correo": correo or None,
            "cargo": cargo or None,
            "area": area or None,
            "estado": "Activo",
        }).execute()

        flash(f"Persona {nombre} creada correctamente.", "success")

    except Exception as error:
        print(f"Error creando persona: {error}")
        flash(f"No fue posible crear la persona: {error}", "danger")

    return redirect(url_for("personas"))


@app.route("/personas/<int:persona_id>/editar", methods=["POST"])
def editar_persona_web(persona_id):
    nombre = request.form.get("nombre", "").strip()
    documento = request.form.get("documento", "").strip()
    correo = request.form.get("correo", "").strip()
    cargo = request.form.get("cargo", "").strip()
    area = request.form.get("area", "").strip()

    if not nombre:
        flash("El nombre es obligatorio.", "danger")
        return redirect(url_for("personas"))

    try:
        supabase.table("personas").update({
            "nombre": nombre,
            "documento": documento or None,
            "correo": correo or None,
            "cargo": cargo or None,
            "area": area or None,
        }).eq("id", persona_id).execute()

        flash("Persona actualizada correctamente.", "success")

    except Exception as error:
        print(f"Error actualizando persona: {error}")
        flash(f"No fue posible actualizar la persona: {error}", "danger")

    return redirect(url_for("personas"))


@app.route("/personas/<int:persona_id>/desactivar", methods=["POST"])
def desactivar_persona_web(persona_id):
    try:
        supabase.table("personas").update({
            "estado": "Inactivo"
        }).eq("id", persona_id).execute()

        flash("Persona desactivada correctamente.", "success")

    except Exception as error:
        print(f"Error desactivando persona: {error}")
        flash(f"No fue posible desactivar la persona: {error}", "danger")

    return redirect(url_for("personas"))


# ==================================================
# ASIGNACIONES
# ==================================================

@app.route("/asignaciones")
def asignaciones():
    activos = consultar_tabla("activos", "*", "codigo")
    lista_personas = consultar_tabla("personas", "*", "nombre")

    activos_disponibles = [
        activo for activo in activos
        if estado_operativo(activo) == "disponible"
    ]

    personas_activas = [
        persona for persona in lista_personas
        if str(persona.get("estado") or "Activo").strip().lower()
        == "activo"
    ]

    return render_template(
        "asignaciones.html",
        activos=activos_disponibles,
        personas=personas_activas,
    )


@app.route("/asignar", methods=["POST"])
@app.route("/asignaciones/crear", methods=["POST"])
def crear_asignacion_web():
    activo_id = request.form.get("activo_id", type=int)
    persona_id = request.form.get("persona_id", type=int)
    observacion = request.form.get("observacion", "").strip()

    if not activo_id or not persona_id:
        flash("Selecciona una persona y un activo.", "danger")
        return redirect(url_for("asignaciones"))

    try:
        activo = obtener_registro("activos", activo_id)
        persona = obtener_registro("personas", persona_id)

        if activo is None:
            raise ValueError("El activo seleccionado no existe.")

        if persona is None:
            raise ValueError("La persona seleccionada no existe.")

        if estado_operativo(activo) != "disponible":
            raise ValueError("El activo ya no esta disponible.")

        nombre_persona = persona.get("nombre")
        area_persona = persona.get("area")
        codigo_activo = activo.get("codigo")

        supabase.table("activos").update({
            "disponibilidad": "Asignado",
            "asignado_a": nombre_persona,
            "area": area_persona,
            "observaciones": observacion or None,
        }).eq("id", activo_id).execute()

        registrar_movimiento(
            activo_id=activo_id,
            persona_id=persona_id,
            accion="Asignacion",
            observacion=observacion,
        )

        registrar_historial(
            activo_id=activo_id,
            accion="Asignacion",
            detalle=f"Activo {codigo_activo} asignado a {nombre_persona}.",
        )

        flash(
            f"{codigo_activo} asignado correctamente a {nombre_persona}.",
            "success",
        )

    except Exception as error:
        print(f"Error asignando activo: {error}")
        flash(str(error), "danger")

    return redirect(url_for("asignaciones"))


# ==================================================
# DEVOLUCIONES
# ==================================================

@app.route("/devoluciones")
def devoluciones():
    activos = consultar_tabla("activos", "*", "codigo")
    activos_asignados = [
        activo for activo in activos
        if estado_operativo(activo) == "asignado"
    ]
    return render_template("devoluciones.html", activos=activos_asignados)


@app.route("/devolver/<int:activo_id>", methods=["POST"])
@app.route("/devoluciones/<int:activo_id>", methods=["POST"])
def devolver_activo_web(activo_id):
    estado_destino = request.form.get("estado_destino", "Disponible")
    observacion = request.form.get("observacion", "").strip()

    estados_permitidos = {
        "Disponible",
        "En reparacion",
        "En reparación",
        "Dado de baja",
    }

    if estado_destino not in estados_permitidos:
        flash("El estado de devolucion no es valido.", "danger")
        return redirect(url_for("devoluciones"))

    try:
        activo = obtener_registro("activos", activo_id)

        if activo is None:
            raise ValueError("El activo seleccionado no existe.")

        if estado_operativo(activo) != "asignado":
            raise ValueError("El activo seleccionado no esta asignado.")

        codigo_activo = activo.get("codigo")
        persona_anterior = activo.get("asignado_a")

        supabase.table("activos").update({
            "disponibilidad": estado_destino,
            "asignado_a": None,
            "area": None,
            "observaciones": observacion or None,
        }).eq("id", activo_id).execute()

        registrar_movimiento(
            activo_id=activo_id,
            persona_id=None,
            accion="Devolucion",
            observacion=observacion,
        )

        registrar_historial(
            activo_id=activo_id,
            accion="Devolucion",
            detalle=(
                f"Activo {codigo_activo} devuelto por "
                f"{persona_anterior or 'persona no identificada'}. "
                f"Nuevo estado: {estado_destino}."
            ),
        )

        flash(f"{codigo_activo} fue devuelto correctamente.", "success")

    except Exception as error:
        print(f"Error devolviendo activo: {error}")
        flash(str(error), "danger")

    return redirect(url_for("devoluciones"))


# ==================================================
# TICKETS
# ==================================================

@app.route("/tickets")
def tickets():
    lista_tickets = consultar_tabla("tickets", "*", "id")
    activos = consultar_tabla("activos", "*", "codigo")
    return render_template(
        "tickets.html",
        tickets=lista_tickets,
        activos=activos,
    )


@app.route("/ticket/crear", methods=["POST"])
@app.route("/tickets/crear", methods=["POST"])
def crear_ticket_web():
    activo_id = request.form.get("activo_id", type=int)
    titulo = request.form.get("titulo", "").strip()
    descripcion = request.form.get("descripcion", "").strip()

    if not titulo or not descripcion:
        flash("El titulo y la descripcion son obligatorios.", "danger")
        return redirect(url_for("tickets"))

    try:
        supabase.table("tickets").insert({
            "activo_id": activo_id,
            "titulo": titulo,
            "descripcion": descripcion,
            "estado": "Pendiente",
        }).execute()

        flash("Ticket creado correctamente.", "success")

    except Exception as error:
        print(f"Error creando ticket: {error}")
        flash(f"No fue posible crear el ticket: {error}", "danger")

    return redirect(url_for("tickets"))


# ==================================================
# ACTAS PDF
# ==================================================

@app.route("/actas")
def actas():
    lista_actas = consultar_tabla("actas", "*", "id")
    lista_personas = consultar_tabla("personas", "*", "nombre")

    return render_template(
        "actas.html",
        actas=lista_actas,
        personas=lista_personas,
    )


@app.route("/actas/generar", methods=["POST"])
def generar_acta_web():
    persona_id = request.form.get("persona_id", type=int)
    tipo = request.form.get("tipo", "Entrega").strip()
    observaciones = request.form.get("observaciones", "").strip()

    if not persona_id:
        flash("Debes seleccionar una persona.", "danger")
        return redirect(url_for("actas"))

    tipos_permitidos = {
        "Entrega",
        "Devolucion",
        "Cambio",
        "Paz y salvo",
    }

    if tipo not in tipos_permitidos:
        flash("El tipo de acta no es valido.", "danger")
        return redirect(url_for("actas"))

    try:
        persona = obtener_registro("personas", persona_id)

        if persona is None:
            raise ValueError("La persona seleccionada no existe.")

        nombre_persona = str(persona.get("nombre") or "").strip()

        if not nombre_persona:
            raise ValueError("La persona no tiene un nombre valido.")

        respuesta_activos = (
            supabase
            .table("activos")
            .select("*")
            .eq("asignado_a", nombre_persona)
            .order("codigo")
            .execute()
        )
        activos_persona = respuesta_activos.data or []

        ruta_pdf, archivo_pdf = generar_acta_pdf(
            persona=persona,
            activos=activos_persona,
            tipo=tipo,
            observaciones=observaciones,
        )

        supabase.table("actas").insert({
            "persona_id": persona_id,
            "tipo": tipo,
            "ruta_pdf": archivo_pdf,
            "observaciones": observaciones or None,
        }).execute()

        print(f"Acta guardada en: {ruta_pdf}")

        return redirect(
            url_for(
                "descargar_acta",
                nombre_archivo=archivo_pdf,
            )
        )

    except Exception as error:
        print(f"Error generando acta PDF: {error}")
        flash(f"No fue posible generar el acta: {error}", "danger")
        return redirect(url_for("actas"))


@app.route("/actas/descargar/<path:nombre_archivo>")
def descargar_acta(nombre_archivo):
    nombre_limpio = Path(nombre_archivo).name
    carpeta_pdf = Path("pdf").resolve()
    archivo_pdf = carpeta_pdf / nombre_limpio

    if not archivo_pdf.exists():
        flash("El archivo PDF solicitado no existe.", "danger")
        return redirect(url_for("actas"))

    return send_from_directory(
        str(carpeta_pdf),
        nombre_limpio,
        as_attachment=True,
    )


# ==================================================
# REPORTES
# ==================================================

@app.route("/reportes")
def reportes():
    activos = consultar_tabla("activos")
    personas = consultar_tabla("personas")
    tickets = consultar_tabla("tickets")
    actas = consultar_tabla("actas")

    resumen = {
        "activos": len(activos),
        "personas": len(personas),
        "tickets": len(tickets),
        "actas": len(actas),
    }

    return render_template("reportes.html", resumen=resumen)


# ==================================================
# CONFIGURACION
# ==================================================

@app.route("/configuracion")
def configuracion():
    areas = consultar_tabla("areas", "*", "id")
    return render_template("configuracion.html", areas=areas)


# ==================================================
# LOGIN Y TEST
# ==================================================

@app.route("/login")
def login():
    return render_template("login.html")


@app.route("/test")
def test():
    activos = consultar_tabla("activos")
    return {
        "conexion": True,
        "cantidad_activos": len(activos),
    }


# ==================================================
# INICIAR APLICACION
# ==================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True,
    )
