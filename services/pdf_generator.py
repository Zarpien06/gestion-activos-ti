"""Generador de actas PDF - Gestion de Activos TI.

Uso:
    ruta_pdf, archivo_pdf = generar_acta_pdf(
        persona, activos, tipo, observaciones, config=obtener_config(),
        activos_entregados=None,
    )

Tipos: "Entrega", "Devolucion", "Cambio", "Paz y salvo".

- Entrega / Paz y salvo: `activos` es la lista de equipos del acta.
- Devolucion: `activos` son los equipos devueltos. Si cada activo trae la clave
  "estado_devolucion" se muestra una columna ESTADO.
- Cambio: `activos` son los equipos DEVUELTOS (con "estado_devolucion") y
  `activos_entregados` los equipos NUEVOS que recibe la persona.

El acta se ajusta automaticamente para quedar en UNA sola hoja: si el contenido
no cabe, se vuelve a generar con fuentes y espacios un poco mas compactos
(ver ESCALAS) hasta que quepa, dejando siempre la firma al final.

Con `config` toma de la configuracion del sistema:
    - nombre_empresa  -> encabezado, pie, clausula y autor del PDF
    - correo_soporte  -> pie de pagina
    - texto_clausula  -> clausula editable (usa {empresa} para insertar el nombre)
Si no se pasa config, usa los valores por defecto de este archivo.
"""
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
import re

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

EMPRESA = "Planeta de Agostini Formación Colombia S.A.S"
CARPETA_PDF = Path("pdf")
LOGO = Path("static") / "logo.png"  # opcional: si existe, se dibuja en el encabezado

# Escalas que se prueban hasta que el acta quepa en una sola hoja.
# 1.0 = tamano normal. Si ni con la ultima cabe, se deja en varias hojas.
ESCALAS = (1.0, 0.94, 0.88, 0.82, 0.76, 0.70, 0.64)

# Colores
AZUL = colors.HexColor("#1F3A5F")
AZUL_CLARO = colors.HexColor("#E8EEF6")
GRIS = colors.HexColor("#6B7280")
LINEA = colors.HexColor("#D1D5DB")
ZEBRA = colors.HexColor("#F7F9FC")

# Textos por tipo de acta
TEXTOS = {
    "Entrega": {
        "titulo": "ACTA DE ENTREGA DE EQUIPOS",
        "intro": "Por medio de la presente se hace entrega de los siguientes equipos:",
    },
    "Devolucion": {
        "titulo": "ACTA DE DEVOLUCIÓN DE EQUIPOS",
        "intro": "Por medio de la presente se recibe la devolución de los siguientes equipos:",
    },
    "Cambio": {
        "titulo": "ACTA DE CAMBIO DE EQUIPOS",
        "intro": "Por medio de la presente se hace constar el cambio de los siguientes equipos:",
    },
    "Paz y salvo": {
        "titulo": "ACTA DE PAZ Y SALVO DE EQUIPOS",
        "intro": (
            "Por medio de la presente se certifica que el colaborador se encuentra "
            "a paz y salvo por concepto de los siguientes equipos:"
        ),
    },
}

CLAUSULA = (
    "Los equipos entregados son y serán de {empresa} en todo momento. En caso de "
    "terminación de contrato o entrega de un nuevo equipo, el colaborador se compromete "
    "a hacer la devolución inmediata de el/los equipos, de lo contrario se harán los "
    "descuentos a los que haya lugar. En caso de daño por uso o defecto de fábrica el "
    "colaborador debe devolverlo a la compañía. En caso de robo o pérdida el colaborador "
    "deberá notificarlo inmediatamente a la compañía."
)
RESPONSABILIDAD = (
    "La persona que firma será responsable de los equipos entregados y se compromete "
    "a cuidar y hacer buen uso de estos."
)

# Tipos de acta en los que se muestra la frase de responsabilidad
TIPOS_CON_RESPONSABILIDAD = {"Entrega", "Cambio", "Paz y salvo"}


