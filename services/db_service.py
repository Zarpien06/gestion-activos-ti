import time
from datetime import datetime, timedelta, timezone

from supabase_client import supabase


def consultar_tabla(nombre_tabla, columnas="*", ordenar_por=None):
    # Reintenta una vez: Supabase a veces corta la conexion por inactividad
    for intento in range(2):
        try:
            consulta = supabase.table(nombre_tabla).select(columnas)
            if ordenar_por:
                consulta = consulta.order(ordenar_por)
            return consulta.execute().data or []
        except Exception as error:
            print(f"Error consultando {nombre_tabla} (intento {intento + 1}): {error}")
            time.sleep(0.3)
    return []


def consultar_recientes(nombre_tabla, limite=8):
    try:
        resultado = (
            supabase.table(nombre_tabla)
            .select("*")
            .order("id", desc=True)
            .limit(limite)
            .execute()
        )
        return resultado.data or []
    except Exception as error:
        print(f"Error consultando recientes de {nombre_tabla}: {error}")
        return []


def obtener_registro(nombre_tabla, registro_id):
    resultado = (
        supabase.table(nombre_tabla)
        .select("*")
        .eq("id", registro_id)
        .limit(1)
        .execute()
    )
    return resultado.data[0] if resultado.data else None


def estado_operativo(activo):
    disponibilidad = str(activo.get("disponibilidad") or "").strip().lower()
    estado = str(activo.get("estado") or "").strip().lower()
    return disponibilidad or estado


def fecha_local(valor, con_anio=False):
    """ISO en UTC -> '02/10 15:30' (o '02/10/2026 15:30') en hora Colombia."""
    formato = "%d/%m/%Y %H:%M" if con_anio else "%d/%m %H:%M"
    try:
        dt = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt - timedelta(hours=5)).strftime(formato)
    except (TypeError, ValueError):
        return str(valor or "")[:16].replace("T", " ")


def fecha_larga(valor):
    return fecha_local(valor, con_anio=True)


def fecha_corta(valor):
    """'2026-10-02...' -> '02/10/2026' (sin conversion de zona horaria)."""
    try:
        return datetime.strptime(str(valor or "")[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return ""


def hoy_colombia():
    return (datetime.now(timezone.utc) - timedelta(hours=5)).date()