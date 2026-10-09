from collections import Counter
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os

from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    jsonify,
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
from flask_wtf.csrf import CSRFError, CSRFProtect

from api.recolector_routes import recolector_bp
from auth import inicializar_login
from auth.auth_service import (
    actualizar_usuario,
    autenticar_usuario,
    cambiar_password,
    crear_usuario,
    limpiar_cache_usuarios,
)
from auth.decorators import permiso_requerido, solo_administrador
from services.pdf_generator import generar_acta_pdf
from supabase_client import supabase
from services.config_service import limpiar_cache_config, obtener_config
from services.dashboard_service import construir_extras
from services.db_service import (
    consultar_recientes,
    consultar_tabla,
    estado_operativo,
    fecha_corta as _fecha_corta,
    fecha_larga as _fecha_larga,
    fecha_local as _fecha_local,
    hoy_colombia as _hoy_colombia,
    obtener_registro,
)
from services.finanzas_service import formato_dinero
from services.historial_service import (
    registrar_historial,
    registrar_historial_persona,
    registrar_movimiento,
)
# Ajusta la ruta si personas_service.py esta en otro lugar
# (en la raiz junto a app.py seria: from personas_service import ...)
from services.personas_service import (
    PersonaDuplicadaError,
    actualizar_persona,
    crear_persona,
)
from routes import (
    documentos_routes,
    finanzas_routes,
    inventario_routes,
    reportes_routes,
)
from routes.software_routes import create_software_bp

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
# Tope de la cookie. El limite real por inactividad sale de la
# configuracion (sesion_minutos) y se valida en controlar_sesion().
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=1)

# Recarga las plantillas al editarlas (sin reiniciar el servidor)
app.config["TEMPLATES_AUTO_RELOAD"] = True

# Limite de tamano por envio (facturas del activo + adicionales)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB

# Cookie "Recordar sesion" (Flask-Login)
app.config["REMEMBER_COOKIE_DURATION"] = timedelta(days=7)
app.config["REMEMBER_COOKIE_HTTPONLY"] = True
app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"
app.config["REMEMBER_COOKIE_SECURE"] = (
    os.getenv("RENDER", "").lower() == "true"
)

inicializar_login(app)
app.register_blueprint(recolector_bp)
app.register_blueprint(create_software_bp(supabase, login_required))

from routes.contratos_routes import create_contratos_bp
app.register_blueprint(create_contratos_bp(supabase))

# Proteccion CSRF global. El recolector ya no recibe datos por HTTP:
# los archivos se suben desde la web con sesion iniciada.
csrf = CSRFProtect(app)

# Filtro de plantilla y rutas que viven en routes/
app.add_template_filter(formato_dinero, "dinero")
inventario_routes.registrar_rutas(app)
finanzas_routes.registrar_rutas(app)
documentos_routes.registrar_rutas(app)
reportes_routes.registrar_rutas(app)

# Rutas accesibles sin iniciar sesion
RUTAS_PUBLICAS = {
    "login",
    "static",
}


def cerrar_sesion_y_redirigir(
    mensaje=None,
    categoria="success",
    destino="login",
    motivo="manual",
):
    """Cierra la sesion de forma definitiva y manda al login.

    Registra la salida en el historial ANTES de limpiar la sesion,
    mientras current_user todavia existe.

    El orden importa: primero se limpia la sesion y despues se llama a
    logout_user(), para que Flask-Login pueda marcar la cookie de
    "recordar" para borrado. Ademas se borra la cookie de forma explicita.
    """
    if current_user.is_authenticated:
        if motivo == "inactividad":
            detalle = f"{current_user.nombre} salió por inactividad."
        else:
            detalle = f"{current_user.nombre} cerró sesión."
        # registrar_historial_persona ya captura sus propios errores
        registrar_historial_persona("Salida", detalle)

    session.clear()
    logout_user()

    if mensaje:
        # El flash va despues de limpiar la sesion, si no se perderia
        flash(mensaje, categoria)

    respuesta = redirect(url_for(destino))
    respuesta.delete_cookie(
        app.config.get("REMEMBER_COOKIE_NAME", "remember_token")
    )
    return respuesta


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
            limite = timedelta(
                minutes=int(
                    obtener_config(usar_cache=True).get("sesion_minutos") or 30
                )
            )

            if ahora - ultimo > limite:
                nombre = current_user.nombre
                return cerrar_sesion_y_redirigir(
                    f"La sesion de {nombre} expiro por inactividad.",
                    "warning",
                    motivo="inactividad",
                )
        except (TypeError, ValueError):
            return cerrar_sesion_y_redirigir(
                "La sesion no es valida. Inicia sesion nuevamente.",
                "warning",
            )

    session["ultimo_acceso"] = ahora.isoformat()
    session.permanent = True
    return None


@app.after_request
def sin_cache(respuesta):
    """Evita que el navegador guarde paginas (boton 'atras' tras cerrar sesion)."""
    if request.endpoint != "static":
        respuesta.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0"
        )
        respuesta.headers["Pragma"] = "no-cache"
        respuesta.headers["Expires"] = "0"
    return respuesta


# ==================================================
# FUNCIONES GENERALES
# ==================================================

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


@app.context_processor
def inyectar_config():
    """Deja la configuracion disponible en todos los templates."""
    if not current_user.is_authenticated:
        return {}
    return {"config_empresa": obtener_config(usar_cache=True)}


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

            # current_user ya esta autenticado: el historial guarda
            # usuario_id y usuario_nombre automaticamente
            registrar_historial_persona(
                "Ingreso",
                f"{usuario.nombre} ingresó al sistema.",
            )

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
    return cerrar_sesion_y_redirigir(
        f"La sesion de {nombre} fue cerrada.",
        "success",
        motivo="manual",
    )