# ---------- Estilos ----------
def _estilos(k=1.0):
    """Estilos de texto. `k` escala tamano de letra e interlineado."""

    def ps(nombre, parent=None, **kw):
        if "fontSize" in kw:
            kw["fontSize"] = kw["fontSize"] * k
        if "leading" in kw:
            kw["leading"] = kw["leading"] * k
        return ParagraphStyle(nombre, parent=parent, **kw)

    base = ps("base", fontName="Helvetica", fontSize=9.5, leading=13)
    return {
        "titulo": ps("titulo", base, fontName="Helvetica-Bold", fontSize=16,
                     leading=20, textColor=AZUL, alignment=TA_CENTER),
        "intro": ps("intro", base, fontSize=10, textColor=colors.HexColor("#374151")),
        "label": ps("label", base, fontName="Helvetica-Bold", fontSize=9, textColor=AZUL),
        "valor": ps("valor", base, fontSize=9.5),
        "th": ps("th", base, fontName="Helvetica-Bold", fontSize=8.5,
                 textColor=colors.white, leading=11),
        "td": ps("td", base, fontSize=9, leading=12),
        "td_c": ps("td_c", base, fontSize=9, leading=12, alignment=TA_CENTER),
        "caja": ps("caja", base, fontSize=8.8, leading=12.5, alignment=TA_JUSTIFY),
        "obs": ps("obs", base, fontSize=9.5, leading=13),
        "resp": ps("resp", base, fontSize=9.5, alignment=TA_JUSTIFY),
        "firma": ps("firma", base, fontSize=9, alignment=TA_CENTER, leading=12),
        "firma_b": ps("firma_b", base, fontName="Helvetica-Bold", fontSize=9.5,
                      alignment=TA_CENTER, textColor=AZUL),
        "mini": ps("mini", base, fontSize=8.5, alignment=TA_CENTER, textColor=GRIS),
    }


# ---------- Utilidades ----------
def _esc(valor):
    """Escapa texto para Paragraph de reportlab."""
    texto = "" if valor is None else str(valor)
    return texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _primero(dic, *claves, default=""):
    for clave in claves:
        valor = dic.get(clave)
        if valor not in (None, ""):
            return valor
    return default


def _fecha_hoy():
    # Hora de Colombia (UTC-5) sin depender de zoneinfo/tzdata en Windows
    ahora = datetime.now(timezone.utc) - timedelta(hours=5)
    return ahora.strftime("%d/%m/%Y"), ahora.strftime("%Y%m%d_%H%M%S")


def _slug(texto):
    texto = re.sub(r"[^a-z0-9]+", "_", str(texto or "").strip().lower()).strip("_")
    return texto[:40] or "persona"


# ---------- Pie y encabezado de pagina ----------
def _decorar_pagina(canvas, doc):
    ancho, alto = A4
    empresa = getattr(doc, "empresa", EMPRESA)
    correo = getattr(doc, "correo", "")
    alto_barra = 22 * mm
    canvas.saveState()

    # Barra superior
    canvas.setFillColor(AZUL)
    canvas.rect(0, alto - alto_barra, ancho, alto_barra, stroke=0, fill=1)

    x_texto = 18 * mm
    if LOGO.exists():
        try:
            lector = ImageReader(str(LOGO))
            iw, ih = lector.getSize()
            # Cabe en 14 mm de alto y 40 mm de ancho, sin deformarse
            h = 14 * mm
            w = h * iw / ih
            if w > 40 * mm:
                w = 40 * mm
                h = w * ih / iw
            y = alto - alto_barra / 2 - h / 2  # centrado en la barra
            canvas.drawImage(lector, 18 * mm, y, width=w, height=h, mask="auto")
            x_texto = 18 * mm + w + 6 * mm
        except Exception:
            x_texto = 18 * mm

    # El nombre de la empresa se achica si no cabe junto al logo
    disponible = ancho - 18 * mm - x_texto
    tam = 12
    while tam > 8 and stringWidth(empresa, "Helvetica-Bold", tam) > disponible:
        tam -= 0.5

    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", tam)
    canvas.drawString(x_texto, alto - 11 * mm, empresa)
    canvas.setFont("Helvetica", 8.5)
    canvas.drawString(x_texto, alto - 16 * mm, "Gestión de Activos TI")

    # Pie
    canvas.setStrokeColor(LINEA)
    canvas.setLineWidth(0.6)
    canvas.line(18 * mm, 16 * mm, ancho - 18 * mm, 16 * mm)
    canvas.setFillColor(GRIS)
    canvas.setFont("Helvetica", 8)
    pie = f"{empresa}  ·  Soporte: {correo}" if correo else empresa
    canvas.drawString(18 * mm, 11 * mm, pie[:140])
    canvas.drawRightString(ancho - 18 * mm, 11 * mm, f"Página {doc.page}")

    canvas.restoreState()


# ---------- Bloques ----------
def _bloque_datos(persona, fecha, st, k=1.0):
    nombre = _primero(persona, "nombre")
    cargo = _primero(persona, "cargo")
    jefe = _primero(persona, "jefe", "jefe_inmediato", "responsable")
    area = _primero(persona, "area")
    documento = _primero(persona, "documento")

    filas = [
        ("Fecha", fecha),
        ("Nombre", nombre),
        ("Documento", documento),
        ("Correo", _primero(persona, "correo")),
        ("Cargo", cargo),
        ("Área", area),
        ("Responsable / jefe inmediato", jefe),
    ]
    data = [[Paragraph(_esc(a), st["label"]), Paragraph(_esc(v), st["valor"])] for a, v in filas]

    tabla = Table(data, colWidths=[55 * mm, 119 * mm])
    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), AZUL_CLARO),
        ("BOX", (0, 0), (-1, -1), 0.6, LINEA),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, LINEA),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5 * k),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5 * k),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return tabla


