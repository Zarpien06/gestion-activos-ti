from supabase_client import supabase
from services.personas_service import obtener_persona
from services.historial_service import (
    registrar_historial,
    registrar_movimiento
)


def obtener_activo(activo_id):
    respuesta = (
        supabase
        .table("activos")
        .select("*")
        .eq("id", activo_id)
        .limit(1)
        .execute()
    )

    if not respuesta.data:
        return None

    return respuesta.data[0]


def obtener_activos_disponibles():
    respuesta = (
        supabase
        .table("activos")
        .select("*")
        .eq("disponibilidad", "Disponible")
        .order("codigo")
        .execute()
    )

    return respuesta.data or []


def asignar_activo(
    activo_id,
    persona_id,
    observacion=None
):
    activo = obtener_activo(activo_id)
    persona = obtener_persona(persona_id)

    if activo is None:
        raise ValueError("El activo seleccionado no existe.")

    if persona is None:
        raise ValueError("La persona seleccionada no existe.")

    disponibilidad = str(
        activo.get("disponibilidad") or ""
    ).strip().lower()

    if disponibilidad != "disponible":
        raise ValueError(
            "El activo ya no está disponible para asignación."
        )

    nombre_persona = persona.get("nombre")
    area_persona = persona.get("area")

    datos_actualizacion = {
        "disponibilidad": "Asignado",
        "asignado_a": nombre_persona,
        "area": area_persona,
        "observaciones": observacion
    }

    (
        supabase
        .table("activos")
        .update(datos_actualizacion)
        .eq("id", activo_id)
        .execute()
    )

    detalle = (
        f"Activo {activo.get('codigo')} asignado a "
        f"{nombre_persona}."
    )

    registrar_movimiento(
        activo_id=activo_id,
        persona_id=persona_id,
        accion="Asignación",
        observacion=observacion
    )

    registrar_historial(
        activo_id=activo_id,
        persona_id=persona_id,
        accion="Asignación",
        detalle=detalle
    )

    return {
        "activo": activo,
        "persona": persona,
        "detalle": detalle
    }