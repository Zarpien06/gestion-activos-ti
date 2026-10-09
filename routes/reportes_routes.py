"""Reportes con filtros, hojas de resumen con graficas y consolidado.

Se registra en app.py con: reportes_routes.registrar_rutas(app)

Eficiencia:
  - Cache corto (en memoria) de tablas, informes filtrados y listas de filtros.
  - Los informes se arman en dos pasos: primero se filtra (barato, basta para
    contar) y solo al descargar se construyen filas, resumen y hojas extra.
  - Se limpia solo cuando la app hace un POST/PUT/PATCH/DELETE exitoso.

Filtros:
  - Sin importar mayusculas ni tildes ("garantia" encuentra "Garantía").
  - Buscador por varias palabras (todas deben aparecer).
  - Cada filtro acepta varios valores (?area=A&area=B).
  - Fechas invalidas se ignoran y un rango invertido se corrige solo.
"""
from collections import Counter
from functools import lru_cache
import io
import threading
import time
import unicodedata
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import (
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import login_required

from auth.decorators import permiso_requerido
from services.config_service import obtener_config
from services.db_service import consultar_tabla, estado_operativo
from services.excel_service import respuesta_libro
from services.finanzas_service import (
    gasto_proveedor_anio,
    leer_vista_financiera,
    resumen_financiero,
)


# ==================================================
# CACHE
# ==================================================
# Los datos cacheados se comparten: nunca se modifican, solo se leen.

TTL_TABLAS = 30      # segundos
TTL_INFORMES = 30
TTL_OPCIONES = 60
_MAX_CACHE = 80

_CACHE = {}
_LOCK = threading.Lock()


def _memo(clave, fn, ttl=TTL_TABLAS):
    ahora = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(clave)
        if hit and ahora - hit[0] < ttl:
            return hit[1]

    valor = fn()

    with _LOCK:
        if len(_CACHE) >= _MAX_CACHE:
            por_edad = sorted(_CACHE, key=lambda k: _CACHE[k][0])
            for k in por_edad[: len(_CACHE) - _MAX_CACHE // 2]:
                del _CACHE[k]
        _CACHE[clave] = (time.monotonic(), valor)
    return valor


def invalidar_cache():
    """Vacia el cache (llamar si se editan datos fuera de la app)."""
    with _LOCK:
        _CACHE.clear()


def _tabla(*args):
    """consultar_tabla con cache."""
    return _memo(("tabla",) + args, lambda: consultar_tabla(*args))


def _activos():
    return _tabla("activos", "*", "codigo")


def _activos_id():
    return _memo(("activos_id",), lambda: {a.get("id"): a for a in _activos()})


def _personas_lista():
    return _tabla("personas", "*", "nombre")


def _personas_id():
    return _memo(("personas_id",),
                 lambda: {p.get("id"): p for p in _personas_lista()})


def _personas_nombre():
    return _memo(("personas_nombre",),
                 lambda: {_txt(p.get("nombre")): p for p in _personas_lista()})


def _asignados_por_persona():
    def construir():
        salida = {}
        for a in _activos():
            if estado_operativo(a) == "asignado":
                salida.setdefault(_txt(a.get("asignado_a")), []).append(a)
        return salida
    return _memo(("asignados",), construir)


def _vista_fin():
    return _memo(("vista_fin",), lambda: leer_vista_financiera("id"))


# ==================================================
# UTILIDADES
# ==================================================

def _hoy():
    return (datetime.now(timezone.utc) - timedelta(hours=5)).date()


def _sello():
    return _hoy().strftime("%Y%m%d")


def _txt(v):
    return str(v or "").strip()


def _norm_raw(t):
    t = unicodedata.normalize("NFD", t.casefold())
    return "".join(c for c in t if not unicodedata.combining(c))


_norm_corto = lru_cache(maxsize=20000)(_norm_raw)


def _low(v):
    """Minusculas y sin tildes, para comparar y buscar."""
    t = _txt(v)
    if t.isascii():
        return t.lower()
    return _norm_corto(t) if len(t) <= 80 else _norm_raw(t)


def _dia(v):
    """'2026-09-30T20:53:27+00:00' -> '2026-09-30'."""
    return _txt(v)[:10]


def _fecha_hora(v):
    return _txt(v)[:16].replace("T", " ")


def _fecha(v):
    try:
        return datetime.strptime(_dia(v), "%Y-%m-%d").date()
    except ValueError:
        return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _arg(nombre):
    return request.args.get(nombre, "").strip()


def _multi(nombre):
    """Valores elegidos de un filtro (acepta varios), normalizados."""
    return {_low(v) for v in request.args.getlist(nombre) if v.strip()}


def _ok(valor, elegidos):
    """True si no hay filtro o el valor esta entre los elegidos."""
    return not elegidos or _low(valor) in elegidos


def _terminos():
    """Palabras del buscador; todas deben aparecer."""
    return _low(_arg("q")).split()


def _coincide(terminos, *valores):
    pajar = " ".join(_low(v) for v in valores)
    return all(t in pajar for t in terminos)


def _rango_args(desde="desde", hasta="hasta"):
    """Fechas validas del filtro; si vienen al reves, las ordena."""
    d, h = _arg(desde), _arg(hasta)
    d = d if _fecha(d) else ""
    h = h if _fecha(h) else ""
    if d and h and d > h:
        d, h = h, d
    return d, h


def _en_rango(valor, desde, hasta):
    if not desde and not hasta:
        return True
    d = _dia(valor)
    if not d:
        return False
    if desde and d < desde:
        return False
    if hasta and d > hasta:
        return False
    return True


def _conteo(valores, tope=15, vacio="Sin dato"):
    items = Counter((_txt(v) or vacio) for v in valores).most_common()
    if len(items) > tope:
        resto = sum(n for _, n in items[tope:])
        items = items[:tope] + [("Otros", resto)]
    return items


def _por_periodo(valores, largo):
    """largo=7 -> por mes (AAAA-MM), largo=4 -> por ano."""
    c = Counter(_dia(v)[:largo] for v in valores if _dia(v))
    return sorted(c.items())


def _bloque(titulo, datos, grafico="bar"):
    return {"titulo": titulo, "datos": datos, "grafico": grafico if datos else None}


def _es_resuelto(t):
    e = _low(t.get("estado"))
    return "solucion" in e or "cerrado" in e or "resuelto" in e


def _limites_garantia():
    try:
        dias = int(obtener_config(usar_cache=True).get("dias_alerta_garantia") or 30)
    except (TypeError, ValueError):
        dias = 30
    hoy = _hoy()
    return hoy.isoformat(), (hoy + timedelta(days=dias)).isoformat()


def _estado_garantia(a, hoy_s, limite):
    g = _dia(a.get("garantia"))
    if not g:
        return "Sin dato"
    if g < hoy_s:
        return "Vencida"
    if g <= limite:
        return "Por vencer"
    return "Vigente"


VALIDOS_ESTADO = {"disponible", "asignado", "repar", "baja", "inhabilit"}


def _estados_sel():
    """Disponibilidades elegidas (vacio = todas)."""
    return _multi("estado") & VALIDOS_ESTADO


def _estado_ok(elegidos, registro):
    if not elegidos:
        return True
    op = estado_operativo(registro)
    return any(e in op for e in elegidos)


def _suma(items, etiqueta, valor, moneda, tope=15, ordenar=False):
    """Totales de dinero por etiqueta. Si hay varias monedas no las mezcla."""
    multi = len({moneda(i) for i in items}) > 1
    total = Counter()
    for i in items:
        v = _num(valor(i))
        if v is None:
            continue
        et = _txt(etiqueta(i)) or "Sin dato"
        if multi:
            et = f"{et} ({moneda(i)})"
        total[et] += v

    if ordenar:
        return [(k, round(v, 2)) for k, v in sorted(total.items())]

    datos = [(k, round(v, 2)) for k, v in total.most_common()]
    if len(datos) > tope:
        resto = sum(v for _, v in datos[tope:])
        datos = datos[:tope] + [("Otros", round(resto, 2))]
    return datos


def _moneda_fila(f):
    return (_txt(f.get("moneda")) or "COP").upper()


def _contar(tabla):
    try:
        return len(_tabla(tabla, "id"))
    except Exception:
        return 0


def _informe(hoja, encabezados, total, filas, resumen, extras=None):
    """Informe filtrado. filas, resumen y extras son funciones que se
    ejecutan solo al descargar; para contar basta con `total`."""
    return {
        "hoja": hoja, "encabezados": encabezados, "total": total,
        "filas": filas, "resumen": resumen, "extras": extras,
    }


def _materializar(d):
    extras = d.get("extras")
    return {
        "hoja": d["hoja"],
        "encabezados": d["encabezados"],
        "filas": d["filas"](),
        "resumen": d["resumen"](),
        "extras": extras() if extras else [],
    }


# ==================================================
# CONSTRUCTORES (filtran; filas y resumen se arman al descargar)
# ==================================================

_CAMPOS_BUSQUEDA_INV = (
    "codigo", "serial", "hostname", "marca", "modelo", "ip", "mac", "asignado_a",
)


def _inventario():
    estados = _estados_sel()
    tipos, marcas = _multi("tipo"), _multi("marca")
    areas, personas_sel = _multi("area"), _multi("persona")
    fisicos = _multi("fisico")
    garantias = {g.replace("_", " ") for g in _multi("garantia")}
    factura = _low(_arg("factura"))
    cdesde, chasta = _rango_args("compra_desde", "compra_hasta")
    q = _terminos()

    personas = _personas_nombre()
    hoy_s, limite = _limites_garantia()

    elegidos = []
    for a in _activos():
        pers = personas.get(_txt(a.get("asignado_a")), {})
        area_a = _txt(a.get("area")) or _txt(pers.get("area"))
        gar = _estado_garantia(a, hoy_s, limite)

        if not _estado_ok(estados, a):
            continue
        if not _ok(a.get("tipo"), tipos):
            continue
        if not _ok(a.get("marca"), marcas):
            continue
        if not _ok(area_a, areas):
            continue
        if not _ok(a.get("asignado_a"), personas_sel):
            continue
        if not _ok(a.get("estado"), fisicos):
            continue
        if garantias and gar.lower() not in garantias:
            continue
        if factura == "si" and not a.get("factura_ruta"):
            continue
        if factura == "no" and a.get("factura_ruta"):
            continue
        if not _en_rango(a.get("fecha_compra"), cdesde, chasta):
            continue
        if q and not _coincide(q, *(a.get(k) for k in _CAMPOS_BUSQUEDA_INV)):
            continue
        elegidos.append((a, pers, area_a, gar))

    def filas():
        return [
            [
                a.get("codigo"), a.get("tipo"), a.get("marca"), a.get("modelo"),
                a.get("serial"), a.get("disponibilidad"), a.get("estado"),
                a.get("cantidad"), a.get("asignado_a"), pers.get("documento"),
                pers.get("cargo"), pers.get("correo"), area_a,
                a.get("procesador"), a.get("ram"), a.get("disco"),
                a.get("hostname"), a.get("ip"), a.get("mac"),
                a.get("sistema_operativo"), a.get("fecha_compra"),
                a.get("garantia"), gar,
                "Si" if a.get("factura_ruta") else "No",
                a.get("observaciones"), _fecha_hora(a.get("created_at")),
            ]
            for a, pers, area_a, gar in elegidos
        ]

    def resumen():
        return [
            _bloque("Equipos por disponibilidad",
                    _conteo(a.get("disponibilidad") for a, *_ in elegidos), "pie"),
            _bloque("Equipos por tipo",
                    _conteo(a.get("tipo") for a, *_ in elegidos), "bar"),
            _bloque("Equipos por marca",
                    _conteo(a.get("marca") for a, *_ in elegidos), "bar"),
            _bloque("Equipos por area",
                    _conteo(ar for _, _, ar, _ in elegidos), "bar"),
            _bloque("Equipos por estado fisico",
                    _conteo(a.get("estado") for a, *_ in elegidos), "pie"),
            _bloque("Estado de garantia",
                    _conteo(g for *_, g in elegidos), "pie"),
            _bloque("Equipos por ano de compra",
                    _por_periodo((a.get("fecha_compra") for a, *_ in elegidos), 4),
                    "bar"),
            _bloque("Mas equipos por persona (top 10)",
                    _conteo((a.get("asignado_a") for a, *_ in elegidos
                             if _txt(a.get("asignado_a"))), tope=10), "bar"),
        ]

    encabezados = [
        "Codigo", "Tipo", "Marca", "Modelo", "Serial", "Disponibilidad",
        "Estado fisico", "Cantidad", "Asignado a", "Documento", "Cargo",
        "Correo", "Area", "Procesador", "RAM", "Disco", "Hostname", "IP",
        "MAC", "Sistema operativo", "Fecha compra", "Garantia",
        "Estado garantia", "Factura", "Observaciones", "Fecha registro",
    ]
    return _informe("Inventario", encabezados, len(elegidos), filas, resumen)


def _personas():
    estados = _multi("estado")
    areas, cargos = _multi("area"), _multi("cargo")
    con_sin = _multi("equipos")
    q = _terminos()

    asignados = _asignados_por_persona()

    retiros = {}
    try:
        for r in _tabla("retiros_personas", "*", "id"):
            retiros[r.get("persona_id")] = r
    except Exception:
        pass

    elegidas = []
    for p in _personas_lista():
        activa = _low(p.get("estado") or "Activo") == "activo"
        equipos = asignados.get(_txt(p.get("nombre")), [])

        if estados and not (
            ("activos" in estados and activa)
            or ("inactivos" in estados and not activa)
        ):
            continue
        if not _ok(p.get("area"), areas):
            continue
        if not _ok(p.get("cargo"), cargos):
            continue
        if con_sin and not (
            ("con" in con_sin and equipos) or ("sin" in con_sin and not equipos)
        ):
            continue
        if q and not _coincide(q, p.get("nombre"), p.get("documento"), p.get("correo")):
            continue
        elegidas.append((p, activa, equipos, None if activa else retiros.get(p.get("id"))))

    def filas():
        return [
            [
                p.get("nombre"), p.get("documento"), p.get("correo"),
                p.get("cargo"), p.get("area"),
                "Activo" if activa else "Inactivo",
                len(eq), ", ".join(_txt(a.get("codigo")) for a in eq),
                _dia(ret.get("fecha")) if ret else "",
                ret.get("motivo") if ret else "",
            ]
            for p, activa, eq, ret in elegidas
        ]

    def resumen():
        equipos_por_area = Counter()
        for p, _, eq, _ in elegidas:
            if eq:
                equipos_por_area[_txt(p.get("area")) or "Sin area"] += len(eq)

        return [
            _bloque("Personas por estado",
                    _conteo("Activo" if ac else "Inactivo" for _, ac, _, _ in elegidas),
                    "pie"),
            _bloque("Personas por area",
                    _conteo(p.get("area") for p, *_ in elegidas), "bar"),
            _bloque("Personas por cargo",
                    _conteo(p.get("cargo") for p, *_ in elegidas), "bar"),
            _bloque("Personas con y sin equipos",
                    _conteo("Con equipos" if eq else "Sin equipos"
                            for _, _, eq, _ in elegidas), "pie"),
            _bloque("Equipos asignados por area",
                    equipos_por_area.most_common(15), "bar"),
            _bloque("Motivos de retiro",
                    _conteo((r.get("motivo") for *_, r in elegidas if r)), "pie"),
        ]

    encabezados = [
        "Nombre", "Documento", "Correo", "Cargo", "Area", "Estado",
        "Equipos asignados", "Codigos de equipos", "Fecha retiro",
        "Motivo retiro",
    ]
    return _informe("Personas", encabezados, len(elegidas), filas, resumen)


def _ticket_estado_ok(t, resuelto, elegidos):
    if not elegidos:
        return True
    est = _low(t.get("estado"))
    for s in elegidos:
        if s == "todos":
            return True
        if s == "abiertos" and not resuelto:
            return True
        if s == "solucionados" and resuelto:
            return True
        if s == est:
            return True
    return False


def _tickets():
    estados = _multi("estado")
    tipos, areas, personas_sel = _multi("tipo"), _multi("area"), _multi("persona")
    desde, hasta = _rango_args("desde", "hasta")
    sdesde, shasta = _rango_args("sol_desde", "sol_hasta")
    dias_min = _num(_arg("dias_min"))
    q = _terminos()

    activos = _activos_id()
    hoy = _hoy()

    elegidos = []
    for t in _tabla("tickets", "*", "id"):
        a = activos.get(t.get("activo_id"), {})
        resuelto = _es_resuelto(t)
        creado = t.get("created_at") or t.get("fecha")

        if not _ticket_estado_ok(t, resuelto, estados):
            continue
        if not _ok(a.get("tipo"), tipos):
            continue
        if not _ok(a.get("area"), areas):
            continue
        if not _ok(a.get("asignado_a"), personas_sel):
            continue
        if not _en_rango(creado, desde, hasta):
            continue
        if not _en_rango(t.get("fecha_solucion"), sdesde, shasta):
            continue
        if q and not _coincide(q, t.get("titulo"), t.get("descripcion"), a.get("codigo")):
            continue

        fin = _fecha(t.get("fecha_solucion")) or (None if resuelto else hoy)
        ini = _fecha(creado)
        dias = (fin - ini).days if ini and fin else ""

        if dias_min is not None and (dias == "" or dias < dias_min):
            continue
        elegidos.append((t, a, resuelto, creado, dias))

    elegidos.sort(key=lambda x: x[0].get("id") or 0, reverse=True)

    def filas():
        return [
            [
                t.get("id"), a.get("codigo") or t.get("activo_id"), a.get("tipo"),
                a.get("asignado_a"), a.get("area"), t.get("titulo"),
                t.get("descripcion"), t.get("estado"), _fecha_hora(creado),
                _fecha_hora(t.get("fecha_solucion")), dias,
            ]
            for t, a, _, creado, dias in elegidos
        ]

    def resumen():
        tiempos = [
            d for t, _, res, _, d in elegidos
            if res and d != "" and _dia(t.get("fecha_solucion"))
        ]
        promedio = round(sum(tiempos) / len(tiempos), 1) if tiempos else "Sin dato"
        abiertos = sum(1 for _, _, res, _, _ in elegidos if not res)

        return [
            _bloque("Indicadores", [
                ("Total de tickets", len(elegidos)),
                ("Abiertos", abiertos),
                ("Solucionados", len(elegidos) - abiertos),
                ("Promedio de dias a solucionar", promedio),
            ], None),
            _bloque("Tickets por estado",
                    _conteo(t.get("estado") for t, *_ in elegidos), "pie"),
            _bloque("Tickets por tipo de equipo",
                    _conteo(a.get("tipo") for _, a, *_ in elegidos), "bar"),
            _bloque("Tickets por area",
                    _conteo(a.get("area") for _, a, *_ in elegidos), "bar"),
            _bloque("Personas con mas tickets (top 10)",
                    _conteo((a.get("asignado_a") for _, a, *_ in elegidos
                             if _txt(a.get("asignado_a"))), tope=10), "bar"),
            _bloque("Tickets creados por mes",
                    _por_periodo((c for _, _, _, c, _ in elegidos), 7), "bar"),
        ]

    encabezados = [
        "ID", "Activo", "Tipo de equipo", "Asignado a", "Area", "Titulo",
        "Descripcion", "Estado", "Fecha", "Fecha solucion", "Dias",
    ]
    return _informe("Tickets", encabezados, len(elegidos), filas, resumen)


def _movimientos():
    """Asignaciones y devoluciones por persona (tabla movimientos)."""
    personas_sel, areas = _multi("persona"), _multi("area")
    acciones = _multi("accion")
    desde, hasta = _rango_args("desde", "hasta")
    q = _terminos()

    personas = _personas_id()
    activos = _activos_id()

    try:
        lista = _tabla("movimientos", "*", "id")
    except Exception:
        lista = []

    elegidos = []
    for m in lista:
        p = personas.get(m.get("persona_id"), {})
        a = activos.get(m.get("activo_id"), {})
        fecha = m.get("created_at") or m.get("fecha")

        if not _ok(p.get("nombre"), personas_sel):
            continue
        if not _ok(p.get("area"), areas):
            continue
        if acciones:
            acc = _low(m.get("accion"))
            if not any(acc.startswith(x) for x in acciones):
                continue
        if not _en_rango(fecha, desde, hasta):
            continue
        if q and not _coincide(q, a.get("codigo"), m.get("observacion")):
            continue
        elegidos.append((m, p, a, fecha))

    elegidos.sort(key=lambda x: x[0].get("id") or 0, reverse=True)

    def filas():
        return [
            [
                _fecha_hora(f), p.get("nombre"), p.get("documento"),
                p.get("area"), p.get("cargo"), a.get("codigo"), a.get("tipo"),
                m.get("accion"), m.get("observacion"),
            ]
            for m, p, a, f in elegidos
        ]

    def resumen():
        return [
            _bloque("Movimientos por accion",
                    _conteo(m.get("accion") for m, *_ in elegidos), "pie"),
            _bloque("Movimientos por area",
                    _conteo(p.get("area") for _, p, *_ in elegidos), "bar"),
            _bloque("Personas con mas movimientos (top 10)",
                    _conteo((p.get("nombre") for _, p, *_ in elegidos
                             if p.get("nombre")), tope=10), "bar"),
            _bloque("Movimientos por tipo de equipo",
                    _conteo(a.get("tipo") for _, _, a, _ in elegidos), "bar"),
            _bloque("Movimientos por mes",
                    _por_periodo((f for *_, f in elegidos), 7), "bar"),
        ]

    encabezados = [
        "Fecha", "Persona", "Documento", "Area", "Cargo", "Activo",
        "Tipo de equipo", "Accion", "Observacion",
    ]
    return _informe("Movimientos", encabezados, len(elegidos), filas, resumen)


def _historial():
    acciones = _multi("accion")
    desde, hasta = _rango_args("desde", "hasta")
    q = _terminos()

    activos = _activos_id()

    elegidos = []
    for h in _tabla("historial", "*", "id"):
        a = activos.get(h.get("activo_id"), {})
        fecha = h.get("created_at") or h.get("fecha")

        if not _ok(h.get("accion"), acciones):
            continue
        if not _en_rango(fecha, desde, hasta):
            continue
        if q and not _coincide(q, a.get("codigo"), h.get("detalle")):
            continue
        elegidos.append((h, a, fecha))

    elegidos.sort(key=lambda x: x[0].get("id") or 0, reverse=True)

    def filas():
        return [
            [_fecha_hora(f), a.get("codigo") or h.get("activo_id"),
             a.get("tipo"), h.get("accion"), h.get("detalle")]
            for h, a, f in elegidos
        ]

    def resumen():
        return [
            _bloque("Eventos por accion",
                    _conteo(h.get("accion") for h, *_ in elegidos), "pie"),
            _bloque("Eventos por tipo de equipo",
                    _conteo(a.get("tipo") for _, a, _ in elegidos), "bar"),
            _bloque("Eventos por mes",
                    _por_periodo((f for *_, f in elegidos), 7), "bar"),
        ]

    encabezados = ["Fecha", "Activo", "Tipo de equipo", "Accion", "Detalle"]
    return _informe("Historial", encabezados, len(elegidos), filas, resumen)


def _actas_filtradas():
    tipos, areas = _multi("tipo"), _multi("area")
    personas_sel = _multi("persona")
    desde, hasta = _rango_args("desde", "hasta")

    personas = _personas_id()

    salida = []
    for ac in _tabla("actas", "*", "id"):
        p = personas.get(ac.get("persona_id"), {})
        if not _ok(ac.get("tipo"), tipos):
            continue
        if not _ok(p.get("area"), areas):
            continue
        if not _ok(p.get("nombre"), personas_sel):
            continue
        if not _en_rango(ac.get("created_at"), desde, hasta):
            continue
        salida.append((ac, p))
    return salida


def _actas():
    elegidas = _actas_filtradas()
    elegidas.sort(key=lambda x: x[0].get("id") or 0, reverse=True)

    def filas():
        return [
            [
                ac.get("id"), _fecha_hora(ac.get("created_at")), p.get("nombre"),
                p.get("documento"), p.get("area"), p.get("cargo"), ac.get("tipo"),
                ac.get("observaciones"), ac.get("ruta_pdf"),
            ]
            for ac, p in elegidas
        ]

    def resumen():
        return [
            _bloque("Actas por tipo",
                    _conteo(ac.get("tipo") for ac, _ in elegidas), "pie"),
            _bloque("Actas por area",
                    _conteo(p.get("area") for _, p in elegidas), "bar"),
            _bloque("Actas por mes",
                    _por_periodo((ac.get("created_at") for ac, _ in elegidas), 7),
                    "bar"),
        ]

    encabezados = [
        "ID", "Fecha", "Persona", "Documento", "Area", "Cargo", "Tipo",
        "Observaciones", "Archivo",
    ]
    return _informe("Actas", encabezados, len(elegidas), filas, resumen)


def _financiero():
    """Inventario con compra, depreciacion y garantia (vista v_activos_financiero)."""
    monedas = _multi("moneda")
    provs, centros = _multi("proveedor"), _multi("centro")
    sedes, tipos, areas = _multi("sede"), _multi("tipo"), _multi("area")
    gars = _multi("garantia")
    estados = _estados_sel()
    cdesde, chasta = _rango_args("compra_desde", "compra_hasta")
    pmin, pmax = _num(_arg("precio_min")), _num(_arg("precio_max"))
    if pmin is not None and pmax is not None and pmin > pmax:
        pmin, pmax = pmax, pmin
    q = _terminos()

    try:
        filas_vista = _vista_fin()
    except Exception as error:
        print(f"Error leyendo v_activos_financiero: {error}")
        filas_vista = []

    elegidos = []
    for f in filas_vista:
        precio = _num(f.get("precio_compra"))
        est_gar = _low(f.get("estado_garantia")) or "sin_garantia"

        if not _estado_ok(estados, f):
            continue
        if not _ok(_moneda_fila(f), monedas):
            continue
        if not _ok(f.get("proveedor"), provs):
            continue
        if not _ok(f.get("centro_costo"), centros):
            continue
        if not _ok(f.get("sede"), sedes):
            continue
        if not _ok(f.get("tipo"), tipos):
            continue
        if not _ok(f.get("area"), areas):
            continue
        if gars and not any(
            est_gar == g or (g == "por_vencer" and est_gar.startswith("por_vencer"))
            for g in gars
        ):
            continue
        if pmin is not None and (precio is None or precio < pmin):
            continue
        if pmax is not None and (precio is None or precio > pmax):
            continue
        if not _en_rango(f.get("fecha_compra"), cdesde, chasta):
            continue
        if q and not _coincide(q, *(f.get(k) for k in (
            "codigo", "serial", "nro_factura", "nro_orden", "proveedor", "asignado_a",
        ))):
            continue
        elegidos.append(f)

    elegidos.sort(key=lambda f: _txt(f.get("codigo")))

    def filas():
        salida = []
        for f in elegidos:
            precio, actual = _num(f.get("precio_compra")), _num(f.get("valor_actual"))
            salida.append([
                f.get("codigo"), f.get("tipo"), f.get("marca"), f.get("modelo"),
                f.get("serial"), f.get("disponibilidad"), f.get("asignado_a"),
                f.get("area"), f.get("sede"), f.get("centro_costo"),
                f.get("proveedor"), f.get("nro_factura"), f.get("nro_orden"),
                _dia(f.get("fecha_compra")), _moneda_fila(f), precio,
                _num(f.get("valor_residual")), f.get("vida_util_meses"),
                _dia(f.get("fecha_puesta_uso")), actual,
                round(precio - actual, 2) if precio is not None and actual is not None else "",
                _dia(f.get("garantia_inicio")), f.get("garantia_meses"),
                _dia(f.get("garantia_fin")), f.get("estado_garantia") or "sin_garantia",
            ])
        return salida

    def resumen():
        totales = []
        for m in sorted({_moneda_fila(f) for f in elegidos}):
            grupo = [f for f in elegidos if _moneda_fila(f) == m]
            compra = sum(_num(f.get("precio_compra")) or 0 for f in grupo)
            actual = sum(_num(f.get("valor_actual")) or 0 for f in grupo)
            totales += [
                (f"Compra {m}", round(compra, 2)),
                (f"Valor actual {m}", round(actual, 2)),
                (f"Depreciacion {m}", round(compra - actual, 2)),
            ]

        sin_precio = sum(1 for f in elegidos if _num(f.get("precio_compra")) is None)
        sin_vida = sum(
            1 for f in elegidos
            if _num(f.get("precio_compra")) is not None and _num(f.get("valor_actual")) is None
        )

        def suma(etiqueta, **kw):
            return _suma(elegidos, etiqueta, lambda f: f.get("precio_compra"),
                         _moneda_fila, **kw)

        return [
            _bloque("Indicadores", [
                ("Equipos en el informe", len(elegidos)),
                ("Sin precio de compra", sin_precio),
                ("Con precio pero sin vida util", sin_vida),
            ], None),
            _bloque("Totales por moneda", totales, None),
            _bloque("Inversion por proveedor", suma(lambda f: f.get("proveedor")), "bar"),
            _bloque("Inversion por tipo de equipo", suma(lambda f: f.get("tipo")), "bar"),
            _bloque("Inversion por area", suma(lambda f: f.get("area")), "bar"),
            _bloque("Inversion por centro de costo",
                    suma(lambda f: f.get("centro_costo")), "bar"),
            _bloque("Inversion por sede", suma(lambda f: f.get("sede")), "bar"),
            _bloque("Inversion por ano de compra",
                    suma(lambda f: _dia(f.get("fecha_compra"))[:4] or "Sin fecha",
                         ordenar=True), "bar"),
            _bloque("Estado de garantia",
                    _conteo(f.get("estado_garantia") or "sin_garantia" for f in elegidos),
                    "pie"),
        ]

    def extras():
        salida = []
        try:
            gasto = _memo(("gasto_proveedor",), gasto_proveedor_anio)
            if gasto:
                cols = list(gasto[0].keys())
                salida.append({
                    "hoja": "Gasto por proveedor",
                    "encabezados": cols,
                    "filas": [[g.get(c) for c in cols] for g in gasto],
                })
        except Exception as error:
            print(f"Error leyendo v_gasto_proveedor_anio: {error}")
        return salida

    encabezados = [
        "Codigo", "Tipo", "Marca", "Modelo", "Serial", "Disponibilidad",
        "Asignado a", "Area", "Sede", "Centro de costo", "Proveedor",
        "N. factura", "N. orden", "Fecha compra", "Moneda",
        "Precio compra", "Valor residual", "Vida util (meses)",
        "Puesta en uso", "Valor actual", "Depreciacion acumulada",
        "Garantia inicio", "Garantia (meses)", "Garantia vence",
        "Estado garantia",
    ]
    return _informe("Financiero", encabezados, len(elegidos), filas, resumen, extras)


def _contratos():
    tipos, provs = _multi("tipo"), _multi("proveedor")
    estados = _multi("estado")
    vdesde, vhasta = _rango_args("vence_desde", "vence_hasta")
    q = _terminos()

    hoy = _hoy()
    hoy_s, limite = _limites_garantia()
    etiquetas = {
        "vencido": "Vencido", "por_vencer": "Por vencer", "vigente": "Vigente",
        "sin_fecha": "Sin fecha de fin", "inactivo": "Inactivo",
    }

    try:
        lista = _tabla("contratos", "*", "id")
    except Exception as error:
        print(f"Error leyendo contratos: {error}")
        lista = []

    elegidos = []
    for c in lista:
        fin = _dia(c.get("fecha_fin"))
        if c.get("activo") is False:
            clave = "inactivo"
        elif not fin:
            clave = "sin_fecha"
        elif fin < hoy_s:
            clave = "vencido"
        elif fin <= limite:
            clave = "por_vencer"
        else:
            clave = "vigente"

        if not _ok(c.get("tipo"), tipos):
            continue
        if not _ok(c.get("proveedor"), provs):
            continue
        if estados and clave not in estados:
            continue
        if not _en_rango(c.get("fecha_fin"), vdesde, vhasta):
            continue
        if q and not _coincide(q, c.get("proveedor"), c.get("numero"), c.get("notas")):
            continue

        f_fin = _fecha(fin)
        dias = (f_fin - hoy).days if f_fin else ""
        elegidos.append((c, clave, dias))

    def filas():
        return [
            [
                c.get("tipo"), c.get("proveedor"), c.get("numero"),
                c.get("renovacion"), _dia(c.get("fecha_inicio")),
                _dia(c.get("fecha_fin")), dias, _num(c.get("costo")),
                _moneda_fila(c), etiquetas[clave],
                "No" if c.get("activo") is False else "Si", c.get("notas"),
            ]
            for c, clave, dias in elegidos
        ]

    def resumen():
        items = [c for c, _, _ in elegidos]

        def costo(etiqueta, **kw):
            return _suma(items, etiqueta, lambda c: c.get("costo"), _moneda_fila, **kw)

        return [
            _bloque("Contratos por estado",
                    _conteo(etiquetas[k] for _, k, _ in elegidos), "pie"),
            _bloque("Contratos por tipo",
                    _conteo(c.get("tipo") for c in items), "pie"),
            _bloque("Costo por proveedor", costo(lambda c: c.get("proveedor")), "bar"),
            _bloque("Costo por tipo", costo(lambda c: c.get("tipo")), "bar"),
            _bloque("Vencimientos por mes",
                    _por_periodo((c.get("fecha_fin") for c in items), 7), "bar"),
        ]

    encabezados = [
        "Tipo", "Proveedor", "N. contrato", "Renovacion", "Inicio", "Fin",
        "Dias para vencer", "Costo", "Moneda", "Estado", "Activo", "Notas",
    ]
    return _informe("Contratos", encabezados, len(elegidos), filas, resumen)


def _licencias():
    tipos, provs = _multi("tipo"), _multi("proveedor")
    estados = _multi("estado")
    vdesde, vhasta = _rango_args("vence_desde", "vence_hasta")
    q = _terminos()

    hoy = _hoy()
    hoy_s = hoy.isoformat()
    limite = (hoy + timedelta(days=90)).isoformat()
    etiquetas = {
        "vencida": "Vencida", "por_vencer": "Vence en 90 dias o menos",
        "vigente": "Vigente", "sin_vencimiento": "Sin vencimiento",
    }

    try:
        software = {s.get("id"): s for s in _tabla("software", "*", "id")}
        lista = _tabla("licencias", "*", "id")
    except Exception as error:
        print(f"Error leyendo licencias: {error}")
        software, lista = {}, []

    elegidas = []
    for l in lista:
        s = software.get(l.get("software_id"), {})
        vence = _dia(l.get("fecha_vence"))
        if not vence:
            clave = "sin_vencimiento"
        elif vence < hoy_s:
            clave = "vencida"
        elif vence <= limite:
            clave = "por_vencer"
        else:
            clave = "vigente"

        if not _ok(l.get("tipo"), tipos):
            continue
        if not _ok(l.get("proveedor"), provs):
            continue
        if estados and clave not in estados:
            continue
        if not _en_rango(l.get("fecha_vence"), vdesde, vhasta):
            continue
        if q and not _coincide(q, s.get("nombre"), s.get("fabricante"), l.get("proveedor")):
            continue
        elegidas.append((l, s, clave))

    def filas():
        return [
            [
                s.get("nombre"), s.get("fabricante"), s.get("version"),
                l.get("tipo"), l.get("cantidad"), l.get("usadas"),
                l.get("proveedor"), _dia(l.get("fecha_vence")), etiquetas[clave],
                _num(l.get("costo")),
            ]
            for l, s, clave in elegidas
        ]

    def resumen():
        por_tipo = Counter()
        por_software = Counter()
        for l, s, _ in elegidas:
            n = int(_num(l.get("cantidad")) or 0)
            por_tipo[_txt(l.get("tipo")) or "Sin dato"] += n
            por_software[_txt(s.get("nombre")) or "Sin dato"] += n

        items = [l for l, _, _ in elegidas]
        nombre_sw = {id(l): _txt(s.get("nombre")) for l, s, _ in elegidas}

        return [
            _bloque("Licencias por estado de vencimiento",
                    _conteo(etiquetas[k] for *_, k in elegidas), "pie"),
            _bloque("Cantidad de licencias por tipo", por_tipo.most_common(15), "pie"),
            _bloque("Cantidad de licencias por software (top 15)",
                    por_software.most_common(15), "bar"),
            _bloque("Costo por proveedor",
                    _suma(items, lambda l: l.get("proveedor"),
                          lambda l: l.get("costo"), lambda l: ""), "bar"),
            _bloque("Costo por software",
                    _suma(items, lambda l: nombre_sw.get(id(l)),
                          lambda l: l.get("costo"), lambda l: ""), "bar"),
            _bloque("Vencimientos por mes",
                    _por_periodo((l.get("fecha_vence") for l in items), 7), "bar"),
        ]

    encabezados = [
        "Software", "Fabricante", "Version", "Tipo", "Cantidad", "Usadas",
        "Proveedor", "Vence", "Estado", "Costo",
    ]
    return _informe("Licencias", encabezados, len(elegidas), filas, resumen)


def _auditoria():
    """Quien cambio que en los activos (tabla historial_cambios)."""
    usuarios, campos = _multi("usuario"), _multi("campo")
    desde, hasta = _rango_args("desde", "hasta")
    q = _terminos()

    activos = _activos_id()
    try:
        lista = _tabla("historial_cambios", "*", "id")
    except Exception as error:
        print(f"Error leyendo historial_cambios: {error}")
        lista = []

    elegidos = []
    for h in lista:
        a = activos.get(h.get("activo_id"), {})
        if not _ok(h.get("usuario_nombre"), usuarios):
            continue
        if not _ok(h.get("campo"), campos):
            continue
        if not _en_rango(h.get("fecha"), desde, hasta):
            continue
        if q and not _coincide(
            q, a.get("codigo"), h.get("valor_anterior"), h.get("valor_nuevo")
        ):
            continue
        elegidos.append((h, a))

    elegidos.sort(key=lambda x: x[0].get("id") or 0, reverse=True)

    def filas():
        return [
            [
                _fecha_hora(h.get("fecha")), a.get("codigo") or h.get("activo_id"),
                a.get("tipo"), h.get("usuario_nombre"), h.get("campo"),
                h.get("valor_anterior"), h.get("valor_nuevo"),
            ]
            for h, a in elegidos
        ]

    def resumen():
        return [
            _bloque("Cambios por campo",
                    _conteo(h.get("campo") for h, _ in elegidos), "bar"),
            _bloque("Cambios por usuario",
                    _conteo(h.get("usuario_nombre") for h, _ in elegidos), "bar"),
            _bloque("Equipos con mas cambios (top 10)",
                    _conteo((a.get("codigo") for _, a in elegidos if a.get("codigo")),
                            tope=10), "bar"),
            _bloque("Cambios por mes",
                    _por_periodo((h.get("fecha") for h, _ in elegidos), 7), "bar"),
        ]

    encabezados = [
        "Fecha", "Activo", "Tipo de equipo", "Usuario", "Campo",
        "Valor anterior", "Valor nuevo",
    ]
    return _informe("Auditoria", encabezados, len(elegidos), filas, resumen)


CONSTRUCTORES = {
    "inventario": _inventario,
    "personas": _personas,
    "tickets": _tickets,
    "movimientos": _movimientos,
    "historial": _historial,
    "actas": _actas,
    "financiero": _financiero,
    "contratos": _contratos,
    "licencias": _licencias,
    "auditoria": _auditoria,
}

# Hojas que lleva el informe consolidado
CONSOLIDADO = ["inventario", "personas", "tickets", "movimientos",
               "historial", "actas", "financiero"]


def _clave_filtros():
    """Los filtros no vacios de la peticion (para el cache)."""
    return tuple(sorted(
        (k, tuple(v.strip() for v in request.args.getlist(k)))
        for k in request.args
        if any(v.strip() for v in request.args.getlist(k))
    ))


def _ejecutar(nombre):
    """Informe filtrado (liviano). Contar y descargar con los mismos filtros
    comparten este resultado."""
    return _memo(("informe", nombre, _clave_filtros()),
                 CONSTRUCTORES[nombre], TTL_INFORMES)


def _completo(nombre):
    """Informe con filas, resumen y hojas extra listos para el Excel."""
    return _materializar(_ejecutar(nombre))


def _con_extras(partes):
    hojas = []
    for p in partes:
        hojas.append(p)
        hojas.extend(p.get("extras", []))
    return hojas


# ==================================================
# RUTAS
# ==================================================

def _opciones():
    """Listas para los desplegables de filtros."""

    def construir():
        activos = _activos()
        personas = _personas_lista()

        def unicos(valores):
            vistos = {}
            for v in valores:
                t = _txt(v)
                if t:
                    vistos.setdefault(t.lower(), t)
            return sorted(vistos.values(), key=str.lower)

        por_area = {}
        for p in personas:
            por_area.setdefault(_txt(p.get("area")), []).append(_txt(p.get("nombre")))

        def distintos(tabla, col):
            try:
                return unicos(f.get(col) for f in _tabla(tabla, col))
            except Exception:
                return []

        return {
            "proveedores": unicos(a.get("proveedor") for a in activos),
            "centros": unicos(a.get("centro_costo") for a in activos),
            "sedes": unicos(a.get("sede") for a in activos),
            "tipos_contrato": distintos("contratos", "tipo"),
            "proveedores_contrato": distintos("contratos", "proveedor"),
            "proveedores_licencia": distintos("licencias", "proveedor"),
            "usuarios_auditoria": distintos("historial_cambios", "usuario_nombre"),
            "campos_auditoria": distintos("historial_cambios", "campo"),
            "tipos": unicos(a.get("tipo") for a in activos),
            "marcas": unicos(a.get("marca") for a in activos),
            "fisicos": unicos(a.get("estado") for a in activos),
            "areas": unicos([p.get("area") for p in personas]
                            + [a.get("area") for a in activos]),
            "cargos": unicos(p.get("cargo") for p in personas),
            "personas": unicos(p.get("nombre") for p in personas),
            "personas_por_area": {k: v for k, v in por_area.items() if k},
            "acciones_historial": distintos("historial", "accion"),
        }

    return _memo(("opciones",), construir, TTL_OPCIONES)


def _resumen_pagina():
    activos = _activos()
    tickets = _tabla("tickets", "*", "id")
    hoy_s, limite = _limites_garantia()
    ops = [estado_operativo(a) for a in activos]  # una sola vez por activo

    resumen = {
        "activos": len(activos),
        "personas": len(_personas_lista()),
        "tickets": len(tickets),
        "actas": _contar("actas"),
        "historial": _contar("historial"),
        "movimientos": _contar("movimientos"),
        "disponibles": sum(1 for o in ops if o == "disponible"),
        "asignados": sum(1 for o in ops if o == "asignado"),
        "reparacion": sum(1 for o in ops if "repar" in o),
        "tickets_abiertos": sum(1 for t in tickets if not _es_resuelto(t)),
        "garantias": sum(
            1 for a, o in zip(activos, ops)
            if "baja" not in o
            and _estado_garantia(a, hoy_s, limite) in ("Por vencer", "Vencida")
        ),
        "contratos": _contar("contratos"),
        "licencias": _contar("licencias"),
        "auditoria": _contar("historial_cambios"),
    }
    try:
        fin = resumen_financiero(_vista_fin())
    except Exception as error:
        print(f"Error en resumen financiero: {error}")
        fin = None
    return resumen, fin


def registrar_rutas(app):

    @app.after_request
    def _refrescar_cache(respuesta):
        # Si la app modifico datos, los reportes deben verlos ya
        if request.method in ("POST", "PUT", "PATCH", "DELETE") \
                and respuesta.status_code < 400:
            invalidar_cache()
        return respuesta

    @app.route("/reportes")
    @login_required
    @permiso_requerido("reportes", "ver")
    def reportes():
        resumen, fin = _memo(("pagina_resumen",), _resumen_pagina)
        return render_template(
            "reportes.html", resumen=resumen, opc=_opciones(), fin=fin
        )

    @app.route("/reportes/contar/<nombre>")
    @login_required
    @permiso_requerido("reportes", "ver")
    def reporte_contar(nombre):
        if nombre not in CONSTRUCTORES:
            abort(404)
        return jsonify({"total": _ejecutar(nombre)["total"]})

    @app.route("/reportes/<nombre>.xlsx")
    @login_required
    @permiso_requerido("reportes", "ver")
    def reporte_xlsx(nombre):
        try:
            if nombre == "consolidado":
                partes = [_completo(n) for n in CONSOLIDADO]
                resumen = [_bloque(
                    "Registros por hoja",
                    [(p["hoja"], len(p["filas"])) for p in partes],
                    "bar",
                )]
                for p in partes:
                    for b in p["resumen"][:3]:
                        resumen.append({**b, "titulo": f"{p['hoja']}: {b['titulo']}"})
                return respuesta_libro(
                    f"consolidado_{_sello()}.xlsx", _con_extras(partes), resumen
                )

            if nombre not in CONSTRUCTORES:
                abort(404)
            datos = _completo(nombre)
            return respuesta_libro(
                f"{nombre}_{_sello()}.xlsx", _con_extras([datos]), datos["resumen"]
            )
        except ImportError:
            flash("Falta instalar openpyxl (pip install openpyxl).", "danger")
            return redirect(url_for("reportes"))

    @app.route("/reportes/actas.zip")
    @login_required
    @permiso_requerido("reportes", "ver")
    def reporte_actas():
        carpeta = Path("pdf").resolve()
        buffer = io.BytesIO()
        incluidos = set()

        # Los PDF ya vienen comprimidos: guardarlos tal cual es mucho mas rapido
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as zf:
            for acta, _ in _actas_filtradas():
                nombre = Path(_txt(acta.get("ruta_pdf"))).name
                if not nombre or nombre in incluidos:
                    continue
                ruta = carpeta / nombre
                if ruta.is_file():
                    zf.write(ruta, nombre)
                    incluidos.add(nombre)

        if not incluidos:
            flash("No hay archivos PDF de actas con esos filtros.", "warning")
            return redirect(url_for("reportes"))

        buffer.seek(0)
        return send_file(
            buffer,
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"actas_{_sello()}.zip",
        )