def _bloque_equipos(activos, st, con_estado=False, k=1.0):
    """Tabla de equipos. Con `con_estado` agrega la columna ESTADO
    (usa la clave "estado_devolucion" de cada activo)."""
    columnas = ["ÍTEM", "MARCA / MODELO", "NÚMERO SERIAL", "NO. INVENTARIO"]
    anchos = [42, 58, 44, 30]
    if con_estado:
        columnas.append("ESTADO")
        anchos = [34, 46, 36, 26, 32]

    data = [[Paragraph(c, st["th"]) for c in columnas]]

    for activo in activos:
        item = _primero(activo, "tipo", "categoria", "nombre", "equipo", default="Equipo")
        marca = _primero(activo, "marca")
        modelo = _primero(activo, "modelo")
        marca_modelo = " ".join(p for p in (str(marca), str(modelo)) if p).strip() or "N/A"
        serial = _primero(activo, "serial", "numero_serial", "serie", default="S/N")
        inventario = _primero(activo, "codigo", "inventario", "id")

        fila = [
            Paragraph(_esc(item), st["td"]),
            Paragraph(_esc(marca_modelo), st["td"]),
            Paragraph(_esc(serial), st["td"]),
            Paragraph(_esc(inventario), st["td_c"]),
        ]
        if con_estado:
            fila.append(Paragraph(_esc(activo.get("estado_devolucion") or ""), st["td"]))
        data.append(fila)

    if len(data) == 1:
        data.append(
            [Paragraph("La persona no tiene equipos asignados.", st["td"])]
            + [""] * (len(columnas) - 1)
        )

    tabla = Table(data, colWidths=[a * mm for a in anchos], repeatRows=1)
    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("BOX", (0, 0), (-1, -1), 0.6, LINEA),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, LINEA),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5 * k),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5 * k),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            estilo.append(("BACKGROUND", (0, i), (-1, i), ZEBRA))
    if len(data) == 2 and not activos:
        estilo.append(("SPAN", (0, 1), (-1, 1)))
    tabla.setStyle(TableStyle(estilo))
    return tabla


def _bloque_clausula(texto, st, k=1.0):
    # Respeta los saltos de linea que se escriban en la configuracion
    contenido = _esc(texto).replace("\n", "<br/>")
    tabla = Table([[Paragraph(contenido, st["caja"])]], colWidths=[174 * mm])
    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), AZUL_CLARO),
        ("LINEBEFORE", (0, 0), (0, -1), 3, AZUL),
        ("TOPPADDING", (0, 0), (-1, -1), 8 * k),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8 * k),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return tabla


def _bloque_firmas(persona, st, tipo="Entrega", k=1.0):
    """Quién firma en cada lado depende del tipo de acta:
    - Devolucion: la persona ENTREGA y Sistemas RECIBE.
    - Cambio: Sistemas a la izquierda y la persona (devuelve y recibe) a la derecha.
    - Resto: Sistemas ENTREGA y la persona RECIBE.
    """
    nombre = _primero(persona, "nombre")
    documento = _primero(persona, "documento")

    en_blanco = (
        Paragraph("Nombre: ____________________", st["firma"]),
        Paragraph("CC: ________________________", st["firma"]),
    )
    de_persona = (
        Paragraph(f"Nombre: {_esc(nombre)}", st["firma"]),
        Paragraph(f"CC: {_esc(documento) or '____________________'}", st["firma"]),
    )

    if tipo == "Devolucion":
        izquierda = ("ENTREGA", de_persona)
        derecha = ("RECIBE", en_blanco)
    elif tipo == "Cambio":
        izquierda = ("SISTEMAS", en_blanco)
        derecha = ("COLABORADOR (DEVUELVE Y RECIBE)", de_persona)
    else:
        izquierda = ("ENTREGA", en_blanco)
        derecha = ("RECIBE", de_persona)

    # Espacio para firmar: se reduce poco para que siempre se pueda firmar
    espacio_firma = max(10, 13 * k) * mm

    data = [
        [Paragraph(izquierda[0], st["firma_b"]), "", Paragraph(derecha[0], st["firma_b"])],
        [Spacer(1, espacio_firma), "", Spacer(1, espacio_firma)],
        [izquierda[1][0], "", derecha[1][0]],
        [izquierda[1][1], "", derecha[1][1]],
    ]
    tabla = Table(data, colWidths=[78 * mm, 18 * mm, 78 * mm])
    tabla.setStyle(TableStyle([
        ("LINEABOVE", (0, 2), (0, 2), 0.8, colors.black),
        ("LINEABOVE", (2, 2), (2, 2), 0.8, colors.black),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 2), (-1, 2), 4),
    ]))
    return tabla


