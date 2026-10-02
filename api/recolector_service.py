from datetime import datetime, timezone
import hashlib
import re

from supabase_client import supabase


SERIALES_INVALIDOS = {
    "",
    "none",
    "null",
    "default string",
    "system serial number",
    "to be filled by o.e.m.",
    "to be filled by oem",
    "not specified",
    "0",
    "00000000",
    "123456789",
    "ffffffff-ffff-ffff-ffff-ffffffffffff",
    "00000000-0000-0000-0000-000000000000",
}

MODELOS_PORTATIL = (
    "laptop", "notebook", "latitude", "thinkpad", "elitebook",
    "probook", "macbook", "zenbook", "vivobook",
)


# ==================================================
# UTILIDADES
# ==================================================

def ahora_iso():
    return datetime.now(timezone.utc).isoformat()


def texto_limpio(valor, maximo=255):
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto[:maximo] if texto else None


def numero_o_none(valor):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def serial_util(valor):
    texto = texto_limpio(valor, 100)
    if texto is None or texto.lower() in SERIALES_INVALIDOS:
        return None
    return texto


def sin_nulos(datos):
    return {clave: valor for clave, valor in datos.items() if valor is not None}


# ==================================================
# TOKENS
# ==================================================

def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validar_token(token):
    if not token:
        return None
    respuesta = (
        supabase.table("tokens_recolector").select("*")
        .eq("token_hash", hash_token(token)).eq("activo", True)
        .limit(1).execute()
    )
    if not respuesta.data:
        return None
    fila = respuesta.data[0]
    supabase.table("tokens_recolector").update({
        "ultimo_uso": ahora_iso()
    }).eq("id", fila["id"]).execute()
    return fila


# ==================================================
# CONSULTAS Y AUXILIARES
# ==================================================

def obtener_escaneo(escaneo_id):
    respuesta = (
        supabase.table("escaneos_equipos").select("*")
        .eq("id", escaneo_id).limit(1).execute()
    )
    return respuesta.data[0] if respuesta.data else None


def datos_tecnicos_desde_escaneo(escaneo):
    return {
        "hostname": escaneo.get("hostname"),
        "usuario_windows": escaneo.get("usuario_windows"),
        "bios_serial": escaneo.get("bios_serial"),
        "uuid_equipo": escaneo.get("uuid_equipo"),
        "marca": escaneo.get("marca"),
        "modelo": escaneo.get("modelo"),
        "procesador": escaneo.get("procesador"),
        "ram_gb": escaneo.get("ram_gb"),
        "disco_gb": escaneo.get("disco_gb"),
        "sistema_operativo": escaneo.get("sistema_operativo"),
        "direccion_ip": escaneo.get("direccion_ip"),
        "direccion_mac": escaneo.get("direccion_mac"),
        "dominio": escaneo.get("dominio"),
        "ultima_revision": ahora_iso(),
    }


def datos_para_activo(escaneo, activo):
    """Hardware siempre se actualiza; marca/modelo solo si el activo los tiene vacios."""
    datos = sin_nulos(datos_tecnicos_desde_escaneo(escaneo))
    for campo in ("marca", "modelo"):
        datos.pop(campo, None)
        if not activo.get(campo) and escaneo.get(campo):
            datos[campo] = escaneo[campo]
    return datos


def siguiente_codigo_activo():
    """Busca el mayor ACT-### paginando (Supabase limita a 1000 filas)."""
    mayor = 0
    inicio = 0
    paso = 1000
    while True:
        respuesta = (
            supabase.table("activos").select("codigo")
            .order("id").range(inicio, inicio + paso - 1).execute()
        )
        filas = respuesta.data or []
        for fila in filas:
            coincidencia = re.fullmatch(
                r"ACT-(\d+)", str(fila.get("codigo") or "").strip().upper()
            )
            if coincidencia:
                mayor = max(mayor, int(coincidencia.group(1)))
        if len(filas) < paso:
            break
        inicio += paso
    return f"ACT-{mayor + 1:03d}"


def tipo_desde_modelo(modelo):
    texto = str(modelo or "").lower()
    if any(clave in texto for clave in MODELOS_PORTATIL):
        return "Laptop"
    return "Computador"


