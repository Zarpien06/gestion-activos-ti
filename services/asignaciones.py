from supabase_client import supabase


def asignar_activo(activo_id, persona_id):

    persona = (
        supabase
        .table("personas")
        .select("*")
        .eq("id", persona_id)
        .single()
        .execute()
    )

    nombre = persona.data["nombre"]

    supabase.table("activos").update({
        "estado": "Asignado",
        "asignado_a": nombre
    }).eq("id", activo_id).execute()

    supabase.table("historial").insert({
        "activo_id": activo_id,
        "persona_id": persona_id,
        "accion": "Asignación",
        "detalle": f"Activo asignado a {nombre}"
    }).execute()