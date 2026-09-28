from supabase_client import supabase


def obtener_personas():
    respuesta = (
        supabase
        .table("personas")
        .select("*")
        .order("nombre")
        .execute()
    )

    return respuesta.data or []


def obtener_persona(persona_id):
    respuesta = (
        supabase
        .table("personas")
        .select("*")
        .eq("id", persona_id)
        .limit(1)
        .execute()
    )

    if not respuesta.data:
        return None

    return respuesta.data[0]


def crear_persona(
    nombre,
    documento=None,
    correo=None,
    cargo=None,
    area=None
):
    datos = {
        "nombre": nombre.strip(),
        "documento": documento.strip() if documento else None,
        "correo": correo.strip() if correo else None,
        "cargo": cargo.strip() if cargo else None,
        "area": area.strip() if area else None,
        "estado": "Activo"
    }

    respuesta = (
        supabase
        .table("personas")
        .insert(datos)
        .execute()
    )

    return respuesta.data


def actualizar_persona(
    persona_id,
    nombre,
    documento=None,
    correo=None,
    cargo=None,
    area=None
):
    datos = {
        "nombre": nombre.strip(),
        "documento": documento.strip() if documento else None,
        "correo": correo.strip() if correo else None,
        "cargo": cargo.strip() if cargo else None,
        "area": area.strip() if area else None
    }

    respuesta = (
        supabase
        .table("personas")
        .update(datos)
        .eq("id", persona_id)
        .execute()
    )

    return respuesta.data


def desactivar_persona(persona_id):
    respuesta = (
        supabase
        .table("personas")
        .update({"estado": "Inactivo"})
        .eq("id", persona_id)
        .execute()
    )

    return respuesta.data