def datos_asignacion(persona_id):
    respuesta = (
        supabase.table("personas").select("*")
        .eq("id", persona_id).limit(1).execute()
    )
    if not respuesta.data:
        raise ValueError("La persona seleccionada no existe.")
    persona = respuesta.data[0]
    if str(persona.get("estado") or "Activo").strip().lower() != "activo":
        raise ValueError("La persona seleccionada esta inactiva.")
    return {
        "persona_id": persona_id,
        "asignado_a": persona.get("nombre"),
        "area": persona.get("area"),
        "disponibilidad": "Asignado",
    }


def registrar_historial(activo_id, accion, detalle):
    supabase.table("historial").insert({
        "activo_id": activo_id,
        "accion": accion,
        "detalle": detalle,
    }).execute()


def registrar_movimiento(activo_id, persona_id, observacion):
    supabase.table("movimientos").insert({
        "activo_id": activo_id,
        "persona_id": persona_id,
        "accion": "Asignacion",
        "observacion": observacion,
    }).execute()


# ==================================================
# VINCULAR / CREAR
# ==================================================

def vincular_escaneo(escaneo_id, activo_id, persona_id=None):
    if not activo_id:
        raise ValueError("Selecciona un activo para vincular.")

    escaneo = obtener_escaneo(escaneo_id)
    if not escaneo:
        raise ValueError("El escaneo no existe.")

    respuesta = (
        supabase.table("activos").select("*")
        .eq("id", activo_id).limit(1).execute()
    )
    if not respuesta.data:
        raise ValueError("El activo no existe.")
    activo = respuesta.data[0]

    # No pisa marca/modelo cargados por una persona.
    datos = datos_para_activo(escaneo, activo)
    if not activo.get("serial") and serial_util(escaneo.get("bios_serial")):
        datos["serial"] = escaneo["bios_serial"]

    if persona_id:
        datos.update(datos_asignacion(persona_id))

    supabase.table("activos").update(datos).eq("id", activo_id).execute()
    supabase.table("escaneos_equipos").update({
        "activo_id": activo_id,
        "estado": "vinculado",
    }).eq("id", escaneo_id).execute()

    registrar_historial(
        activo_id,
        "Vinculacion de recolector",
        f"Escaneo {escaneo_id} vinculado al activo {activo.get('codigo')}.",
    )
    if persona_id:
        registrar_movimiento(
            activo_id, persona_id, "Asignado al vincular desde recolector."
        )
    return activo_id


def crear_activo_desde_escaneo(escaneo_id, persona_id=None):
    escaneo = obtener_escaneo(escaneo_id)
    if not escaneo:
        raise ValueError("El escaneo no existe.")
    if escaneo.get("activo_id"):
        raise ValueError("El escaneo ya esta vinculado a un activo.")

    datos = datos_tecnicos_desde_escaneo(escaneo)
    datos.update({
        "tipo": tipo_desde_modelo(escaneo.get("modelo")),
        "serial": serial_util(escaneo.get("bios_serial")),
        "estado": "Bueno",
        "disponibilidad": "Disponible",
        "observaciones": "Creado desde el recolector de inventario.",
    })
    if persona_id:
        datos.update(datos_asignacion(persona_id))

    creado = None
    ultimo_error = None
    for _ in range(3):  # reintenta si otro usuario tomo el mismo codigo
        datos["codigo"] = siguiente_codigo_activo()
        try:
            creado = supabase.table("activos").insert(datos).execute().data[0]
            break
        except Exception as error:
            ultimo_error = error
            texto = str(error).lower()
            if "duplicate" not in texto and "23505" not in texto:
                raise
    if creado is None:
        raise ultimo_error

    supabase.table("escaneos_equipos").update({
        "activo_id": creado["id"],
        "estado": "vinculado",
    }).eq("id", escaneo_id).execute()

    registrar_historial(
        creado["id"],
        "Creacion desde recolector",
        f"Activo {creado.get('codigo')} creado desde el escaneo {escaneo_id}.",
    )
    if persona_id:
        registrar_movimiento(
            creado["id"], persona_id, "Asignado al crear desde recolector."
        )
    return creado


