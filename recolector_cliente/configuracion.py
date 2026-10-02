import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv es opcional en el cliente
    load_dotenv = None

BASE = Path(__file__).resolve().parent

# Orden de lectura: .env junto al cliente, luego .env de la raiz del proyecto.
# Las variables ya definidas en el sistema tienen prioridad.
if load_dotenv:
    load_dotenv(BASE / ".env")
    load_dotenv(BASE.parent / ".env")


def _normalizar_url(valor):
    """Limpia la URL: quita espacios, evita 'http://http://' y barra final."""
    url = (valor or "").strip()
    while url.lower().startswith(
        ("http://http://", "https://http://", "http://https://", "https://https://")
    ):
        url = url.split("://", 1)[1]
    if not url.lower().startswith(("http://", "https://")):
        url = "http://" + url
    return url.rstrip("/")


API_URL = _normalizar_url(
    os.getenv("INVENTARIO_API_URL", "http://127.0.0.1:5000")
)

# Solo NOMBRES de variables aqui. El valor del token va unicamente en el .env.
TOKEN = (
    os.getenv("INVENTARIO_RECOLECTOR_TOKEN")
    or os.getenv("RECOLECTOR_TOKEN")
    or ""
).strip()

VERSION = "1.0.0"
TIMEOUT = 30