import io
from functools import wraps
from pathlib import Path

from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import login_required

from auth.decorators import permiso_requerido
from supabase_client import supabase
from api.recolector_service import (
    buscar_activo_con_motivo,
    crear_activo_desde_escaneo,
    obtener_escaneo,
    procesar_escaneo,
    validar_token,
    vincular_escaneo,
)

recolector_bp = Blueprint("recolector", __name__)

CARPETA_CLIENTE = Path(__file__).resolve().parent.parent / "recolector_cliente"
ARCHIVO_RECOLECTOR = "recolector.ps1"


def extraer_token():
    cabecera = request.headers.get("Authorization", "").strip()
    if cabecera.lower().startswith("bearer "):
        return cabecera[7:].strip()
    return request.headers.get("X-Recolector-Token", "").strip()


def token_requerido(funcion):
    @wraps(funcion)
    def wrapper(*args, **kwargs):
        try:
            valido = validar_token(extraer_token())
        except Exception as error:
            print(f"Error validando token del recolector: {error}")
            return jsonify({"error": "No fue posible validar el token."}), 500
        if not valido:
            return jsonify({"error": "Token invalido"}), 401
        return funcion(*args, **kwargs)
    return wrapper


@recolector_bp.post("/api/recolector/equipos")
@token_requerido
def recibir_equipo():
    datos = request.get_json(silent=True)
    if not isinstance(datos, dict):
        return jsonify({"error": "Se esperaba un JSON valido."}), 400
    if not str(datos.get("hostname") or "").strip():
        return jsonify({"error": "Falta hostname"}), 400

    try:
        resultado = procesar_escaneo(datos)
    except Exception as error:
        print(f"Error procesando escaneo: {error}")
        return jsonify({"error": "No fue posible guardar el equipo."}), 500

    resultado["mensaje"] = "Equipo recibido correctamente"
    return jsonify(resultado), 201


@recolector_bp.get("/recolector/descargar")
@login_required
@permiso_requerido("recolector", "ver")
def descargar_script():
    ruta = CARPETA_CLIENTE / ARCHIVO_RECOLECTOR
    if not ruta.is_file():
        flash(f"No se encontro el archivo {ARCHIVO_RECOLECTOR}.", "danger")
        return redirect(url_for("recolector.bandeja"))

    servidor = request.host_url.rstrip("/")
    contenido = ruta.read_bytes().replace(
        b"__SERVIDOR__", servidor.encode("utf-8")
    )

    return send_file(
        io.BytesIO(contenido),
        mimetype="text/plain",
        as_attachment=True,
        download_name=ARCHIVO_RECOLECTOR,
    )


@recolector_bp.get("/recolector")
@login_required
@permiso_requerido("recolector", "ver")
def bandeja():
    consulta = supabase.table("escaneos_equipos").select("*")
    estado = request.args.get("estado", "").strip()
    if estado in {"vinculado", "sin_vincular"}:
        consulta = consulta.eq("estado", estado)
    escaneos = (
        consulta.order("fecha_escaneo", desc=True).limit(500).execute().data or []
    )
    return render_template(
        "recolector/bandeja.html",
        escaneos=escaneos,
        estado_filtro=estado,
    )


@recolector_bp.get("/recolector/<int:escaneo_id>")
@login_required
@permiso_requerido("recolector", "ver")
def detalle(escaneo_id):
    escaneo = obtener_escaneo(escaneo_id)
    if not escaneo:
        flash("El escaneo no existe.", "danger")
        return redirect(url_for("recolector.bandeja"))

    activos = (
        supabase.table("activos")
        .select("id,codigo,tipo,marca,modelo,serial,asignado_a")
        .order("codigo").execute().data or []
    )
    personas = (
        supabase.table("personas")
        .select("id,nombre,area,cargo,estado")
        .eq("estado", "Activo").order("nombre").execute().data or []
    )
    activo_actual = None
    sugerido, motivo_sugerido = None, None
    if escaneo.get("activo_id"):
        respuesta = (
            supabase.table("activos").select("*")
            .eq("id", escaneo["activo_id"]).limit(1).execute()
        )
        activo_actual = respuesta.data[0] if respuesta.data else None
    else:
        sugerido, motivo_sugerido = buscar_activo_con_motivo(
            escaneo.get("bios_serial"),
            escaneo.get("uuid_equipo"),
            escaneo.get("hostname"),
        )

    return render_template(
        "recolector/detalle.html",
        escaneo=escaneo,
        activos=activos,
        personas=personas,
        activo_actual=activo_actual,
        sugerido=sugerido,
        motivo_sugerido=motivo_sugerido,
    )


@recolector_bp.post("/recolector/<int:escaneo_id>/vincular")
@login_required
@permiso_requerido("recolector", "editar")
def vincular(escaneo_id):
    try:
        activo_id = request.form.get("activo_id", type=int)
        persona_id = request.form.get("persona_id", type=int)
        vincular_escaneo(escaneo_id, activo_id, persona_id)
        flash("Equipo vinculado y datos tecnicos actualizados.", "success")
    except Exception as error:
        print(f"Error vinculando escaneo {escaneo_id}: {error}")
        flash(str(error), "danger")
    return redirect(url_for("recolector.detalle", escaneo_id=escaneo_id))


@recolector_bp.post("/recolector/<int:escaneo_id>/crear-activo")
@login_required
@permiso_requerido("recolector", "crear")
def crear_activo(escaneo_id):
    try:
        persona_id = request.form.get("persona_id", type=int)
        creado = crear_activo_desde_escaneo(escaneo_id, persona_id)
        flash(f"Activo {creado.get('codigo')} creado correctamente.", "success")
    except Exception as error:
        print(f"Error creando activo desde escaneo {escaneo_id}: {error}")
        flash(str(error), "danger")
    return redirect(url_for("recolector.detalle", escaneo_id=escaneo_id))