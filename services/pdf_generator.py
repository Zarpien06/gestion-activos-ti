"""Generador de actas PDF - Planeta de Agostini Formacion Colombia S.A.S.

Uso (igual que antes):
    ruta_pdf, archivo_pdf = generar_acta_pdf(persona, activos, tipo, observaciones)
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
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

# ---------- Estilos ----------
def _estilos():
    base = ParagraphStyle("base", fontName="Helvetica", fontSize=9.5, leading=13)
    return {
        "titulo": ParagraphStyle(
            "titulo", parent=base, fontName="Helvetica-Bold", fontSize=16,
            leading=20, textColor=AZUL, alignment=TA_CENTER,
        ),
        "intro": ParagraphStyle("intro", parent=base, fontSize=10, textColor=colors.HexColor("#374151")),
        "label": ParagraphStyle("label", parent=base, fontName="Helvetica-Bold", fontSize=9, textColor=AZUL),
        "valor": ParagraphStyle("valor", parent=base, fontSize=9.5),
        "th": ParagraphStyle("th", parent=base, fontName="Helvetica-Bold", fontSize=8.5,
                             textColor=colors.white, leading=11),
        "td": ParagraphStyle("td", parent=base, fontSize=9, leading=12),
        "td_c": ParagraphStyle("td_c", parent=base, fontSize=9, leading=12, alignment=TA_CENTER),
        "caja": ParagraphStyle("caja", parent=base, fontSize=8.8, leading=12.5, alignment=TA_JUSTIFY),
        "obs": ParagraphStyle("obs", parent=base, fontSize=9.5, leading=13),
        "resp": ParagraphStyle("resp", parent=base, fontSize=9.5, alignment=TA_JUSTIFY),
        "firma": ParagraphStyle("firma", parent=base, fontSize=9, alignment=TA_CENTER, leading=12),
        "firma_b": ParagraphStyle("firma_b", parent=base, fontName="Helvetica-Bold", fontSize=9.5,
                                  alignment=TA_CENTER, textColor=AZUL),
        "mini": ParagraphStyle("mini", parent=base, fontSize=8.5, alignment=TA_CENTER, textColor=GRIS),
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
    canvas.saveState()

    # Barra superior
    canvas.setFillColor(AZUL)
    canvas.rect(0, alto - 22 * mm, ancho, 22 * mm, stroke=0, fill=1)

    x_texto = 18 * mm
    if LOGO.exists():
        try:
            canvas.drawImage(
                str(LOGO), 18 * mm, alto - 18 * mm, height=14 * mm, width=40 * mm,
                preserveAspectRatio=True, mask="auto", anchor="w",
            )
            x_texto = 62 * mm
        except Exception:
            x_texto = 18 * mm

    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 12)
    canvas.drawString(x_texto, alto - 11 * mm, EMPRESA)
    canvas.setFont("Helvetica", 8.5)
    canvas.drawString(x_texto, alto - 16 * mm, "Gestión de Activos TI")

    # Pie
    canvas.setStrokeColor(LINEA)
    canvas.setLineWidth(0.6)
    canvas.line(18 * mm, 16 * mm, ancho - 18 * mm, 16 * mm)
    canvas.setFillColor(GRIS)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(18 * mm, 11 * mm, EMPRESA)
    canvas.drawRightString(ancho - 18 * mm, 11 * mm, f"Página {doc.page}")

    canvas.restoreState()


# ---------- Bloques ----------
def _bloque_datos(persona, fecha, st):
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
    data = [[Paragraph(_esc(k), st["label"]), Paragraph(_esc(v), st["valor"])] for k, v in filas]

    tabla = Table(data, colWidths=[55 * mm, 119 * mm])
    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), AZUL_CLARO),
        ("BOX", (0, 0), (-1, -1), 0.6, LINEA),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, LINEA),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return tabla


def _bloque_equipos(activos, st):
    encabezado = [
        Paragraph("ÍTEM", st["th"]),
        Paragraph("MARCA / MODELO", st["th"]),
        Paragraph("NÚMERO SERIAL", st["th"]),
        Paragraph("NO. INVENTARIO", st["th"]),
    ]
    data = [encabezado]

    for activo in activos:
        item = _primero(activo, "tipo", "categoria", "nombre", "equipo", default="Equipo")
        marca = _primero(activo, "marca")
        modelo = _primero(activo, "modelo")
        marca_modelo = " ".join(p for p in (str(marca), str(modelo)) if p).strip() or "N/A"
        serial = _primero(activo, "serial", "numero_serial", "serie", default="S/N")
        inventario = _primero(activo, "codigo", "inventario", "id")

        data.append([
            Paragraph(_esc(item), st["td"]),
            Paragraph(_esc(marca_modelo), st["td"]),
            Paragraph(_esc(serial), st["td"]),
            Paragraph(_esc(inventario), st["td_c"]),
        ])

    if len(data) == 1:
        data.append([Paragraph("La persona no tiene equipos asignados.", st["td"]), "", "", ""])

    tabla = Table(data, colWidths=[42 * mm, 58 * mm, 44 * mm, 30 * mm], repeatRows=1)
    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("BOX", (0, 0), (-1, -1), 0.6, LINEA),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, LINEA),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            estilo.append(("BACKGROUND", (0, i), (-1, i), ZEBRA))
    if len(data) == 2 and not activos:
        estilo.append(("SPAN", (0, 1), (-1, 1)))
    tabla.setStyle(TableStyle(estilo))
    return tabla


def _bloque_clausula(st):
    tabla = Table([[Paragraph(_esc(CLAUSULA.format(empresa=EMPRESA)), st["caja"])]], colWidths=[174 * mm])
    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), AZUL_CLARO),
        ("LINEBEFORE", (0, 0), (0, -1), 3, AZUL),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return tabla


def _bloque_firmas(persona, st):
    nombre = _primero(persona, "nombre")
    documento = _primero(persona, "documento")

    data = [
        [Paragraph("ENTREGA", st["firma_b"]), "", Paragraph("RECIBE", st["firma_b"])],
        [Spacer(1, 13 * mm), "", Spacer(1, 13 * mm)],
        [Paragraph("Nombre: ____________________", st["firma"]), "",
         Paragraph(f"Nombre: {_esc(nombre)}", st["firma"])],
        [Paragraph("CC: ________________________", st["firma"]), "",
         Paragraph(f"CC: {_esc(documento) or '____________________'}", st["firma"])],
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


def _bloque_control(st):
    data = [
        [Paragraph(t, st["th"]) for t in ("DD/MM/AAAA", "ENTREGA", "RECIBE", "PAZ Y SALVO")],
        ["", "", "", ""],
    ]
    tabla = Table(data, colWidths=[43.5 * mm] * 4, rowHeights=[None, 9 * mm])
    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("BOX", (0, 0), (-1, -1), 0.6, LINEA),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, LINEA),
        ("TOPPADDING", (0, 0), (-1, 0), 4),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
    ]))
    return tabla


# ---------- Funcion principal ----------
def generar_acta_pdf(persona, activos, tipo="Entrega", observaciones=""):
    """Genera el acta y devuelve (ruta_completa, nombre_archivo)."""
    st = _estilos()
    textos = TEXTOS.get(tipo, TEXTOS["Entrega"])
    fecha, marca_tiempo = _fecha_hoy()

    CARPETA_PDF.mkdir(parents=True, exist_ok=True)
    nombre_archivo = f"acta_{_slug(tipo)}_{_slug(persona.get('nombre'))}_{marca_tiempo}.pdf"
    ruta = (CARPETA_PDF / nombre_archivo).resolve()

    doc = BaseDocTemplate(
        str(ruta),
        pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=28 * mm, bottomMargin=20 * mm,
        title=textos["titulo"],
        author=EMPRESA,
    )
    marco = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="principal")
    doc.addPageTemplates([PageTemplate(id="acta", frames=[marco], onPage=_decorar_pagina)])

    historia = [
        Paragraph(textos["titulo"], st["titulo"]),
        Spacer(1, 4 * mm),
        Paragraph(_esc(textos["intro"]), st["intro"]),
        Spacer(1, 4 * mm),
        _bloque_datos(persona, fecha, st),
        Spacer(1, 6 * mm),
        _bloque_equipos(activos, st),
        Spacer(1, 6 * mm),
    ]

    if observaciones:
        historia += [
            Paragraph("OBSERVACIONES", st["label"]),
            Spacer(1, 1.5 * mm),
            Paragraph(_esc(observaciones).replace("\n", "<br/>"), st["obs"]),
            Spacer(1, 4 * mm),
        ]

    historia += [
        _bloque_clausula(st),
        Spacer(1, 4 * mm),
        Paragraph(_esc(RESPONSABILIDAD), st["resp"]),
        Spacer(1, 5 * mm),
        KeepTogether([
            _bloque_firmas(persona, st),
            Spacer(1, 6 * mm),
            _bloque_control(st),
        ]),
    ]

    doc.build(historia)
    return str(ruta), nombre_archivo