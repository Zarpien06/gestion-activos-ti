"""Calculos extra del dashboard (alertas, tendencias, vencimientos, rankings).

Uso en la ruta:
    extras = construir_extras(activos, todos_los_tickets, garantias)
    return render_template("dashboard.html", ..., **extras)

Todo esta protegido: si una tabla o columna no existe, ese bloque
simplemente no se muestra y el dashboard sigue funcionando.
"""

from collections import Counter
from datetime import date, datetime, timedelta, timezone

from flask import url_for

from services.db_service import consultar_tabla, estado_operativo

MESES = ["ene", "feb", "mar", "abr", "may", "jun",
         "jul", "ago", "sep", "oct", "nov", "dic"]

TICKETS_CERRADOS = {"cerrado", "resuelto", "solucionado", "finalizado", "completado"}

# Nombres posibles de columnas / tablas (ajusta si los tuyos son otros)
CAMPOS_PRECIO = ("precio_compra", "precio", "valor_compra")
CAMPOS_FECHA_COMPRA = ("fecha_compra", "compra", "fecha_adquisicion")
CAMPOS_FIN_CONTRATO = ("fecha_fin", "fecha_vencimiento", "fecha_vence", "vence", "vencimiento")
CAMPOS_VENCE_LICENCIA = ("fecha_vence", "fecha_vencimiento", "vence", "vencimiento", "fecha_fin")
TABLAS_CONTRATOS = ("contratos", "contratos_mantenimiento")


# ---------------------------------------------------------------- utilidades

def hoy_colombia():
    return (datetime.now(timezone.utc) - timedelta(hours=5)).date()


def _fecha(valor):
    if not valor:
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = str(valor).strip()
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(texto[:10], fmt).date()
        except ValueError:
            continue
    return None


def _primero(fila, claves):
    for c in claves:
        v = fila.get(c)
        if v not in (None, ""):
            return v
    return None


def _numero(valor):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _tabla(nombres):
    """Primera tabla que exista; None si ninguna."""
    for n in nombres:
        try:
            return consultar_tabla(n, "*", "id")
        except Exception:
            continue
    return None


def _ultimos_meses(n):
    hoy = hoy_colombia()
    y, m, salida = hoy.year, hoy.month, []
    for _ in range(n):
        salida.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return salida[::-1]


def _serie_mensual(filas, campo_fecha, n=6, filtro=None):
    claves, cont = _ultimos_meses(n), Counter()
    for f in filas:
        if filtro and not filtro(f):
            continue
        d = _fecha(f.get(campo_fecha))
        if d:
            cont[(d.year, d.month)] += 1
    etiquetas = [f"{MESES[m - 1]} {str(y)[2:]}" for y, m in claves]
    return etiquetas, [cont[k] for k in claves]


def _accion(fila):
    return str(fila.get("accion") or "").strip().lower()


# --------------------------------------------------------------- bloques

def _tendencias(tickets, n=6):
    extra = {}

    try:
        movs = consultar_tabla("movimientos", "*", "id")
        fecha = "created_at" if any(m.get("created_at") for m in movs) else "fecha"
        etiquetas, asig = _serie_mensual(movs, fecha, n, lambda f: _accion(f).startswith("asign"))
        _, devol = _serie_mensual(movs, fecha, n, lambda f: _accion(f).startswith("devol"))
        if any(asig) or any(devol):
            extra["mov_mensual"] = {
                "labels": etiquetas, "asignaciones": asig, "devoluciones": devol,
            }
    except Exception as error:
        print(f"Dashboard: no se pudo calcular movimientos por mes: {error}")

    try:
        etiquetas, creados = _serie_mensual(tickets, "created_at", n)
        _, solucionados = _serie_mensual(tickets, "fecha_solucion", n)
        if any(creados) or any(solucionados):
            extra["tk_mensual"] = {
                "labels": etiquetas, "creados": creados, "solucionados": solucionados,
            }

        dias = []
        for t in tickets:
            ini, fin = _fecha(t.get("created_at")), _fecha(t.get("fecha_solucion"))
            if ini and fin and fin >= ini:
                dias.append((fin - ini).days)
        extra["tk_prom_dias"] = (sum(dias) / len(dias)) if dias else None
    except Exception as error:
        print(f"Dashboard: no se pudo calcular tickets por mes: {error}")

    return extra


def _vencimientos(filas, campos_fecha, construir, ventana=60, atras=90):
    hoy = hoy_colombia()
    lista = []
    for f in filas:
        # "activa" (licencias) y "activo" (otras tablas)
        if f.get("activa") is False or f.get("activo") is False:
            continue
        if str(f.get("estado") or "").strip().lower() in ("inactivo", "cancelado"):
            continue
        vence = _fecha(_primero(f, campos_fecha))
        if not vence:
            continue
        dias = (vence - hoy).days
        if -atras <= dias <= ventana:
            lista.append({**construir(f), "vence": vence, "dias": dias})
    return sorted(lista, key=lambda x: x["dias"])


