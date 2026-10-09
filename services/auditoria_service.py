import re
from decimal import Decimal

from flask import has_request_context
from flask_login import current_user

from services.db_service import fecha_larga, obtener_registro
from supabase_client import supabase

# Campos que se auditan y como se muestran
ETIQUETAS = {
    "codigo": "Código",
    "tipo": "Tipo",
    "marca": "Marca",
    "modelo": "Modelo",
    "serial": "Serial",
    "estado": "Estado físico",
    "cantidad": "Cantidad",
    "procesador": "Procesador",
    "ram": "RAM",
    "disco": "Disco",
    "hostname": "Hostname",
    "ip": "IP",
    "mac": "MAC",
    "sistema_operativo": "Sistema operativo",
    "observaciones": "Observaciones",
    "disponibilidad": "Disponibilidad",
    "asignado_a": "Asignado a",
    "area": "Área",
    "proveedor": "Proveedor",
    "fecha_compra": "Fecha de compra",
    "precio_compra": "Precio de compra",
    "moneda": "Moneda",
    "nro_factura": "N.º factura",
    "nro_orden": "N.º orden",
    "centro_costo": "Centro de costo",
    "sede": "Sede",
    "fecha_puesta_uso": "Puesta en uso",
    "vida_util_meses": "Vida útil (meses)",
    "valor_residual": "Valor residual",
    "garantia_inicio": "Garantía inicio",
    "garantia_meses": "Garantía (meses)",
    "garantia_fin": "Garantía vence",
}


def _norm(valor):
    """Normaliza para comparar: None y '' son iguales, 3500000.0 == 3500000."""
    if valor is None:
        return ""
    if isinstance(valor, bool):
        return "Sí" if valor else "No"
    if isinstance(valor, (int, float, Decimal)):
        f = float(valor)
        return str(int(f)) if f == int(f) else str(round(f, 2))
    s = str(valor).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}T", s):
        return s[:10]
    return s


def diferencias(anterior, nuevo):
    """Lista de (campo, antes, despues) solo con lo que cambio de verdad."""
    cambios = []
    for campo in ETIQUETAS:
        if campo not in nuevo:
            continue
        antes, despues = _norm(anterior.get(campo)), _norm(nuevo.get(campo))
        if antes != despues:
            cambios.append((campo, antes or None, despues or None))
    return cambios


def registrar_cambios(activo_id, cambios):
    """Guarda las diferencias. Nunca rompe el flujo principal."""
    if not cambios:
        return

    usuario_id = usuario_nombre = None
    if has_request_context() and current_user.is_authenticated:
        usuario_id = int(current_user.id)
        usuario_nombre = current_user.nombre

    filas = [
        {
            "activo_id": activo_id,
            "usuario_id": usuario_id,
            "usuario_nombre": usuario_nombre,
            "campo": campo,
            "valor_anterior": antes,
            "valor_nuevo": despues,
        }
        for campo, antes, despues in cambios
    ]
    try:
        supabase.table("historial_cambios").insert(filas).execute()
    except Exception as error:
        print(f"Error registrando cambios del activo {activo_id}: {error}")


def actualizar_con_auditoria(activo_id, datos, anterior=None):
    """UPDATE sobre activos que ademas deja el rastro campo por campo.

    Compara contra la fila que devuelve la BD, asi tambien captura los
    valores que calculan los triggers (por ejemplo garantia_fin).
    """
    if anterior is None:
        anterior = obtener_registro("activos", activo_id) or {}

    resultado = supabase.table("activos").update(datos).eq("id", activo_id).execute()
    nuevo = resultado.data[0] if resultado.data else {**anterior, **datos}

    registrar_cambios(activo_id, diferencias(anterior, nuevo))
    return nuevo


def listar_cambios(activo_id, limite=100):
    try:
        filas = (
            supabase.table("historial_cambios")
            .select("*")
            .eq("activo_id", activo_id)
            .order("id", desc=True)
            .limit(limite)
            .execute()
            .data
            or []
        )
    except Exception as error:
        print(f"Error leyendo historial de cambios: {error}")
        return []

    return [
        {
            "fecha": fecha_larga(f.get("fecha")),
            "usuario": f.get("usuario_nombre") or "—",
            "campo": ETIQUETAS.get(f["campo"], f["campo"]),
            "antes": f.get("valor_anterior") or "—",
            "despues": f.get("valor_nuevo") or "—",
        }
        for f in filas
    ]