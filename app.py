from datetime import datetime, timedelta, timezone
from pathlib import Path
import os

from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from flask_login import (
    current_user,
    login_required,
    login_user,
    logout_user,
)

from auth import inicializar_login
from auth.auth_service import (
    actualizar_usuario,
    autenticar_usuario,
    cambiar_password,
    crear_usuario,
)
from auth.decorators import permiso_requerido, solo_administrador
from services.pdf_generator import generar_acta_pdf
from supabase_client import supabase


load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv(
    "SECRET_KEY",
    "cambia-esta-clave-en-produccion",
)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = (
    os.getenv("RENDER", "").lower() == "true"
)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
    minutes=int(os.getenv("SESSION_TIMEOUT_MINUTES", "30"))
)

inicializar_login(app)


RUTAS_PUBLICAS = {
    "login",
    "static",
}


@app.before_request
def controlar_sesion():
    endpoint = request.endpoint

    if endpoint is None or endpoint in RUTAS_PUBLICAS:
        return None

    if not current_user.is_authenticated:
        flash(
            "Tu sesion expiro o debes iniciar sesion nuevamente.",
            "warning",
        )
        return redirect(url_for("login", next=request.path))

    ahora = datetime.now(timezone.utc)
    ultimo_acceso = session.get("ultimo_acceso")

    if ultimo_acceso:
        try:
            ultimo = datetime.fromisoformat(ultimo_acceso)
            limite = app.config["PERMANENT_SESSION_LIFETIME"]

            if ahora - ultimo > limite:
                nombre = current_user.nombre
                logout_user()
                session.clear()
                flash(
                    f"La sesion de {nombre} expiro por inactividad.",
                    "warning",
                )
                return redirect(url_for("login"))
        except (TypeError, ValueError):
            session.clear()
            logout_user()
            flash("La sesion no es valida. Inicia sesion nuevamente.", "warning")
            return redirect(url_for("login"))

    session["ultimo_acceso"] = ahora.isoformat()
    session.permanent = True
    return None


# ==================================================
# FUNCIONES GENERALES
# ==================================================

def consultar_tabla(nombre_tabla, columnas="*", ordenar_por=None):
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
    return resultado.data[0] if resultado.data else None


def estado_operativo(activo):
    disponibilidad = str(
        activo.get("disponibilidad") or ""
    ).strip().lower()
    estado = str(activo.get("estado") or "").strip().lower()
    return disponibilidad or estado


def registrar_movimiento(
    activo_id,
    persona_id,
    accion,
    observacion=None,
):
    return (
        supabase
        .table("movimientos")
        .insert({
            "activo_id": activo_id,
            "persona_id": persona_id,
            "accion": accion,
            "observacion": observacion or None,
        })
        .execute()
    )


def registrar_historial(activo_id, accion, detalle):
    return (
        supabase
        .table("historial")
        .insert({
            "activo_id": activo_id,
            "accion": accion,
            "detalle": detalle,
        })
        .execute()
    )


def obtener_roles():
    return consultar_tabla("roles", "*", "nombre")


def plantilla_existe(nombre):
    return (Path(app.template_folder or "templates") / nombre).exists()


@app.context_processor
def utilidades_permisos():
    def tiene_permiso(modulo, accion="ver"):
        return (
            current_user.is_authenticated
            and current_user.tiene_permiso(modulo, accion)
        )

    return {
        "tiene_permiso": tiene_permiso,
    }


# ==================================================
# AUTENTICACION
# ==================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        recordar = request.form.get("recordar") == "on"

        if not username or not password:
            flash("Escribe el usuario y la contrasena.", "danger")
            return render_template("login.html")

        try:
            usuario = autenticar_usuario(username, password)
            if usuario is None:
                flash("Usuario o contrasena incorrectos.", "danger")
                return render_template("login.html")

            login_user(usuario, remember=recordar)
            session.permanent = True
            session["ultimo_acceso"] = datetime.now(
                timezone.utc
            ).isoformat()
            flash(f"Bienvenido, {usuario.nombre}.", "success")

            siguiente = request.args.get("next", "")
            if siguiente.startswith("/") and not siguiente.startswith("//"):
                return redirect(siguiente)

            return redirect(url_for("dashboard"))

        except Exception as error:
            print(f"Error iniciando sesion: {error}")
            flash("No fue posible iniciar sesion.", "danger")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    nombre = current_user.nombre
    logout_user()
    session.clear()
    flash(f"La sesion de {nombre} fue cerrada.", "success")
    return redirect(url_for("login"))


# ==================================================
# DASHBOARD
# ==================================================

@app.route("/")
@login_required
@permiso_requerido("dashboard", "ver")
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
@login_required
@permiso_requerido("inventario", "ver")
def inventario():
    activos = consultar_tabla("activos", "*", "codigo")
    return render_template("activos.html", activos=activos)