def _licencias_por_vencer(ventana=90):
    """Licencias (tabla `licencias`) con el nombre tomado de `software`."""
    try:
        filas = consultar_tabla("licencias", "*", "id")
    except Exception as error:
        print(f"Dashboard: no se pudo leer licencias: {error}")
        return None

    nombres = {}
    try:
        for s in consultar_tabla("software", "*", "id"):
            texto = f"{s.get('nombre') or ''} {s.get('version') or ''}".strip()
            nombres[s["id"]] = texto or "Licencia"
    except Exception as error:
        print(f"Dashboard: no se pudo leer software: {error}")

    return _vencimientos(
        filas, CAMPOS_VENCE_LICENCIA,
        lambda f: {"software": nombres.get(f.get("software_id")) or "Licencia"},
        ventana=ventana,
    )


def _financiero_y_rankings(activos):
    extra = {}

    # Por marca (top 6)
    marcas = Counter(
        (str(a.get("marca") or "").strip() or "Sin marca") for a in activos
        if "baja" not in estado_operativo(a)
    ).most_common(6)
    if marcas:
        extra["por_marca"] = marcas

    # Personas con mas equipos
    conteo, area_de = Counter(), {}
    for a in activos:
        if estado_operativo(a) == "asignado" and a.get("asignado_a"):
            nombre = str(a["asignado_a"]).strip()
            conteo[nombre] += 1
            area_de[nombre] = a.get("area")
    if conteo:
        extra["top_personas"] = [(n, area_de.get(n), c) for n, c in conteo.most_common(5)]

    # Inversion por anio de compra (millones, solo COP o sin moneda)
    por_anio = Counter()
    for a in activos:
        if str(a.get("moneda") or "COP").strip().upper() != "COP":
            continue
        precio = _numero(_primero(a, CAMPOS_PRECIO))
        fecha = _fecha(_primero(a, CAMPOS_FECHA_COMPRA))
        if precio and fecha:
            por_anio[fecha.year] += precio
    if por_anio:
        extra["gasto_anual"] = [(y, round(v / 1_000_000, 1)) for y, v in sorted(por_anio.items())]

    return extra


# ------------------------------------------------------------ alertas

def _alertas(activos, tickets, garantias, contratos, licencias):
    alertas = []
    hoy = hoy_colombia()

    vencidas = sum(1 for g in garantias if g["dias"] < 0)
    por_vencer = len(garantias) - vencidas
    if vencidas:
        alertas.append({"nivel": "danger", "texto": f"{vencidas} garantía{'s' if vencidas != 1 else ''} vencida{'s' if vencidas != 1 else ''}",
                        "url": url_for("inventario")})
    if por_vencer:
        alertas.append({"nivel": "warning", "texto": f"{por_vencer} garantía{'s' if por_vencer != 1 else ''} por vencer",
                        "url": url_for("inventario")})

    viejos = 0
    for t in tickets:
        if str(t.get("estado") or "").strip().lower() in TICKETS_CERRADOS:
            continue
        creado = _fecha(t.get("created_at"))
        if creado and (hoy - creado).days > 7:
            viejos += 1
    if viejos:
        alertas.append({"nivel": "warning", "texto": f"{viejos} ticket{'s' if viejos != 1 else ''} abierto{'s' if viejos != 1 else ''} hace más de 7 días",
                        "url": url_for("tickets")})

    for lista, nombre, endpoint in (
        (contratos, "contrato", "contratos.contratos_page"),
        (licencias, "licencia", "software.software_page"),
    ):
        if not lista:
            continue
        venc = sum(1 for x in lista if x["dias"] < 0)
        prox = sum(1 for x in lista if 0 <= x["dias"] <= 30)
        try:
            destino = url_for(endpoint)
        except Exception:
            destino = "#"
        if venc:
            alertas.append({"nivel": "danger", "texto": f"{venc} {nombre}{'s' if venc != 1 else ''} vencido{'s' if venc != 1 else ''}", "url": destino})
        if prox:
            alertas.append({"nivel": "warning", "texto": f"{prox} {nombre}{'s' if prox != 1 else ''} vence{'n' if prox != 1 else ''} en 30 días o menos", "url": destino})

    if any(any(k in a for k in CAMPOS_PRECIO) for a in activos[:1]):
        sin_precio = sum(1 for a in activos if "baja" not in estado_operativo(a) and not _numero(_primero(a, CAMPOS_PRECIO)))
        if sin_precio:
            alertas.append({"nivel": "info", "texto": f"{sin_precio} equipo{'s' if sin_precio != 1 else ''} sin precio de compra",
                            "url": url_for("inventario")})

    return alertas


# ------------------------------------------------------------ entrada

def construir_extras(activos, tickets, garantias):
    """Devuelve el diccionario de variables nuevas para dashboard.html."""
    extras = {}

    extras.update(_tendencias(tickets))
    extras.update(_financiero_y_rankings(activos))

    contratos = licencias = None

    filas = _tabla(TABLAS_CONTRATOS)
    if filas is not None:
        contratos = _vencimientos(
            filas, CAMPOS_FIN_CONTRATO,
            lambda f: {"proveedor": f.get("proveedor") or "Sin proveedor", "tipo": f.get("tipo")},
        )
        extras["contratos_prox"] = contratos

    licencias = _licencias_por_vencer(ventana=90)
    if licencias is not None:
        extras["licencias_prox"] = licencias

    try:
        extras["alertas"] = _alertas(activos, tickets, garantias, contratos, licencias)
    except Exception as error:
        print(f"Dashboard: no se pudieron calcular alertas: {error}")

    return extras