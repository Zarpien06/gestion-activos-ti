from flask import flash, jsonify, redirect, request, url_for
from flask_login import login_required

from auth.decorators import permiso_requerido
from services.auditoria_service import listar_cambios
from services.db_service import fecha_larga, obtener_registro
from services.documentos_service import (
    TIPOS,
    eliminar_documento,
    listar_documentos,
    obtener_documento,
    subir_documento,
    tamano_legible,
)
from services.historial_service import registrar_historial
from services.storage_service import url_firmada


@login_required
@permiso_requerido("inventario", "ver")
def extras_activo(activo_id):
    """JSON con documentos e historial de cambios (lo carga el modal de detalle)."""
    documentos = [
        {
            "id": d["id"],
            "tipo": TIPOS.get(d["tipo"], d["tipo"]),
            "nombre": d["nombre_original"],
            "tamano": tamano_legible(d.get("tamano_bytes")),
            "subido_por": d.get("subido_por_nombre") or "—",
            "fecha": fecha_larga(d.get("created_at")),
            "url": url_for("abrir_documento", doc_id=d["id"]),
        }
        for d in listar_documentos(activo_id)
    ]
    return jsonify({"documentos": documentos, "cambios": listar_cambios(activo_id)})


@login_required
@permiso_requerido("inventario", "ver")
def abrir_documento(doc_id):
    try:
        doc = obtener_documento(doc_id)
        if doc is None:
            raise ValueError("Documento no encontrado.")
        return redirect(url_firmada(doc["ruta"], 300))
    except Exception as error:
        print(f"Error abriendo documento {doc_id}: {type(error).__name__}: {error}")
        flash(f"No fue posible abrir el documento ({type(error).__name__}).", "danger")
        return redirect(url_for("inventario"))


@login_required
@permiso_requerido("inventario", "editar")
def subir_documentos(activo_id):
    activo = obtener_registro("activos", activo_id)
    if activo is None:
        return jsonify({"ok": False, "error": "El activo no existe."}), 404

    archivos = [a for a in request.files.getlist("archivos") if a and a.filename]
    if not archivos:
        return jsonify({"ok": False, "error": "Selecciona al menos un archivo."}), 400

    tipo = request.form.get("tipo", "otro")
    subidos, errores = [], []

    for archivo in archivos:
        try:
            doc = subir_documento(archivo, activo_id, tipo)
            subidos.append(doc["nombre_original"])
            registrar_historial(
                activo_id,
                "Documento",
                f"Activo {activo.get('codigo')}: documento '{doc['nombre_original']}' "
                f"({TIPOS.get(doc['tipo'], doc['tipo'])}) agregado.",
            )
        except Exception as error:
            print(f"Error subiendo documento: {error}")
            errores.append(str(error))

    return jsonify({"ok": bool(subidos), "subidos": subidos, "errores": errores})


@login_required
@permiso_requerido("inventario", "eliminar")
def eliminar_documento_web(doc_id):
    try:
        doc = eliminar_documento(doc_id)
        activo = obtener_registro("activos", doc["activo_id"]) or {}
        registrar_historial(
            doc["activo_id"],
            "Documento",
            f"Activo {activo.get('codigo')}: documento '{doc['nombre_original']}' eliminado.",
        )
        return jsonify({"ok": True})
    except Exception as error:
        print(f"Error eliminando documento {doc_id}: {error}")
        return jsonify({"ok": False, "error": str(error)}), 400


def registrar_rutas(app):
    reglas = [
        ("/inventario/<int:activo_id>/extras.json", "extras_activo", extras_activo, ["GET"]),
        ("/documentos/<int:doc_id>/abrir", "abrir_documento", abrir_documento, ["GET"]),
        ("/inventario/<int:activo_id>/documentos", "subir_documentos", subir_documentos, ["POST"]),
        ("/documentos/<int:doc_id>/eliminar", "eliminar_documento_web", eliminar_documento_web, ["POST"]),
    ]
    for ruta, endpoint, vista, metodos in reglas:
        app.add_url_rule(ruta, endpoint=endpoint, view_func=vista, methods=metodos)