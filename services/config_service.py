import os
import time

from supabase_client import supabase

CONFIG_DEFECTO = {
    "nombre_empresa": "Editorial Planeta",
    "correo_soporte": "",
    "dias_alerta_garantia": 30,
    "sesion_minutos": int(os.getenv("SESSION_TIMEOUT_MINUTES", "30")),
    "exigir_serial": False,
    "texto_clausula": "",
    "moneda_defecto": "COP",
}

_cache_config = {"hasta": 0, "valor": None}


def limpiar_cache_config():
    _cache_config["valor"] = None


def obtener_config(usar_cache=False):
    """Lee la configuracion. Con usar_cache evita ir a la BD en cada request."""
    if (
        usar_cache
        and _cache_config["valor"]
        and time.time() < _cache_config["hasta"]
    ):
        return dict(_cache_config["valor"])

    cfg = dict(CONFIG_DEFECTO)
    try:
        r = (
            supabase.table("configuracion")
            .select("*")
            .eq("id", 1)
            .limit(1)
            .execute()
        )
        if r.data:
            # Los NULL de la BD no pisan los valores por defecto
            cfg.update({k: v for k, v in r.data[0].items() if v is not None})
    except Exception as error:
        print(f"Error leyendo configuracion: {error}")

    _cache_config["valor"] = dict(cfg)
    _cache_config["hasta"] = time.time() + 60
    return cfg