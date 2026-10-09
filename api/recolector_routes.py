import io
import json
from pathlib import Path

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import login_required
from werkzeug.utils import secure_filename

from auth.decorators import permiso_requerido
from supabase_client import supabase
from api.recolector_service import (
    buscar_activo_con_motivo,
    crear_activo_desde_escaneo,
    obtener_escaneo,
    procesar_escaneo,
    vincular_escaneo,
)

recolector_bp = Blueprint("recolector", __name__)

CARPETA_CLIENTE = Path(__file__).resolve().parent.parent / "recolector_cliente"
ARCHIVO_RECOLECTOR = "recolector.ps1"

MAX_ARCHIVO = 256 * 1024  # 256 KB por archivo


@recolector_bp.post("/recolector/subir")
@login_required
@permiso_requerido("recolector", "crear")
def subir_archivos():
    """Recibe uno o varios NOMBRE-DEL-EQUIPO.json generados por el recolector."""
    archivos = [a for a in request.files.getlist("archivos") if a and a.filename]
    if not archivos:
        flash("Selecciona al menos un archivo.", "danger")
        return redirect(url_for("recolector.bandeja"))

    procesados, errores = [], []
    for archivo in archivos:
        nombre = secure_filename(archivo.filename) or "archivo"
        try:
            if not nombre.lower().endswith(".json"):
                raise ValueError("no es un archivo .json")

            crudo = archivo.read(MAX_ARCHIVO + 1)
            if len(crudo) > MAX_ARCHIVO:
                raise ValueError("pesa demasiado")

            # utf-8-sig: acepta el archivo con o sin BOM
            datos = json.loads(crudo.decode("utf-8-sig"))
            if not isinstance(datos, dict):
                raise ValueError("formato inválido")

            # Si el archivo no trae hostname, se usa el nombre del archivo
            if not str(datos.get("hostname") or "").strip():
                datos["hostname"] = Path(nombre).stem

            procesados.append(procesar_escaneo(datos))
        except Exception as error:
            print(f"Error procesando {nombre}: {error}")
            errores.append(f"{nombre}: {error}")

    if procesados:
        vinculados = sum(1 for r in procesados if r.get("resultado") == "vinculado")
        flash(
            f"{len(procesados)} equipo(s) procesado(s): {vinculados} vinculado(s), "
            f"{len(procesados) - vinculados} sin vincular.",
            "success",
        )
    for e in errores:
        flash(f"No se pudo procesar {e}", "danger")

    return redirect(url_for("recolector.bandeja"))


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