# ==================================================
# PERSONAS
# ==================================================

@app.route("/personas")
@login_required
@permiso_requerido("personas", "ver")
def personas():
    lista_personas = consultar_tabla("personas", "*", "nombre")
    return render_template("personas.html", personas=lista_personas)


@app.route("/persona/crear", methods=["POST"])
@app.route("/personas/crear", methods=["POST"])
@login_required
@permiso_requerido("personas", "crear")
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
@login_required
@permiso_requerido("personas", "editar")
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
@login_required
@permiso_requerido("personas", "eliminar")
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
@login_required
@permiso_requerido("asignaciones", "ver")
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
@login_required
@permiso_requerido("asignaciones", "crear")
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
            activo_id,
            persona_id,
            "Asignacion",
            observacion,
        )
        registrar_historial(
            activo_id,
            "Asignacion",
            f"Activo {codigo_activo} asignado a {nombre_persona}.",
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
@login_required
@permiso_requerido("devoluciones", "ver")
def devoluciones():
    activos = consultar_tabla("activos", "*", "codigo")
    activos_asignados = [
        activo for activo in activos
        if estado_operativo(activo) == "asignado"
    ]
    return render_template("devoluciones.html", activos=activos_asignados)


@app.route("/devolver/<int:activo_id>", methods=["POST"])
@app.route("/devoluciones/<int:activo_id>", methods=["POST"])
@login_required
@permiso_requerido("devoluciones", "crear")
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
            activo_id,
            None,
            "Devolucion",
            observacion,
        )
        registrar_historial(
            activo_id,
            "Devolucion",
            (
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
@login_required
@permiso_requerido("tickets", "ver")
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
@login_required
@permiso_requerido("tickets", "crear")
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
@login_required
@permiso_requerido("actas", "ver")
def actas():
    lista_actas = consultar_tabla("actas", "*", "id")
    lista_personas = consultar_tabla("personas", "*", "nombre")
    return render_template(
        "actas.html",
        actas=lista_actas,
        personas=lista_personas,
    )


@app.route("/actas/generar", methods=["POST"])
@login_required
@permiso_requerido("actas", "crear")
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
@login_required
@permiso_requerido("actas", "ver")
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
# REPORTES Y CONFIGURACION
# ==================================================

@app.route("/reportes")
@login_required
@permiso_requerido("reportes", "ver")
def reportes():
    resumen = {
        "activos": len(consultar_tabla("activos")),
        "personas": len(consultar_tabla("personas")),
        "tickets": len(consultar_tabla("tickets")),
        "actas": len(consultar_tabla("actas")),
    }
    return render_template("reportes.html", resumen=resumen)


@app.route("/configuracion")
@login_required
@permiso_requerido("configuracion", "ver")
def configuracion():
    areas = consultar_tabla("areas", "*", "id")
    return render_template("configuracion.html", areas=areas)


# ==================================================
# ADMINISTRACION DE USUARIOS
# ==================================================

@app.route("/usuarios")
@login_required
@solo_administrador
def usuarios():
    lista_usuarios = consultar_tabla("usuarios", "*", "nombre")
    roles = consultar_tabla("roles", "*", "nombre")

    if plantilla_existe("usuarios/lista.html"):
        return render_template(
            "usuarios/lista.html",
            usuarios=lista_usuarios,
            roles=roles,
        )

    return {
        "usuarios": lista_usuarios,
        "roles": roles,
    }


@app.route("/usuarios/crear", methods=["POST"])
@login_required
@solo_administrador
def crear_usuario_web():
    nombre = request.form.get("nombre", "").strip()
    username = request.form.get("username", "").strip().lower()
    correo = request.form.get("correo", "").strip().lower()
    password = request.form.get("password", "")
    rol_id = request.form.get("rol_id", type=int)

    try:
        crear_usuario(
            nombre=nombre,
            username=username,
            correo=correo,
            password=password,
            rol_id=rol_id,
        )
        flash("Usuario creado correctamente.", "success")
    except Exception as error:
        print(f"Error creando usuario: {error}")
        flash(str(error), "danger")

    return redirect(url_for("usuarios"))


@app.route(
    "/usuarios/<int:usuario_id>/editar",
    methods=["POST"],
)
@login_required
@solo_administrador
def editar_usuario_web(usuario_id):
    nombre = request.form.get("nombre", "").strip()
    username = request.form.get("username", "").strip().lower()
    correo = request.form.get("correo", "").strip().lower()
    rol_id = request.form.get("rol_id", type=int)

    if not rol_id:
        flash("Debes seleccionar un rol.", "danger")
        return redirect(url_for("usuarios"))

    if str(current_user.id) == str(usuario_id):
        usuario_actual = obtener_registro("usuarios", usuario_id)
        if usuario_actual and int(usuario_actual.get("rol_id")) != int(rol_id):
            flash("No puedes cambiar el rol de tu propia sesion.", "danger")
            return redirect(url_for("usuarios"))

    try:
        actualizar_usuario(
            usuario_id=usuario_id,
            nombre=nombre,
            username=username,
            correo=correo,
            rol_id=rol_id,
        )
        flash("Usuario actualizado correctamente.", "success")
    except Exception as error:
        print(f"Error actualizando usuario: {error}")
        flash(str(error), "danger")

    return redirect(url_for("usuarios"))


@app.route(
    "/usuarios/<int:usuario_id>/password",
    methods=["POST"],
)
@login_required
@solo_administrador
def cambiar_password_web(usuario_id):
    password_nueva = request.form.get("password", "")

    try:
        cambiar_password(usuario_id, password_nueva)
        flash("Contrasena actualizada correctamente.", "success")
    except Exception as error:
        print(f"Error cambiando contrasena: {error}")
        flash(str(error), "danger")

    return redirect(url_for("usuarios"))


@app.route(
    "/usuarios/<int:usuario_id>/estado",
    methods=["POST"],
)
@login_required
@solo_administrador
def cambiar_estado_usuario(usuario_id):
    activo = request.form.get("activo") == "true"

    if str(current_user.id) == str(usuario_id) and not activo:
        flash("No puedes desactivar tu propio usuario.", "danger")
        return redirect(url_for("usuarios"))

    try:
        supabase.table("usuarios").update({
            "activo": activo
        }).eq("id", usuario_id).execute()
        flash("Estado de usuario actualizado.", "success")
    except Exception as error:
        print(f"Error actualizando usuario: {error}")
        flash(str(error), "danger")

    return redirect(url_for("usuarios"))


@app.route("/usuarios/permisos", methods=["GET", "POST"])
@login_required
@solo_administrador
def administrar_permisos():
    roles = consultar_tabla("roles", "*", "nombre")
    modulos = consultar_tabla("modulos", "*", "orden")
    rol_id = request.values.get("rol_id", type=int)

    if request.method == "POST":
        if not rol_id:
            flash("Selecciona un rol.", "danger")
            return redirect(url_for("administrar_permisos"))

        rol = obtener_registro("roles", rol_id)
        if not rol:
            abort(404)

        if str(rol.get("nombre", "")).lower() == "administrador":
            flash(
                "Los permisos del Administrador siempre son totales.",
                "warning",
            )
            return redirect(
                url_for("administrar_permisos", rol_id=rol_id)
            )

        try:
            for modulo in modulos:
                modulo_id = modulo["id"]
                prefijo = f"modulo_{modulo_id}_"
                datos = {
                    "rol_id": rol_id,
                    "modulo_id": modulo_id,
                    "puede_ver": request.form.get(prefijo + "ver") == "on",
                    "puede_crear": request.form.get(prefijo + "crear") == "on",
                    "puede_editar": request.form.get(prefijo + "editar") == "on",
                    "puede_eliminar": request.form.get(prefijo + "eliminar") == "on",
                }

                existente = (
                    supabase
                    .table("permisos")
                    .select("id")
                    .eq("rol_id", rol_id)
                    .eq("modulo_id", modulo_id)
                    .limit(1)
                    .execute()
                )

                if existente.data:
                    (
                        supabase
                        .table("permisos")
                        .update(datos)
                        .eq("id", existente.data[0]["id"])
                        .execute()
                    )
                else:
                    supabase.table("permisos").insert(datos).execute()

            flash("Permisos actualizados correctamente.", "success")
        except Exception as error:
            print(f"Error actualizando permisos: {error}")
            flash(str(error), "danger")

        return redirect(
            url_for("administrar_permisos", rol_id=rol_id)
        )

    permisos = []
    if rol_id:
        permisos = (
            supabase
            .table("permisos")
            .select("*")
            .eq("rol_id", rol_id)
            .execute()
        ).data or []

    permiso_por_modulo = {
        permiso["modulo_id"]: permiso
        for permiso in permisos
    }

    if plantilla_existe("usuarios/permisos.html"):
        return render_template(
            "usuarios/permisos.html",
            roles=roles,
            modulos=modulos,
            rol_id=rol_id,
            permiso_por_modulo=permiso_por_modulo,
        )

    return {
        "roles": roles,
        "modulos": modulos,
        "rol_id": rol_id,
        "permisos": permisos,
    }


# ==================================================
# ERRORES Y TEST
# ==================================================

@app.errorhandler(403)
def acceso_denegado(error):
    if plantilla_existe("403.html"):
        return render_template("403.html"), 403
    return "No tienes permiso para realizar esta accion.", 403


@app.route("/test")
@login_required
def test():
    activos = consultar_tabla("activos")
    return {
        "conexion": True,
        "cantidad_activos": len(activos),
        "usuario": current_user.correo,
        "rol": current_user.rol_nombre,
    }


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True,
    )