# ==================================================
# DASHBOARD
# ==================================================

def _garantias_por_vencer(activos, dias):
    """Activos cuya garantia (AAAA-MM-DD) vence dentro de 'dias' o ya vencio."""
    hoy = (datetime.now(timezone.utc) - timedelta(hours=5)).date()
    limite = hoy + timedelta(days=dias)
    lista = []

    for a in activos:
        if "baja" in estado_operativo(a):
            continue
        try:
            vence = datetime.strptime(
                str(a.get("garantia") or "")[:10], "%Y-%m-%d"
            ).date()
        except ValueError:
            continue

        if vence <= limite:
            lista.append({
                "codigo": a.get("codigo"),
                "tipo": a.get("tipo"),
                "vence": vence,
                "dias": (vence - hoy).days,
            })

    return sorted(lista, key=lambda x: x["dias"])


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

    # Activos por tipo (top 6)
    por_tipo = Counter(
        (str(a.get("tipo") or "").strip() or "Sin tipo")
        for a in activos
    ).most_common(6)

    # Activos asignados por area (top 5)
    por_area = Counter(
        str(a.get("area")).strip()
        for a in activos
        if estado_operativo(a) == "asignado" and a.get("area")
    ).most_common(5)

    # Tickets que siguen abiertos
    cerrados = {"cerrado", "resuelto", "solucionado", "finalizado", "completado"}
    todos_tickets = consultar_tabla("tickets", "*", "id")
    tickets_abiertos = [
        t for t in todos_tickets
        if str(t.get("estado") or "").strip().lower() not in cerrados
    ]
    tickets_abiertos.sort(key=lambda t: t.get("id") or 0, reverse=True)

    codigos = {a.get("id"): a.get("codigo") for a in activos}

    # Actividad reciente: hora local y codigo del activo
    # (12 filas: ingresos y salidas ocupan lugar junto a los movimientos)
    actividad = consultar_recientes("historial", 12)
    for mov in actividad:
        mov["fecha_local"] = _fecha_local(
            mov.get("created_at") or mov.get("fecha")
        )
        mov["activo_codigo"] = codigos.get(mov.get("activo_id"))

    lista_personas = consultar_tabla("personas", "*", "id")
    personas_activas = sum(
        1 for p in lista_personas
        if str(p.get("estado") or "Activo").strip().lower() == "activo"
    )

    # Garantias (se calculan una vez y se reutilizan en alertas)
    garantias = _garantias_por_vencer(
        activos,
        int(obtener_config(usar_cache=True).get("dias_alerta_garantia") or 30),
    )

    # Alertas, tendencias, vencimientos y rankings (services/dashboard_service.py)
    extras = construir_extras(activos, todos_tickets, garantias)

    return render_template(
        "dashboard.html",
        total=total,
        disponibles=disponibles,
        asignados=asignados,
        reparacion=reparacion,
        bajas=bajas,
        por_tipo=por_tipo,
        por_area=por_area,
        tickets_abiertos=tickets_abiertos[:5],
        total_tickets_abiertos=len(tickets_abiertos),
        codigos=codigos,
        personas_activas=personas_activas,
        total_actas=len(consultar_tabla("actas", "id")),
        actividad=actividad,
        garantias=garantias,
        hoy=datetime.now(timezone.utc) - timedelta(hours=5),
        **extras,
    )


# ==================================================
# INVENTARIO
# --------------------------------------------------
# Movido a routes/inventario_routes.py (se registra arriba
# con inventario_routes.registrar_rutas(app)).
# ==================================================


# ==================================================
# PERSONAS
# ==================================================

MOTIVOS_RETIRO = [
    "Renuncia",
    "Fin de contrato",
    "Despido",
    "Traslado",
    "Jubilación",
    "Otro",
]


def _persona_activa(persona):
    return str(persona.get("estado") or "Activo").strip().lower() == "activo"


def _persona_id_por_nombre(nombre):
    """Los activos se vinculan por nombre; esto recupera el id de la persona."""
    nombre = str(nombre or "").strip()
    if not nombre:
        return None
    try:
        r = (
            supabase
            .table("personas")
            .select("id")
            .eq("nombre", nombre)
            .limit(1)
            .execute()
        )
        return r.data[0]["id"] if r.data else None
    except Exception as error:
        print(f"Error buscando persona por nombre: {error}")
        return None


def _activos_de_persona(nombre):
    """Activos que la persona tiene asignados hoy."""
    nombre = str(nombre or "").strip()
    if not nombre:
        return []
    try:
        filas = (
            supabase
            .table("activos")
            .select("*")
            .eq("asignado_a", nombre)
            .order("codigo")
            .execute()
        ).data or []
    except Exception as error:
        print(f"Error consultando activos de persona: {error}")
        return []
    return [a for a in filas if estado_operativo(a) == "asignado"]


def _formatear_retiro(r):
    return {
        **r,
        "fecha_txt": _fecha_corta(r.get("fecha") or r.get("created_at")),
        "reactivado_txt": (
            _fecha_larga(r["reactivado_en"]) if r.get("reactivado_en") else ""
        ),
    }


