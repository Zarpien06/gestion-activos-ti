import requests

from configuracion import API_URL, TIMEOUT, TOKEN, VERSION


def enviar(datos):
    if not TOKEN:
        raise RuntimeError(
            "Falta el token. Define INVENTARIO_RECOLECTOR_TOKEN "
            "o RECOLECTOR_TOKEN en el archivo .env."
        )

    datos = dict(datos)
    datos["version_recolector"] = VERSION
    url = f"{API_URL.rstrip('/')}/api/recolector/equipos"

    try:
        respuesta = requests.post(
            url,
            json=datos,
            headers={"Authorization": f"Bearer {TOKEN}"},
            timeout=TIMEOUT,
        )
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            f"No se pudo conectar con el servidor en {API_URL}. "
            "Verifica que Flask este corriendo."
        )
    except requests.exceptions.Timeout:
        raise RuntimeError("El servidor tardo demasiado en responder.")

    if not respuesta.ok:
        try:
            detalle = respuesta.json().get("error", respuesta.text)
        except ValueError:
            detalle = respuesta.text
        raise RuntimeError(f"Error {respuesta.status_code}: {detalle}")

    return respuesta.json()