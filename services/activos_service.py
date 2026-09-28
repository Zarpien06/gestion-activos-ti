from supabase_client import supabase


def obtener_activos():

    return (
        supabase
        .table("activos")
        .select("*")
        .execute()
        .data
    )


def obtener_activo(id_activo):

    resultado = (
        supabase
        .table("activos")
        .select("*")
        .eq("id", id_activo)
        .single()
        .execute()
    )

    return resultado.data