def _bloque_control(st, k=1.0):
    data = [
        [Paragraph(t, st["th"]) for t in ("DD/MM/AAAA", "ENTREGA", "RECIBE", "PAZ Y SALVO")],
        ["", "", "", ""],
    ]
    tabla = Table(data, colWidths=[43.5 * mm] * 4, rowHeights=[None, max(7, 9 * k) * mm])
    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("BOX", (0, 0), (-1, -1), 0.6, LINEA),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, LINEA),
        ("TOPPADDING", (0, 0), (-1, 0), 4),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
    ]))
    return tabla


# ---------- Armado del documento ----------
def _construir(destino, k, persona, activos, tipo, observaciones, empresa,
               correo, texto_clausula, activos_entregados, fecha):
    """Arma el PDF en `destino` (ruta o buffer) con escala `k`.
    Devuelve el numero de hojas generadas."""
    st = _estilos(k)
    textos = TEXTOS.get(tipo, TEXTOS["Entrega"])

    def esp(valor_mm):
        return Spacer(1, valor_mm * mm * k)

    doc = BaseDocTemplate(
        destino,
        pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=28 * mm, bottomMargin=20 * mm,
        title=textos["titulo"],
        author=empresa,
    )
    doc.empresa = empresa
    doc.correo = correo
    marco = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="principal")
    doc.addPageTemplates([PageTemplate(id="acta", frames=[marco], onPage=_decorar_pagina)])

    historia = [
        Paragraph(textos["titulo"], st["titulo"]),
        esp(4),
        Paragraph(_esc(textos["intro"]), st["intro"]),
        esp(4),
        _bloque_datos(persona, fecha, st, k),
        esp(6),
    ]

    # Tablas de equipos
    if tipo == "Cambio":
        historia += [
            Paragraph("EQUIPOS DEVUELTOS", st["label"]),
            esp(1.5),
            _bloque_equipos(activos, st, con_estado=True, k=k),
            esp(5),
            Paragraph("EQUIPOS ENTREGADOS", st["label"]),
            esp(1.5),
            _bloque_equipos(activos_entregados or [], st, k=k),
            esp(6),
        ]
    else:
        con_estado = tipo == "Devolucion" and any(
            a.get("estado_devolucion") for a in activos
        )
        historia += [
            _bloque_equipos(activos, st, con_estado=con_estado, k=k),
            esp(6),
        ]

    if observaciones:
        historia += [
            Paragraph("OBSERVACIONES", st["label"]),
            esp(1.5),
            Paragraph(_esc(observaciones).replace("\n", "<br/>"), st["obs"]),
            esp(4),
        ]

    historia += [
        _bloque_clausula(texto_clausula, st, k),
        esp(4),
    ]

    if tipo in TIPOS_CON_RESPONSABILIDAD:
        historia += [
            Paragraph(_esc(RESPONSABILIDAD), st["resp"]),
            esp(5),
        ]
    else:
        historia.append(esp(2))

    # La firma y el control van siempre juntos (nunca separados)
    historia.append(
        KeepTogether([
            _bloque_firmas(persona, st, tipo, k),
            esp(6),
            _bloque_control(st, k),
        ])
    )

    doc.build(historia)
    return doc.page


# ---------- Funcion principal ----------
def generar_acta_pdf(
    persona,
    activos,
    tipo="Entrega",
    observaciones="",
    config=None,
    activos_entregados=None,
):
    """Genera el acta (en una sola hoja) y devuelve (ruta_completa, nombre_archivo)."""
    cfg = config or {}
    empresa = str(cfg.get("nombre_empresa") or "").strip() or EMPRESA
    correo = str(cfg.get("correo_soporte") or "").strip()
    texto_clausula = str(cfg.get("texto_clausula") or "").strip() or CLAUSULA
    # replace (y no format) para que las llaves del texto editable no fallen
    texto_clausula = texto_clausula.replace("{empresa}", empresa)

    activos = activos or []
    fecha, marca_tiempo = _fecha_hoy()

    CARPETA_PDF.mkdir(parents=True, exist_ok=True)
    nombre_archivo = f"acta_{_slug(tipo)}_{_slug(persona.get('nombre'))}_{marca_tiempo}.pdf"
    ruta = (CARPETA_PDF / nombre_archivo).resolve()

    args = (persona, activos, tipo, observaciones, empresa, correo,
            texto_clausula, activos_entregados, fecha)

    # Busca la escala mas grande con la que todo cabe en 1 hoja
    escala = ESCALAS[-1]
    for k in ESCALAS:
        if _construir(BytesIO(), k, *args) <= 1:
            escala = k
            break

    _construir(str(ruta), escala, *args)
    return str(ruta), nombre_archivo