# ==================================================
# PROCESAR ESCANEO ENTRANTE
# ==================================================

def buscar_activo_con_motivo(serial, uuid, hostname=None):
    """Devuelve (activo, motivo). El hostname solo se usa si se pasa explicitamente."""
    candidatos = [
        ("bios_serial", serial, "serial"),
        ("serial", serial, "serial"),
        ("uuid_equipo", uuid, "UUID"),
        ("hostname", hostname, "hostname (revisar, puede ser otro equipo)"),
    ]
    for campo, valor, motivo in candidatos:
        if not valor:
            continue
        respuesta = (
            supabase.table("activos").select("*")
            .eq(campo, valor).limit(1).execute()
        )
        if respuesta.data:
            return respuesta.data[0], motivo
    return None, None


def buscar_activo(serial, uuid, hostname=None):
    """Para el vinculo automatico: nunca usa hostname."""
    activo, _ = buscar_activo_con_motivo(serial, uuid, None)
    return activo


def buscar_escaneo_existente(serial, uuid, hostname):
    """Evita una fila nueva por cada ejecucion del script en el mismo equipo."""
    candidatos = [("uuid_equipo", uuid), ("bios_serial", serial)]
    if not uuid and not serial:
        candidatos.append(("hostname", hostname))  # ultimo recurso
    for campo, valor in candidatos:
        if not valor:
            continue
        respuesta = (
            supabase.table("escaneos_equipos").select("id,activo_id")
            .eq(campo, valor).order("id", desc=True).limit(1).execute()
        )
        if respuesta.data:
            return respuesta.data[0]
    return None


def procesar_escaneo(datos):
    serial = serial_util(datos.get("bios_serial") or datos.get("serial"))
    uuid = serial_util(datos.get("uuid_equipo"))
    hostname = texto_limpio(datos.get("hostname"))

    existente = buscar_escaneo_existente(serial, uuid, hostname)

    # Si ya se vinculo a mano, se respeta ese vinculo.
    activo = None
    if existente and existente.get("activo_id"):
        respuesta = (
            supabase.table("activos").select("*")
            .eq("id", existente["activo_id"]).limit(1).execute()
        )
        activo = respuesta.data[0] if respuesta.data else None
    if activo is None:
        activo = buscar_activo(serial, uuid)

    estado = "vinculado" if activo else "sin_vincular"
    escaneo = {
        "activo_id": activo.get("id") if activo else None,
        "hostname": hostname,
        "usuario_windows": texto_limpio(datos.get("usuario_windows")),
        "bios_serial": serial,
        "uuid_equipo": uuid,
        "marca": texto_limpio(datos.get("marca")),
        "modelo": texto_limpio(datos.get("modelo")),
        "procesador": texto_limpio(datos.get("procesador")),
        "ram_gb": numero_o_none(datos.get("ram_gb")),
        "disco_gb": numero_o_none(datos.get("disco_gb")),
        "sistema_operativo": texto_limpio(datos.get("sistema_operativo")),
        "direccion_ip": texto_limpio(datos.get("direccion_ip"), 64),
        "direccion_mac": texto_limpio(datos.get("direccion_mac"), 64),
        "dominio": texto_limpio(datos.get("dominio")),
        "version_recolector": texto_limpio(datos.get("version_recolector"), 32),
        "estado": estado,
        "datos_json": datos,
        "fecha_escaneo": ahora_iso(),
    }

    if existente:
        supabase.table("escaneos_equipos").update(escaneo) \
            .eq("id", existente["id"]).execute()
        escaneo_id = existente["id"]
    else:
        escaneo_id = supabase.table("escaneos_equipos") \
            .insert(escaneo).execute().data[0]["id"]

    # Mantiene al dia el hardware del activo vinculado (sin pisar marca/modelo).
    if activo:
        supabase.table("activos").update(
            datos_para_activo(escaneo, activo)
        ).eq("id", activo["id"]).execute()

    return {
        "escaneo_id": escaneo_id,
        "resultado": estado,
        "activo_id": activo.get("id") if activo else None,
        "actualizado": bool(existente),
    }