@app.route("/personas")
@login_required
@permiso_requerido("personas", "ver")
def personas():
    estado = request.args.get("estado", "activos").strip().lower()
    if estado not in ("activos", "inactivos", "todos"):
        estado = "activos"

    lista_personas = consultar_tabla("personas", "*", "nombre")

    # Equipos asignados hoy, por nombre de persona
    equipos_por_nombre = Counter(
        str(a.get("asignado_a") or "").strip()
        for a in consultar_tabla("activos", "*", "id")
        if estado_operativo(a) == "asignado"
    )

    # Ultimo retiro de cada persona (orden ascendente: el ultimo gana)
    ultimo_retiro = {}
    for r in consultar_tabla("retiros_personas", "*", "id"):
        ultimo_retiro[r.get("persona_id")] = r

    for p in lista_personas:
        nombre = str(p.get("nombre") or "").strip()
        p["equipos"] = equipos_por_nombre.get(nombre, 0)

        retiro = ultimo_retiro.get(p.get("id"))
        p["retiro"] = (
            _formatear_retiro(retiro)
            if retiro and not _persona_activa(p) else None
        )

    conteos = {
        "activos": sum(1 for p in lista_personas if _persona_activa(p)),
        "inactivos": sum(1 for p in lista_personas if not _persona_activa(p)),
        "todos": len(lista_personas),
    }

    if estado == "activos":
        visibles = [p for p in lista_personas if _persona_activa(p)]
    elif estado == "inactivos":
        visibles = [p for p in lista_personas if not _persona_activa(p)]
    else:
        visibles = lista_personas

    return render_template(
        "personas.html",
        personas=visibles,
        conteos=conteos,
        filtro=estado,
    )


@app.route("/persona/crear", methods=["POST"])
@app.route("/personas/crear", methods=["POST"])
@login_required
@permiso_requerido("personas", "crear")
def crear_persona_web():
    nombre = request.form.get("nombre", "").strip()

    if not nombre:
        flash("El nombre es obligatorio.", "danger")
        return redirect(url_for("personas"))

    try:
        crear_persona(
            nombre=nombre,
            documento=request.form.get("documento"),
            correo=request.form.get("correo"),
            telefono=request.form.get("telefono"),
            cargo=request.form.get("cargo"),
            area=request.form.get("area"),
        )
        registrar_historial_persona("Persona creada", f"Persona {nombre} registrada.")
        flash(f"Persona {nombre} creada correctamente.", "success")
    except PersonaDuplicadaError as error:
        flash(str(error), "danger")
    except Exception as error:
        print(f"Error creando persona: {error}")
        flash(f"No fue posible crear la persona: {error}", "danger")

    return redirect(url_for("personas"))


@app.route("/personas/<int:persona_id>/editar", methods=["POST"])
@login_required
@permiso_requerido("personas", "editar")
def editar_persona_web(persona_id):
    nombre = request.form.get("nombre", "").strip()

    if not nombre:
        flash("El nombre es obligatorio.", "danger")
        return redirect(url_for("persona_detalle", persona_id=persona_id))

    try:
        anterior = obtener_registro("personas", persona_id)
        if anterior is None:
            raise ValueError("La persona no existe.")

        # El estado NO se toca aqui: cambia solo con "Registrar retiro" o "Reactivar"
        actualizar_persona(
            persona_id,
            nombre=nombre,
            documento=request.form.get("documento"),
            correo=request.form.get("correo"),
            telefono=request.form.get("telefono"),
            cargo=request.form.get("cargo"),
            area=request.form.get("area"),
        )

        # Los activos se vinculan por nombre: si cambia, se mantiene el vínculo
        if anterior.get("nombre") and anterior["nombre"] != nombre:
            supabase.table("activos").update({
                "asignado_a": nombre,
            }).eq("asignado_a", anterior["nombre"]).execute()

        detalle = f"Datos de {nombre} actualizados."
        if anterior.get("nombre") and anterior["nombre"] != nombre:
            detalle = f"Datos actualizados. Nombre: {anterior['nombre']} -> {nombre}."
        registrar_historial_persona("Persona editada", detalle)
        flash("Persona actualizada correctamente.", "success")
    except PersonaDuplicadaError as error:
        flash(str(error), "danger")
    except Exception as error:
        print(f"Error actualizando persona: {error}")
        flash(f"No fue posible actualizar la persona: {error}", "danger")

    return redirect(url_for("persona_detalle", persona_id=persona_id))


