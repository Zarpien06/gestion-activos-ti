from supabase_client import supabase


def registrar_historial(
    activo_id,
    persona_id,
    accion,
    detalle
):
    datos = {
        "activo_id": activo_id,
        "persona_id": persona_id,
        "accion": accion,
        "detalle": detalle
    }

    respuesta = (
        supabase
        .table("historial")
        .insert(datos)
        .execute()
    )

    return respuesta.data


def registrar_movimiento(
    activo_id,
    persona_id,
    accion,
    observacion=None
):
    datos = {
        "activo_id": activo_id,
        "persona_id": persona_id,
        "accion": accion,
        "observacion": observacion
    }

    respuesta = (
        supabase
        .table("movimientos")
        .insert(datos)
        .execute()
    )

    return respuesta.data
