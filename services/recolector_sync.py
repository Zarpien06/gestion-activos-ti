from datetime import datetime, timezone
from supabase_client import supabase

CAMPOS_HARDWARE = [
    "hostname", "usuario_windows", "dominio", "uuid_equipo", "bios_serial",
    "procesador", "ram_gb", "disco_gb", "sistema_operativo",
    "direccion_ip", "direccion_mac",
]

# Seriales basura que ponen algunos fabricantes
SERIALES_INVALIDOS = {
    "", "default string", "to be filled by o.e.m.", "none",
    "system serial number", "0", "123456789",
}


def _serial_valido(valor):
    return str(valor or "").strip().lower() not in SERIALES_INVALIDOS


def buscar_activo_para_equipo(equipo):
    """Devuelve el activo que coincide por serial o uuid, o None."""
    serial = str(equipo.get("bios_serial") or "").strip()
    if _serial_valido(serial):
        for columna in ("serial", "bios_serial"):
            r = (supabase.table("activos").select("id,codigo")
                 .eq(columna, serial).limit(1).execute())
            if r.data:
                return r.data[0]

    uuid = str(equipo.get("uuid_equipo") or "").strip()
    if uuid:
        r = (supabase.table("activos").select("id,codigo")
             .eq("uuid_equipo", uuid).limit(1).execute())
        if r.data:
            return r.data[0]
    return None


def sincronizar_activo(equipo):
    """Copia el hardware del equipo a su activo vinculado.
    No toca codigo, tipo, marca, modelo ni serial (los cura una persona)."""
    activo_id = equipo.get("activo_id")
    if not activo_id:
        return False

    datos = {
        c: equipo.get(c)
        for c in CAMPOS_HARDWARE
        if equipo.get(c) not in (None, "")
    }
    datos["ultima_revision"] = datetime.now(timezone.utc).isoformat()

    supabase.table("activos").update(datos).eq("id", activo_id).execute()
    return True


def vincular_equipo(equipo_id, activo_id):
    supabase.table("recolector_equipos").update(
        {"activo_id": activo_id}
    ).eq("id", equipo_id).execute()

    r = (supabase.table("recolector_equipos").select("*")
         .eq("id", equipo_id).limit(1).execute())
    if r.data:
        sincronizar_activo(r.data[0])