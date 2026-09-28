from supabase_client import supabase

from services.asignaciones_service import obtener_activo

from services.historial_service import (
    registrar_historial,
    registrar_movimiento
)


ESTADOS_DEVOLUCION_PERMITIDOS = {
    "Disponible",
    "En reparación",
    "Dado de baja"
}


def obtener_activos_asignados():
    """
    Obtiene todos los activos cuya disponibilidad
    actual sea Asignado.
    """

    respuesta = (
        supabase
        .table("activos")
        .select("*")
        .eq("disponibilidad", "Asignado")
        .order("codigo")
        .execute()
    )

    return respuesta.data or []


def devolver_activo(
    activo_id,
    estado_destino,
    observacion=None
):
    """
    Devuelve un activo asignado.

    El activo puede quedar como:
    - Disponible
    - En reparación
    - Dado de baja
    """

    activo = obtener_activo(activo_id)

    if activo is None:
        raise ValueError(
            "El activo seleccionado no existe."
        )

    disponibilidad_actual = str(
        activo.get("disponibilidad") or ""
    ).strip().lower()

    if disponibilidad_actual != "asignado":
        raise ValueError(
            "El activo seleccionado no está asignado."
        )

    if estado_destino not in ESTADOS_DEVOLUCION_PERMITIDOS:
        raise ValueError(
            "El estado de devolución seleccionado no es válido."
        )

    persona_anterior = activo.get("asignado_a")
    codigo_activo = activo.get("codigo")

    datos_actualizacion = {
        "disponibilidad": estado_destino,
        "asignado_a": None,
        "area": None,
        "observaciones": observacion or None
    }

    (
        supabase
        .table("activos")
        .update(datos_actualizacion)
        .eq("id", activo_id)
        .execute()
    )

    detalle = (
        f"El activo {codigo_activo} fue devuelto por "
        f"{persona_anterior or 'una persona sin identificar'}. "
        f"Nuevo estado: {estado_destino}."
    )

    registrar_movimiento(
        activo_id=activo_id,
        persona_id=None,
        accion="Devolución",
        observacion=observacion
    )

    registrar_historial(
        activo_id=activo_id,
        persona_id=None,
        accion="Devolución",
        detalle=detalle
    )

    return {
        "activo_id": activo_id,
        "codigo": codigo_activo,
        "persona_anterior": persona_anterior,
        "estado_destino": estado_destino,
        "observacion": observacion,
        "detalle": detalle
    }
