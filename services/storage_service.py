import os
import uuid
from pathlib import Path

from supabase_client import supabase

BUCKET_FACTURAS = "facturas"
EXT_FACTURA = {".pdf", ".jpg", ".jpeg", ".png"}


def subir_factura(archivo, carpeta):
    """Sube un archivo al bucket privado y devuelve la ruta (o None)."""
    if not archivo or not archivo.filename:
        return None

    ext = Path(archivo.filename).suffix.lower()
    if ext not in EXT_FACTURA:
        raise ValueError("La factura debe ser PDF, JPG o PNG.")

    ruta = f"{carpeta}/{uuid.uuid4().hex}{ext}"
    supabase.storage.from_(BUCKET_FACTURAS).upload(
        ruta,
        archivo.read(),
        {"content-type": archivo.mimetype or "application/octet-stream"},
    )
    return ruta


def url_firmada(ruta, segundos=300):
    """Enlace temporal a un archivo del bucket privado."""
    firmado = supabase.storage.from_(BUCKET_FACTURAS).create_signed_url(ruta, segundos)
    url = firmado.get("signedURL") or firmado.get("signedUrl")
    if not url:
        raise ValueError("Sin URL")
    if url.startswith("/"):
        if not url.startswith("/storage/v1"):
            url = "/storage/v1" + url
        url = os.getenv("SUPABASE_URL", "").rstrip("/") + url
    return url