@app.route("/personas/<int:persona_id>/retirar", methods=["POST"])
@login_required
@permiso_requerido("personas", "eliminar")
def retirar_persona_web(persona_id):
    """Marca a la persona como Inactiva y guarda el motivo del retiro.

    No se borra nada: el historial y los equipos que tuvo se conservan.
    """
    destino = redirect(url_for("persona_detalle", persona_id=persona_id))

    motivo = request.form.get("motivo", "").strip()
    notas = request.form.get("notas", "").strip()
    fecha_txt = request.form.get("fecha", "").strip()

    if motivo not in MOTIVOS_RETIRO:
        flash("Selecciona un motivo de retiro válido.", "danger")
        return destino

    hoy = _hoy_colombia()
    try:
        fecha = (
            datetime.strptime(fecha_txt, "%Y-%m-%d").date()
            if fecha_txt else hoy
        )
    except ValueError:
        flash("La fecha de retiro no es válida.", "danger")
        return destino

    if fecha > hoy:
        flash("La fecha de retiro no puede ser futura.", "danger")
        return destino

    try:
        persona = obtener_registro("personas", persona_id)
        if persona is None:
            raise ValueError("La persona no existe.")
        if not _persona_activa(persona):
            raise ValueError("La persona ya está inactiva.")

        pendientes = _activos_de_persona(persona.get("nombre"))
        if pendientes:
            raise ValueError(
                f"Todavía tiene {len(pendientes)} equipo(s) sin devolver. "
                "Registra primero la devolución."
            )

        registro = {
            "persona_id": persona_id,
            "fecha": fecha.isoformat(),
            "motivo": motivo,
            "notas": notas or None,
            "usuario_id": int(current_user.id),
            "usuario_nombre": current_user.nombre,
        }
        creado = supabase.table("retiros_personas").insert(registro).execute()
        retiro_id = creado.data[0]["id"] if creado.data else None

        try:
            supabase.table("personas").update({
                "estado": "Inactivo",
            }).eq("id", persona_id).execute()
        except Exception:
            # Si no se pudo inactivar, no dejar un retiro "fantasma"
            if retiro_id:
                try:
                    supabase.table("retiros_personas").delete().eq(
                        "id", retiro_id
                    ).execute()
                except Exception as error_borrado:
                    print(f"Error revirtiendo retiro: {error_borrado}")
            raise

        registrar_historial_persona(
            "Retiro",
            f"{persona.get('nombre')} se retiró de la empresa. Motivo: {motivo}.",
        )
        flash(
            f"Retiro de {persona.get('nombre')} registrado. "
            "Quedó como Inactiva con todo su historial.",
            "success",
        )
    except Exception as error:
        print(f"Error registrando retiro: {error}")
        flash(f"No fue posible registrar el retiro: {error}", "danger")

    return destino


@app.route("/personas/<int:persona_id>/activar", methods=["POST"])
@login_required
@permiso_requerido("personas", "editar")
def activar_persona_web(persona_id):
    try:
        supabase.table("personas").update({
            "estado": "Activo"
        }).eq("id", persona_id).execute()

        # Marca el retiro abierto como reactivado (el registro se conserva)
        try:
            supabase.table("retiros_personas").update({
                "reactivado_en": datetime.now(timezone.utc).isoformat(),
            }).eq("persona_id", persona_id).is_(
                "reactivado_en", "null"
            ).execute()
        except Exception as error:
            print(f"Error marcando reactivacion del retiro: {error}")

        persona = obtener_registro("personas", persona_id)
        registrar_historial_persona(
            "Persona reactivada",
            f"{(persona or {}).get('nombre') or 'Persona'} fue reactivada.",
        )
        flash("Persona activada correctamente.", "success")
    except Exception as error:
        print(f"Error activando persona: {error}")
        flash(f"No fue posible activar la persona: {error}", "danger")

    destino = request.form.get("volver")
    if destino == "detalle":
        return redirect(url_for("persona_detalle", persona_id=persona_id))
    return redirect(url_for("personas"))


