import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import has_request_context
from flask_login import current_user

from services.storage_service import BUCKET_FACTURAS
from supabase_client import supabase

TIPOS = {
    "factura": "Factura",
    "acta": "Acta",
    "manual": "Manual",
    "garantia": "Garantía",
    "otro": "Otro",
}
EXTENSIONES = {".pdf", ".jpg", ".jpeg", ".png", ".docx", ".xlsx"}
MAX_BYTES = 20 * 1024 * 1024


def _usuario():
    if has_request_context() and current_user.is_authenticated:
        return int(current_user.id), current_user.nombre
    return None, None


def subir_documento(archivo, activo_id, tipo="otro"):
    """Sube el archivo al bucket y crea su fila en documentos. Devuelve la fila."""
    if not archivo or not archivo.filename:
        return None

    nombre = Path(archivo.filename).name
    ext = Path(nombre).suffix.lower()
    if ext not in EXTENSIONES:
        raise ValueError(f"'{nombre}': formato no permitido (PDF, JPG, PNG, DOCX o XLSX).")

    contenido = archivo.read()
    if not contenido:
        raise ValueError(f"'{nombre}' está vacío.")
    if len(contenido) > MAX_BYTES:
        raise ValueError(f"'{nombre}' supera los 20 MB.")

    if tipo not in TIPOS:
        tipo = "otro"

    ruta = f"activos/{activo_id}/docs/{uuid.uuid4().hex}{ext}"
    supabase.storage.from_(BUCKET_FACTURAS).upload(
        ruta,
        contenido,
        {"content-type": archivo.mimetype or "application/octet-stream"},
    )

    uid, unombre = _usuario()
    try:
        fila = supabase.table("documentos").insert({
            "activo_id": activo_id,
            "tipo": tipo,
            "nombre_original": nombre,
            "ruta": ruta,
            "tamano_bytes": len(contenido),
            "subido_por_id": uid,
            "subido_por_nombre": unombre,
        }).execute()
    except Exception:
        # No dejar un archivo huerfano si fallo el registro
        try:
            supabase.storage.from_(BUCKET_FACTURAS).remove([ruta])
        except Exception:
            pass
        raise

    return fila.data[0]


def listar_documentos(activo_id):
    try:
        return (
            supabase.table("documentos")
            .select("*")
            .eq("activo_id", activo_id)
            .is_("eliminado_at", "null")
            .order("id", desc=True)
            .execute()
            .data
            or []
        )
    except Exception as error:
        print(f"Error listando documentos: {error}")
        return []


def obtener_documento(doc_id):
    r = (
        supabase.table("documentos")
        .select("*")
        .eq("id", doc_id)
        .is_("eliminado_at", "null")
        .limit(1)
        .execute()
    )
    return r.data[0] if r.data else None


def eliminar_documento(doc_id):
    """Borrado logico: el archivo queda en el bucket por trazabilidad."""
    doc = obtener_documento(doc_id)
    if doc is None:
        raise ValueError("El documento no existe o ya fue eliminado.")

    _, unombre = _usuario()
    supabase.table("documentos").update({
        "eliminado_at": datetime.now(timezone.utc).isoformat(),
        "eliminado_por": unombre,
    }).eq("id", doc_id).execute()
    return doc


def tamano_legible(n):
    if not n:
        return ""
    if n < 1024 * 1024:
        return f"{max(1, round(n / 1024))} KB"
    return f"{n / (1024 * 1024):.1f} MB"