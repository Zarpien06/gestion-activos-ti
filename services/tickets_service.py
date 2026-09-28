from supabase_client import supabase


def crear_ticket(
    activo_id,
    titulo,
    descripcion
):

    return (
        supabase
        .table("tickets")
        .insert({
            "activo_id": activo_id,
            "titulo": titulo,
            "descripcion": descripcion,
            "estado": "Pendiente"
        })
        .execute()
    )


def obtener_tickets():

    return (
        supabase
        .table("tickets")
        .select("*")
        .execute()
        .data
    )