@app.route("/personas/<int:persona_id>")
@login_required
@permiso_requerido("personas", "ver")
def persona_detalle(persona_id):
    persona = obtener_registro("personas", persona_id)
    if persona is None:
        abort(404)

    nombre = str(persona.get("nombre") or "").strip()
    activa = _persona_activa(persona)

    # Activos asignados actualmente
    activos_asignados = _activos_de_persona(nombre)
    ids_actuales = {a["id"] for a in activos_asignados}

    # Movimientos de esta persona (orden cronologico)
    try:
        movimientos = (
            supabase.table("movimientos")
            .select("*")
            .eq("persona_id", persona_id)
            .order("id")
            .limit(1000)
            .execute()
        ).data or []
    except Exception as error:
        print(f"Error consultando movimientos: {error}")
        movimientos = []

    activo_ids = {m["activo_id"] for m in movimientos if m.get("activo_id")}
    activo_ids |= ids_actuales

    # Todos los movimientos de esos equipos (de cualquier persona), para saber
    # cuando y como termino cada tenencia
    mov_por_activo = {}
    if activo_ids:
        try:
            filas = (
                supabase.table("movimientos")
                .select("*")
                .in_("activo_id", list(activo_ids))
                .order("id")
                .limit(5000)
                .execute()
            ).data or []
            for fila in filas:
                mov_por_activo.setdefault(fila["activo_id"], []).append(fila)
        except Exception as error:
            print(f"Error consultando movimientos de equipos: {error}")

    # Datos de los equipos (incluye los que hoy tienen otra persona)
    activos_por_id = {a["id"]: a for a in activos_asignados}
    faltan = [i for i in activo_ids if i not in activos_por_id]
    if faltan:
        try:
            filas = (
                supabase.table("activos")
                .select("*")
                .in_("id", faltan)
                .execute()
            ).data or []
            activos_por_id.update({f["id"]: f for f in filas})
        except Exception as error:
            print(f"Error consultando equipos: {error}")

    def fecha_mov(m):
        return m.get("created_at") or m.get("fecha")

    def es_asignacion(m):
        return str(m.get("accion") or "").strip().lower().startswith("asign")

    def es_devolucion(m):
        return str(m.get("accion") or "").strip().lower().startswith("devol")

    # ---------- Equipos que ha tenido (cada tenencia con desde / hasta) ----------
    equipos_historial = []
    cierres = set()  # ids de devoluciones que cerraron una tenencia

    for m in movimientos:
        if not es_asignacion(m):
            continue

        aid = m.get("activo_id")
        siguiente = next(
            (n for n in mov_por_activo.get(aid, []) if n["id"] > m["id"]),
            None,
        )

        hasta = None
        if siguiente is None:
            estado = "actual" if aid in ids_actuales else "sin_registro"
        else:
            hasta = _fecha_larga(fecha_mov(siguiente))
            if es_devolucion(siguiente):
                estado = "devuelto"
                cierres.add(siguiente["id"])
            else:
                estado = "reasignado"

        equipos_historial.append({
            "orden": m["id"],
            "activo": activos_por_id.get(aid) or {},
            "desde": _fecha_larga(fecha_mov(m)),
            "hasta": hasta,
            "estado": estado,
        })

    # Devoluciones que no cerraron ninguna asignacion registrada
    # (equipo asignado por otra via o antes de existir el historial)
    for m in movimientos:
        if es_devolucion(m) and m["id"] not in cierres:
            equipos_historial.append({
                "orden": m["id"],
                "activo": activos_por_id.get(m.get("activo_id")) or {},
                "desde": "—",
                "hasta": _fecha_larga(fecha_mov(m)),
                "estado": "devuelto",
            })

    # Equipos asignados hoy que no tienen movimiento de asignacion registrado
    con_tenencia_actual = {
        e["activo"].get("id") for e in equipos_historial
        if e["estado"] == "actual"
    }
    for a in activos_asignados:
        if a["id"] not in con_tenencia_actual:
            equipos_historial.append({
                "orden": 0,
                "activo": a,
                "desde": "—",
                "hasta": None,
                "estado": "actual",
            })

    equipos_historial.sort(key=lambda e: e["orden"], reverse=True)

    # ---------- Retiros ----------
    try:
        retiros = [
            _formatear_retiro(r)
            for r in (
                supabase.table("retiros_personas")
                .select("*")
                .eq("persona_id", persona_id)
                .order("id", desc=True)
                .execute()
            ).data or []
        ]
    except Exception as error:
        print(f"Error consultando retiros: {error}")
        retiros = []

    # ---------- Historial completo (movimientos + retiros + reactivaciones) ----------
    movs = {m["id"]: m for m in movimientos}
    for mov_id in cierres:
        for lista in mov_por_activo.values():
            for n in lista:
                if n["id"] == mov_id:
                    movs.setdefault(mov_id, n)

    linea = []
    for m in movs.values():
        linea.append({
            "orden": str(fecha_mov(m) or ""),
            "fecha": _fecha_larga(fecha_mov(m)),
            "accion": m.get("accion"),
            "activo": activos_por_id.get(m.get("activo_id")),
            "detalle": m.get("observacion"),
        })

    for r in retiros:
        detalle = r.get("motivo") or ""
        if r.get("notas"):
            detalle += f" — {r['notas']}"
        linea.append({
            "orden": str(r.get("created_at") or r.get("fecha") or ""),
            "fecha": r.get("fecha_txt") or "",
            "accion": "Retiro",
            "activo": None,
            "detalle": detalle,
        })
        if r.get("reactivado_en"):
            linea.append({
                "orden": str(r["reactivado_en"]),
                "fecha": r["reactivado_txt"],
                "accion": "Reactivacion",
                "activo": None,
                "detalle": "Persona reactivada.",
            })

    linea.sort(key=lambda x: x["orden"], reverse=True)

    # ---------- Actas ----------
    try:
        actas_persona = (
            supabase.table("actas")
            .select("*")
            .eq("persona_id", persona_id)
            .order("id", desc=True)
            .execute()
        ).data or []
    except Exception as error:
        print(f"Error consultando actas: {error}")
        actas_persona = []

    return render_template(
        "persona_detalle.html",
        persona=persona,
        activa=activa,
        activos=activos_asignados,
        equipos_historial=equipos_historial,
        linea=linea,
        actas=actas_persona,
        retiros=retiros,
        motivos_retiro=MOTIVOS_RETIRO,
        hoy=_hoy_colombia().isoformat(),
    )


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

    # Acta recien generada (se descarga sola al cargar la pagina)
    acta = Path(request.args.get("acta", "")).name
    if acta and not (Path("pdf") / acta).exists():
        acta = ""

    return render_template(
        "asignaciones.html",
        activos=activos_disponibles,
        personas=personas_activas,
        acta=acta or None,
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
        if not _persona_activa(persona):
            raise ValueError("La persona seleccionada está inactiva.")
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
            persona_id=persona_id,
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


# Estados con los que puede quedar un equipo al devolverlo
ESTADOS_DEVOLUCION = {
    "Disponible": "Disponible",
    "En reparacion": "En reparación",
    "En reparación": "En reparación",
    "Dado de baja": "Dado de baja",
}


@app.route("/asignaciones/persona/<int:persona_id>/activos")
@login_required
@permiso_requerido("asignaciones", "ver")
def activos_de_persona(persona_id):
    """Equipos que la persona tiene asignados (para el paso 2 del formulario)."""
    persona = obtener_registro("personas", persona_id)
    if persona is None:
        return jsonify([])

    nombre = str(persona.get("nombre") or "").strip()
    if not nombre:
        return jsonify([])

    try:
        filas = (
            supabase
            .table("activos")
            .select("id,codigo,tipo,marca,modelo,serial,disponibilidad,estado")
            .eq("asignado_a", nombre)
            .order("codigo")
            .execute()
        ).data or []
    except Exception as error:
        print(f"Error consultando equipos de la persona: {error}")
        return jsonify([]), 500

    return jsonify([f for f in filas if estado_operativo(f) == "asignado"])


@app.route("/asignaciones/cambio", methods=["POST"])
@login_required
@permiso_requerido("asignaciones", "crear")
def cambio_equipo_web():
    """Devolucion + asignacion + acta en un solo paso.

    - Solo devolver:  devolver_ids sin activos_ids  -> acta de Devolucion
    - Solo asignar:   activos_ids sin devolver_ids  -> acta de Entrega
    - Ambos:          acta de Cambio

    Se pueden entregar varios equipos a la vez (activos_ids).
    """
    persona_id = request.form.get("persona_id", type=int)
    observacion = request.form.get("observacion", "").strip()
    quiere_acta = request.form.get("generar_acta") == "on"

    devolver_ids = []
    for valor in request.form.getlist("devolver_ids"):
        try:
            numero = int(valor)
        except ValueError:
            continue
        if numero not in devolver_ids:
            devolver_ids.append(numero)

    # Equipos a entregar (uno o varios, sin repetidos)
    nuevos_ids = []
    for valor in request.form.getlist("activos_ids"):
        try:
            numero = int(valor)
        except ValueError:
            continue
        if numero not in nuevos_ids:
            nuevos_ids.append(numero)

    if not persona_id:
        flash("Selecciona una persona.", "danger")
        return redirect(url_for("asignaciones"))

    if not devolver_ids and not nuevos_ids:
        flash("Marca al menos un equipo para devolver o elige uno para entregar.", "danger")
        return redirect(url_for("asignaciones"))

    if devolver_ids:
        if not current_user.tiene_permiso("devoluciones", "crear"):
            abort(403)
        if not observacion:
            flash("La observación es obligatoria al devolver equipos.", "danger")
            return redirect(url_for("asignaciones"))

    # ---------- 1) Validar TODO antes de tocar la base de datos ----------
    try:
        persona = obtener_registro("personas", persona_id)
        if persona is None:
            raise ValueError("La persona seleccionada no existe.")
        if str(persona.get("estado") or "Activo").strip().lower() != "activo":
            raise ValueError("La persona seleccionada está inactiva.")

        nombre_persona = str(persona.get("nombre") or "").strip()
        if not nombre_persona:
            raise ValueError("La persona no tiene un nombre válido.")

        devueltos = []
        for activo_id in devolver_ids:
            activo = obtener_registro("activos", activo_id)
            if (
                activo is None
                or estado_operativo(activo) != "asignado"
                or str(activo.get("asignado_a") or "").strip() != nombre_persona
            ):
                raise ValueError(
                    "Uno de los equipos a devolver ya no está asignado a esta "
                    "persona. Recarga la página e intenta de nuevo."
                )

            estado_destino = ESTADOS_DEVOLUCION.get(
                request.form.get(f"estado_{activo_id}", "Disponible")
            )
            if estado_destino is None:
                raise ValueError("El estado de devolución no es válido.")

            # Copia del activo ANTES de devolverlo (despues se limpia asignado_a)
            devueltos.append({**activo, "estado_devolucion": estado_destino})

        nuevos = []
        for nid in nuevos_ids:
            nuevo = obtener_registro("activos", nid)
            if nuevo is None:
                raise ValueError("Uno de los equipos a entregar no existe.")
            if estado_operativo(nuevo) != "disponible":
                raise ValueError(
                    f"El equipo {nuevo.get('codigo')} ya no está disponible. "
                    "Recarga la página e intenta de nuevo."
                )
            nuevos.append(nuevo)
    except Exception as error:
        print(f"Error validando cambio de equipo: {error}")
        flash(str(error), "danger")
        return redirect(url_for("asignaciones"))

    # ---------- 2) Ejecutar ----------
    try:
        for d in devueltos:
            supabase.table("activos").update({
                "disponibilidad": d["estado_devolucion"],
                "asignado_a": None,
                "area": None,
                "observaciones": observacion or None,
            }).eq("id", d["id"]).execute()

            # Con persona_id: asi la devolucion aparece en el perfil de la persona
            registrar_movimiento(d["id"], persona_id, "Devolucion", observacion)
            registrar_historial(
                d["id"],
                persona_id=persona_id,
                accion="Devolucion",
                detalle=(
                    f"Activo {d.get('codigo')} devuelto por {nombre_persona}. "
                    f"Nuevo estado: {d['estado_devolucion']}."
                ),
            )

        codigos_devueltos = ", ".join(str(d.get("codigo")) for d in devueltos)
        for nuevo in nuevos:
            supabase.table("activos").update({
                "disponibilidad": "Asignado",
                "asignado_a": nombre_persona,
                "area": persona.get("area"),
                "observaciones": observacion or None,
            }).eq("id", nuevo["id"]).execute()

            registrar_movimiento(nuevo["id"], persona_id, "Asignacion", observacion)
            detalle = f"Activo {nuevo.get('codigo')} asignado a {nombre_persona}."
            if devueltos:
                detalle += f" En cambio de: {codigos_devueltos}."
            registrar_historial(
                nuevo["id"],
                persona_id=persona_id,
                accion="Asignacion",
                detalle=detalle,
            )
    except Exception as error:
        print(f"Error ejecutando cambio de equipo: {error}")
        flash(
            "Ocurrió un error y el proceso pudo quedar incompleto. "
            f"Revisa el historial de los equipos. Detalle: {error}",
            "danger",
        )
        return redirect(url_for("asignaciones"))

    # ---------- 3) Acta ----------
    archivo = None
    if quiere_acta:
        if not current_user.tiene_permiso("actas", "crear"):
            flash("El proceso se registró, pero no tienes permiso para generar actas.", "warning")
        else:
            try:
                if devueltos and nuevos:
                    tipo, lista, entregados = "Cambio", devueltos, nuevos
                elif devueltos:
                    tipo, lista, entregados = "Devolucion", devueltos, None
                else:
                    tipo, lista, entregados = "Entrega", nuevos, None

                _, archivo = generar_acta_pdf(
                    persona=persona,
                    activos=lista,
                    tipo=tipo,
                    observaciones=observacion,
                    config=obtener_config(usar_cache=True),
                    activos_entregados=entregados,
                )

                supabase.table("actas").insert({
                    "persona_id": persona_id,
                    "tipo": tipo,
                    "ruta_pdf": archivo,
                    "observaciones": observacion or None,
                }).execute()
            except Exception as error:
                print(f"Error generando acta del cambio: {error}")
                flash(
                    "El proceso se registró, pero no fue posible generar el acta: "
                    f"{error}. Puedes generarla desde el módulo Actas.",
                    "warning",
                )
                archivo = None

    # Mensaje final
    codigos_nuevos = ", ".join(str(n.get("codigo")) for n in nuevos)
    if devueltos and nuevos:
        flash(
            f"Cambio registrado para {nombre_persona}: "
            f"{len(devueltos)} devuelto(s) y {len(nuevos)} entregado(s) "
            f"({codigos_nuevos}).",
            "success",
        )
    elif devueltos:
        flash(
            f"Devolución registrada para {nombre_persona}: {len(devueltos)} equipo(s).",
            "success",
        )
    else:
        flash(
            f"{codigos_nuevos} asignado(s) correctamente a {nombre_persona}.",
            "success",
        )

    if archivo:
        return redirect(url_for("asignaciones", acta=archivo))
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

    if not observacion:
        flash("La observación es obligatoria.", "danger")
        return redirect(url_for("devoluciones"))

    try:
        activo = obtener_registro("activos", activo_id)
        if activo is None:
            raise ValueError("El activo seleccionado no existe.")
        if estado_operativo(activo) != "asignado":
            raise ValueError("El activo seleccionado no esta asignado.")

        codigo_activo = activo.get("codigo")
        persona_anterior = activo.get("asignado_a")

        # Se busca la persona ANTES de limpiar asignado_a, para que la
        # devolucion quede en su perfil
        persona_anterior_id = _persona_id_por_nombre(persona_anterior)

        supabase.table("activos").update({
            "disponibilidad": estado_destino,
            "asignado_a": None,
            "area": None,
            "observaciones": observacion or None,
        }).eq("id", activo_id).execute()

        registrar_movimiento(
            activo_id,
            persona_anterior_id,
            "Devolucion",
            observacion,
        )
        registrar_historial(
            activo_id,
            persona_id=persona_anterior_id,
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
@login_required
@permiso_requerido("tickets", "ver")
def tickets():
    lista_tickets = consultar_tabla("tickets", "*", "id")
    lista_tickets.sort(key=lambda t: t.get("id") or 0, reverse=True)
    activos = consultar_tabla("activos", "*", "codigo")

    # Solo personas activas pueden aparecer en el selector
    nombres_activos = {
        str(p.get("nombre") or "").strip()
        for p in consultar_tabla("personas", "*", "nombre")
        if _persona_activa(p)
    }

    equipos = []
    for x in activos:
        estado = estado_operativo(x)
        if "baja" in estado:
            continue

        persona = str(x.get("asignado_a") or "").strip()

        # Si no está asignado, ignora cualquier nombre que haya quedado pegado
        if estado != "asignado":
            persona = ""
        # Si está asignado a alguien inactivo o inexistente, no se ofrece
        elif persona not in nombres_activos:
            continue

        equipos.append({
            "id": x["id"],
            "codigo": x.get("codigo"),
            "tipo": x.get("tipo"),
            "detalle": " ".join(
                str(v) for v in (x.get("marca"), x.get("modelo")) if v
            ),
            "persona": persona,
        })

    return render_template(
        "tickets.html",
        tickets=lista_tickets,
        activos=activos,
        equipos=equipos,
    )

@app.route("/ticket/crear", methods=["POST"])
@app.route("/tickets/crear", methods=["POST"])
@login_required
@permiso_requerido("tickets", "crear")
def crear_ticket_web():
    activo_id = request.form.get("activo_id", type=int)
    titulo = request.form.get("titulo", "").strip()
    descripcion = request.form.get("descripcion", "").strip()

    if not activo_id or not titulo or not descripcion:
        flash("El activo, el titulo y la descripcion son obligatorios.", "danger")
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


ESTADOS_TICKET = {
    "Pendiente",
    "En proceso",
    "Esperando repuesto",
    "Solucionado",
}


@app.route("/tickets/<int:ticket_id>/estado", methods=["POST"])
@login_required
@permiso_requerido("tickets", "editar")
def cambiar_estado_ticket_web(ticket_id):
    estado = request.form.get("estado", "").strip()

    if estado not in ESTADOS_TICKET:
        flash("El estado del ticket no es valido.", "danger")
        return redirect(url_for("tickets"))

    try:
        if obtener_registro("tickets", ticket_id) is None:
            raise ValueError("El ticket no existe.")

        supabase.table("tickets").update({
            "estado": estado,
        }).eq("id", ticket_id).execute()

        # Opcional: guarda la fecha de solucion si la columna existe
        # (ver SQL en las notas). Si no existe, se ignora sin afectar.
        try:
            supabase.table("tickets").update({
                "fecha_solucion": (
                    datetime.now(timezone.utc).isoformat()
                    if estado == "Solucionado" else None
                ),
            }).eq("id", ticket_id).execute()
        except Exception:
            pass

        if estado == "Solucionado":
            flash(f"Ticket #{ticket_id} marcado como solucionado.", "success")
        else:
            flash(f"Ticket #{ticket_id} actualizado a {estado}.", "success")
    except Exception as error:
        print(f"Error actualizando ticket: {error}")
        flash(str(error), "danger")

    return redirect(url_for("tickets"))


# ==================================================
# ACTAS PDF
# ==================================================

TIPOS_CON_SELECCION = {"Devolucion", "Cambio"}


@app.route("/actas")
@login_required
@permiso_requerido("actas", "ver")
def actas():
    lista_actas = consultar_tabla("actas", "*", "id")
    lista_personas = consultar_tabla("personas", "*", "nombre")

    # Equipos asignados por persona (para elegir en Devolucion / Cambio)
    id_por_nombre = {
        str(p.get("nombre") or "").strip(): p["id"] for p in lista_personas
    }
    equipos = {}
    for a in consultar_tabla("activos", "*", "codigo"):
        if estado_operativo(a) != "asignado":
            continue
        persona_id = id_por_nombre.get(str(a.get("asignado_a") or "").strip())
        if persona_id is None:
            continue
        equipos.setdefault(persona_id, []).append({
            "id": a["id"],
            "codigo": a.get("codigo"),
            "tipo": a.get("tipo"),
            "detalle": " ".join(
                str(x) for x in (a.get("marca"), a.get("modelo")) if x
            ),
            "serial": a.get("serial"),
        })

    nombres = {p["id"]: p.get("nombre") for p in lista_personas}
    for acta in lista_actas:
        acta["persona_nombre"] = nombres.get(acta.get("persona_id"))
        acta["fecha_local"] = (
            _fecha_local(acta["created_at"]) if acta.get("created_at") else ""
        )

    return render_template(
        "actas.html",
        actas=lista_actas,
        personas=lista_personas,
        equipos=equipos,
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

        # Devolucion y Cambio: solo los equipos seleccionados
        if tipo in TIPOS_CON_SELECCION:
            ids = set(request.form.getlist("activo_ids", type=int))
            if not ids:
                raise ValueError("Selecciona al menos un equipo.")
            activos_persona = [
                a for a in activos_persona if a.get("id") in ids
            ]
            if not activos_persona:
                raise ValueError(
                    "Los equipos seleccionados no pertenecen a la persona."
                )

        ruta_pdf, archivo_pdf = generar_acta_pdf(
            persona=persona,
            activos=activos_persona,
            tipo=tipo,
            observaciones=observaciones,
            config=obtener_config(usar_cache=True),
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
# REPORTES
# --------------------------------------------------
# Movido a routes/reportes_routes.py (se registra arriba
# con reportes_routes.registrar_rutas(app)).
# ==================================================


# --------------------------------------------------
# CONFIGURACION
# (CONFIG_DEFECTO y obtener_config viven en services/config_service.py)
# --------------------------------------------------

@app.route("/configuracion", methods=["GET", "POST"])
@login_required
@permiso_requerido("configuracion", "ver")
def configuracion():
    cfg = obtener_config()
    errores = {}

    if request.method == "POST":
        if not current_user.tiene_permiso("configuracion", "editar"):
            abort(403)

        nombre = request.form.get("nombre_empresa", "").strip()
        correo = request.form.get("correo_soporte", "").strip().lower()
        exigir_serial = request.form.get("exigir_serial") == "on"
        texto_clausula = request.form.get("texto_clausula", "").strip()

        def entero(campo, minimo, maximo):
            txt = request.form.get(campo, "").strip()
            try:
                valor = int(txt)
                if not minimo <= valor <= maximo:
                    raise ValueError
                return valor
            except ValueError:
                errores[campo] = f"Debe ser un número entre {minimo} y {maximo}."
                return txt

        dias = entero("dias_alerta_garantia", 1, 365)
        sesion = entero("sesion_minutos", 5, 1440)

        if not nombre:
            errores["nombre_empresa"] = "El nombre es obligatorio."
        if correo and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", correo):
            errores["correo_soporte"] = "Correo inválido."

        cfg.update({
            "nombre_empresa": nombre,
            "correo_soporte": correo,
            "dias_alerta_garantia": dias,
            "sesion_minutos": sesion,
            "exigir_serial": exigir_serial,
            "texto_clausula": texto_clausula,
        })

        if not errores:
            try:
                supabase.table("configuracion").upsert({
                    "id": 1,
                    "nombre_empresa": nombre,
                    "correo_soporte": correo or None,
                    "dias_alerta_garantia": dias,
                    "sesion_minutos": sesion,
                    "exigir_serial": exigir_serial,
                    "texto_clausula": texto_clausula or None,
                }).execute()
                limpiar_cache_config()  # fuerza releer
                flash("Configuración guardada correctamente.", "success")
                return redirect(url_for("configuracion"))
            except Exception as error:
                print(f"Error guardando configuracion: {error}")
                flash(f"No fue posible guardar: {error}", "danger")

    return render_template("configuracion.html", cfg=cfg, errores=errores)


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
        limpiar_cache_usuarios(usuario_id)
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

            limpiar_cache_usuarios()
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

@app.errorhandler(CSRFError)
def error_csrf(error):
    flash(
        "El formulario expiró o no es válido. Intenta de nuevo.",
        "danger",
    )
    return redirect(request.referrer or url_for("login"))


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