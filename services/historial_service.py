
from flask import has_request_context
from flask_login import current_user

from supabase_client import supabase


def registrar_movimiento(
    activo_id,
    persona_id,
    accion,
    observacion=None
):
    """
    Registra un movimiento del activo.

    Parámetros:
        activo_id: ID del activo.
        persona_id: ID de la persona relacionada.
        accion: Acción realizada.
        observacion: Observación opcional.

    Retorna:
        Los datos insertados en Supabase.
    """

    datos = {
        "activo_id": activo_id,
        "persona_id": persona_id,
        "accion": accion,
        "observacion": observacion or None,
    }

    respuesta = (
        supabase
        .table("movimientos")
        .insert(datos)
        .execute()
    )

    return respuesta.data


def registrar_historial(
    activo_id,
    persona_id=None,
    accion=None,
    detalle=None
):
    """
    Registra una acción en el historial.

    También guarda automáticamente el usuario autenticado
    cuando la función se ejecuta dentro de una petición Flask.

    Parámetros:
        activo_id: ID del activo. Puede ser None para eventos de personas.
        persona_id: ID de la persona relacionada. Opcional.
        accion: Acción realizada.
        detalle: Descripción detallada de la acción.

    Retorna:
        Los datos insertados en Supabase.
    """

    registro = {
        "activo_id": activo_id,
        "accion": accion,
        "detalle": detalle,
    }

    # Registrar usuario autenticado cuando exista contexto Flask
    if has_request_context() and current_user.is_authenticated:
        try:
            registro["usuario_id"] = int(current_user.id)
        except (ValueError, TypeError, AttributeError):
            pass

        try:
            registro["usuario_nombre"] = current_user.nombre
        except AttributeError:
            pass

    try:
        respuesta = (
            supabase
            .table("historial")
            .insert(registro)
            .execute()
        )

        return respuesta.data

    except Exception as error:
        # Compatibilidad si todavía no existen
        # las columnas usuario_id / usuario_nombre.
        if "usuario_" in str(error):
            registro.pop("usuario_id", None)
            registro.pop("usuario_nombre", None)

            respuesta = (
                supabase
                .table("historial")
                .insert(registro)
                .execute()
            )

            return respuesta.data

        raise


def registrar_historial_persona(
    accion,
    detalle,
    persona_id=None
):
    """
    Registra un evento relacionado con una persona,
    sin necesidad de asociarlo a un activo.

    Nunca debería romper el flujo principal de la aplicación.
    """

    try:
        return registrar_historial(
            activo_id=None,
            persona_id=persona_id,
            accion=accion,
            detalle=detalle
        )

    except Exception as error:
        print(f"Error registrando historial de persona: {error}")
        return None
