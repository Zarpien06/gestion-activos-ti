import re
from collections import Counter, defaultdict
from datetime import datetime

from supabase_client import supabase

MONEDAS = ("COP", "USD", "EUR")


# ---------------------------------------------------------------
# FORMATO
# ---------------------------------------------------------------

def formato_dinero(valor, moneda="COP"):
    if valor is None or valor == "":
        return "—"
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return "—"
    if moneda == "COP":
        return "$ " + f"{v:,.0f}".replace(",", ".")
    simbolo = {"USD": "US$", "EUR": "€"}.get(moneda, moneda)
    return f"{simbolo} {v:,.2f}"


# ---------------------------------------------------------------
# PARSEO Y VALIDACION DEL FORMULARIO
# ---------------------------------------------------------------

def parsear_precio(texto, nombre="El valor"):
    """'$ 1.500.000', '1,500,000.50', '1500000' -> float. Vacio -> None."""
    t = (texto or "").strip()
    if not t:
        return None

    t = re.sub(r"[^\d.,]", "", t)
    if not t:
        raise ValueError(f"{nombre} no es válido.")

    if "." in t and "," in t:
        # El ultimo separador es el decimal
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        if re.fullmatch(r"\d{1,3}(,\d{3})+", t):
            t = t.replace(",", "")
        else:
            t = t.replace(",", ".")
    elif "." in t:
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", t):
            t = t.replace(".", "")

    try:
        valor = round(float(t), 2)
    except ValueError:
        raise ValueError(f"{nombre} no es válido.")

    if valor > 999_999_999_999:
        raise ValueError(f"{nombre} es demasiado grande.")
    return valor


def parsear_fecha(texto, nombre="La fecha"):
    t = (texto or "").strip()
    if not t:
        return None
    try:
        return datetime.strptime(t[:10], "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise ValueError(f"{nombre} no es válida.")


def parsear_entero(texto, minimo, maximo, nombre="El valor"):
    t = (texto or "").strip()
    if not t:
        return None
    try:
        n = int(t)
    except ValueError:
        raise ValueError(f"{nombre} debe ser un número entero.")
    if not minimo <= n <= maximo:
        raise ValueError(f"{nombre} debe estar entre {minimo} y {maximo}.")
    return n


def campos_finanzas(form):
    """Campos de compra, garantia y depreciacion listos para guardar.

    Lanza ValueError con un mensaje claro si algo no es valido.
    """
    def txt(nombre):
        return (form.get(nombre) or "").strip() or None

    moneda = (form.get("moneda") or "COP").strip().upper()
    if moneda not in MONEDAS:
        moneda = "COP"

    precio = parsear_precio(form.get("precio_compra"), "El precio de compra")
    residual = parsear_precio(form.get("valor_residual"), "El valor residual") or 0
    if precio is not None and residual > precio:
        raise ValueError("El valor residual no puede superar el precio de compra.")

    fecha_compra = parsear_fecha(form.get("fecha_compra"), "La fecha de compra")
    puesta_uso = parsear_fecha(form.get("fecha_puesta_uso"), "La fecha de puesta en uso")
    g_inicio = parsear_fecha(form.get("garantia_inicio"), "El inicio de garantía")
    g_fin = parsear_fecha(form.get("garantia_fin"), "El vencimiento de garantía")
    g_meses = parsear_entero(form.get("garantia_meses"), 0, 240, "Los meses de garantía")
    vida = parsear_entero(form.get("vida_util_meses"), 1, 600, "La vida útil")

    # Si hay meses pero no inicio, la garantia arranca en la fecha de compra
    if g_meses is not None and g_inicio is None:
        g_inicio = fecha_compra

    # Con inicio + meses, el trigger de la BD calcula el vencimiento
    if g_inicio and g_meses is not None:
        g_fin = None
    elif g_inicio and g_fin and g_fin < g_inicio:
        raise ValueError("La garantía no puede vencer antes de su inicio.")

    return {
        "proveedor": txt("proveedor"),
        "fecha_compra": fecha_compra,
        "precio_compra": precio,
        "moneda": moneda,
        "nro_factura": txt("nro_factura"),
        "nro_orden": txt("nro_orden"),
        "centro_costo": txt("centro_costo"),
        "sede": txt("sede"),
        "fecha_puesta_uso": puesta_uso,
        "vida_util_meses": vida,
        "valor_residual": residual,
        "garantia_inicio": g_inicio,
        "garantia_meses": g_meses,
        "garantia_fin": g_fin,
        "garantia": None,  # texto viejo: el trigger lo rellena con la fecha fin
    }


# ---------------------------------------------------------------
# CONSULTAS A LAS VISTAS
# ---------------------------------------------------------------

def _leer_todo(tabla, columnas="*", orden="id"):
    """Lee toda la tabla/vista (Supabase limita a 1000 filas por consulta)."""
    filas, desde, paso = [], 0, 1000
    while True:
        datos = (
            supabase.table(tabla)
            .select(columnas)
            .order(orden)
            .range(desde, desde + paso - 1)
            .execute()
            .data
            or []
        )
        filas.extend(datos)
        if len(datos) < paso:
            return filas
        desde += paso


def leer_vista_financiera(orden="id"):
    return _leer_todo("v_activos_financiero", "*", orden)


def gasto_proveedor_anio():
    return _leer_todo("v_gasto_proveedor_anio", "*", "anio")


def resumen_financiero(filas):
    """Totales para las tarjetas. Ignora los activos dados de baja."""
    compra, actual = defaultdict(float), defaultdict(float)
    garantia = Counter()
    sin_precio = sin_vida = 0

    for f in filas:
        if "baja" in str(f.get("disponibilidad") or "").lower():
            continue

        moneda = f.get("moneda") or "COP"
        garantia[f.get("estado_garantia") or "sin_garantia"] += 1

        if f.get("precio_compra") is None:
            sin_precio += 1
            continue

        compra[moneda] += float(f["precio_compra"])
        if f.get("valor_actual") is None:
            sin_vida += 1
        else:
            actual[moneda] += float(f["valor_actual"])

    def texto(totales):
        return " · ".join(formato_dinero(v, m) for m, v in totales.items()) or "—"

    por_vencer = (
        garantia["por_vencer_30"] + garantia["por_vencer_60"] + garantia["por_vencer_90"]
    )
    return {
        "compra_txt": texto(compra),
        "actual_txt": texto(actual),
        "garantias_por_vencer": por_vencer,
        "garantias_vencidas": garantia["vencida"],
        "garantias_30": garantia["por_vencer_30"],
        "sin_precio": sin_precio,
        "sin_vida_util": sin_vida,
    }


def datos_financieros():
    """(mapa por id, resumen). Si la vista falla, la pagina sigue funcionando."""
    try:
        filas = leer_vista_financiera()
    except Exception as error:
        print(f"Error leyendo v_activos_financiero: {error}")
        return {}, None
    return {f["id"]: f for f in filas}, resumen_financiero(filas)