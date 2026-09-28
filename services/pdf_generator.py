from datetime import datetime
from pathlib import Path
import re

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


PDF_DIR = Path("pdf")
PDF_DIR.mkdir(exist_ok=True)


def nombre_seguro(valor):
    valor = str(valor or "persona").strip().lower()
    valor = re.sub(r"[^a-z0-9]+", "_", valor)
    return valor.strip("_") or "persona"


def texto(valor, defecto=""):
    if valor is None:
        return defecto
    return str(valor).strip()


def generar_acta_pdf(
    persona,
    activos,
    tipo="Entrega",
    observaciones=""
):
    fecha = datetime.now()
    nombre_persona = texto(
        persona.get("nombre"),
        "Sin nombre"
    )

    archivo = (
        f"acta_{nombre_seguro(tipo)}_"
        f"{nombre_seguro(nombre_persona)}_"
        f"{fecha.strftime('%Y%m%d_%H%M%S')}.pdf"
    )

    ruta = PDF_DIR / archivo

    documento = SimpleDocTemplate(
        str(ruta),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=18 * mm,
        title=f"Acta de {tipo} - {nombre_persona}",
        author="Gestion de Activos TI",
    )

    estilos = getSampleStyleSheet()

    estilos.add(
        ParagraphStyle(
            name="TituloActa",
            parent=estilos["Title"],
            fontName="Helvetica-Bold",
            fontSize=16,
            leading=20,
            textColor=colors.HexColor("#0F172A"),
            spaceAfter=5 * mm,
        )
    )

    estilos.add(
        ParagraphStyle(
            name="SeccionActa",
            parent=estilos["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=13,
            textColor=colors.HexColor("#1E40AF"),
            spaceBefore=3 * mm,
            spaceAfter=2 * mm,
        )
    )

    estilos.add(
        ParagraphStyle(
            name="CuerpoActa",
            parent=estilos["BodyText"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=14,
            textColor=colors.HexColor("#334155"),
        )
    )

    elementos = []

    elementos.append(
        Paragraph(
            "GESTION DE ACTIVOS TI",
            estilos["TituloActa"],
        )
    )

    elementos.append(
        Paragraph(
            f"ACTA DE {texto(tipo).upper()}",
            estilos["TituloActa"],
        )
    )

    elementos.append(
        Paragraph(
            f"Fecha: {fecha.strftime('%d/%m/%Y %H:%M')}",
            estilos["CuerpoActa"],
        )
    )

    elementos.append(Spacer(1, 5 * mm))

    datos_persona = [
        ["Nombre", nombre_persona],
        [
            "Documento",
            texto(
                persona.get("documento"),
                "No registrado",
            ),
        ],
        [
            "Correo",
            texto(
                persona.get("correo"),
                "No registrado",
            ),
        ],
        [
            "Cargo",
            texto(
                persona.get("cargo"),
                "No registrado",
            ),
        ],
        [
            "Area",
            texto(
                persona.get("area"),
                "No registrada",
            ),
        ],
    ]

    tabla_persona = Table(
        datos_persona,
        colWidths=[38 * mm, 135 * mm],
    )

    tabla_persona.setStyle(
        TableStyle([
            (
                "BACKGROUND",
                (0, 0),
                (0, -1),
                colors.HexColor("#E2E8F0"),
            ),
            (
                "FONTNAME",
                (0, 0),
                (0, -1),
                "Helvetica-Bold",
            ),
            (
                "FONTSIZE",
                (0, 0),
                (-1, -1),
                9,
            ),
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "MIDDLE",
            ),
            (
                "GRID",
                (0, 0),
                (-1, -1),
                0.5,
                colors.HexColor("#CBD5E1"),
            ),
            (
                "LEFTPADDING",
                (0, 0),
                (-1, -1),
                7,
            ),
            (
                "RIGHTPADDING",
                (0, 0),
                (-1, -1),
                7,
            ),
            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                6,
            ),
            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                6,
            ),
        ])
    )

    elementos.append(
        Paragraph(
            "DATOS DEL COLABORADOR",
            estilos["SeccionActa"],
        )
    )

    elementos.append(tabla_persona)
    elementos.append(Spacer(1, 6 * mm))

    filas_activos = [[
        "Codigo",
        "Tipo",
        "Marca / Modelo",
        "Serial",
        "Estado",
    ]]

    for activo in activos:
        marca_modelo = (
            f"{texto(activo.get('marca'))} "
            f"{texto(activo.get('modelo'))}"
        ).strip()

        filas_activos.append([
            texto(activo.get("codigo")),
            texto(activo.get("tipo")),
            marca_modelo,
            texto(
                activo.get("serial"),
                "Sin serial",
            ),
            texto(
                activo.get("disponibilidad"),
                texto(activo.get("estado")),
            ),
        ])

    if len(filas_activos) == 1:
        filas_activos.append([
            "-",
            "Sin activos asignados",
            "-",
            "-",
            "-",
        ])

    tabla_activos = Table(
        filas_activos,
        repeatRows=1,
        colWidths=[
            24 * mm,
            30 * mm,
            53 * mm,
            43 * mm,
            28 * mm,
        ],
    )

    tabla_activos.setStyle(
        TableStyle([
            (
                "BACKGROUND",
                (0, 0),
                (-1, 0),
                colors.HexColor("#1D4ED8"),
            ),
            (
                "TEXTCOLOR",
                (0, 0),
                (-1, 0),
                colors.white,
            ),
            (
                "FONTNAME",
                (0, 0),
                (-1, 0),
                "Helvetica-Bold",
            ),
            (
                "FONTSIZE",
                (0, 0),
                (-1, -1),
                8,
            ),
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "MIDDLE",
            ),
            (
                "GRID",
                (0, 0),
                (-1, -1),
                0.5,
                colors.HexColor("#CBD5E1"),
            ),
            (
                "ROWBACKGROUNDS",
                (0, 1),
                (-1, -1),
                [
                    colors.white,
                    colors.HexColor("#F8FAFC"),
                ],
            ),
            (
                "LEFTPADDING",
                (0, 0),
                (-1, -1),
                5,
            ),
            (
                "RIGHTPADDING",
                (0, 0),
                (-1, -1),
                5,
            ),
            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                6,
            ),
            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                6,
            ),
        ])
    )

    elementos.append(
        Paragraph(
            "ACTIVOS RELACIONADOS",
            estilos["SeccionActa"],
        )
    )

    elementos.append(tabla_activos)
    elementos.append(Spacer(1, 6 * mm))

    texto_responsabilidad = (
        "El colaborador manifiesta haber recibido o entregado "
        "los activos descritos en esta acta y se compromete "
        "a informar oportunamente cualquier novedad, dano, "
        "perdida o cambio de ubicacion."
    )

    elementos.append(
        Paragraph(
            texto_responsabilidad,
            estilos["CuerpoActa"],
        )
    )

    elementos.append(Spacer(1, 4 * mm))

    elementos.append(
        Paragraph(
            "OBSERVACIONES",
            estilos["SeccionActa"],
        )
    )

    elementos.append(
        Paragraph(
            texto(
                observaciones,
                "Sin observaciones.",
            ),
            estilos["CuerpoActa"],
        )
    )

    elementos.append(Spacer(1, 18 * mm))

    tabla_firmas = Table(
        [
            [
                "_______________________________",
                "_______________________________",
            ],
            [
                "Firma del colaborador",
                "Firma de Sistemas",
            ],
            [
                nombre_persona,
                "Nombre: _______________________",
            ],
            [
                "Documento: ____________________",
                "Cargo: _________________________",
            ],
        ],
        colWidths=[86 * mm, 86 * mm],
    )

    tabla_firmas.setStyle(
        TableStyle([
            (
                "ALIGN",
                (0, 0),
                (-1, -1),
                "CENTER",
            ),
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "MIDDLE",
            ),
            (
                "FONTNAME",
                (0, 1),
                (-1, 1),
                "Helvetica-Bold",
            ),
            (
                "FONTSIZE",
                (0, 0),
                (-1, -1),
                9,
            ),
            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                4,
            ),
            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                4,
            ),
        ])
    )

    elementos.append(tabla_firmas)

    def pie_pagina(canvas, doc):
        canvas.saveState()
        canvas.setFont(
            "Helvetica",
            7.5,
        )
        canvas.setFillColor(
            colors.HexColor("#64748B")
        )
        canvas.drawString(
            18 * mm,
            10 * mm,
            "Gestion de Activos TI",
        )
        canvas.drawRightString(
            A4[0] - 18 * mm,
            10 * mm,
            f"Pagina {doc.page}",
        )
        canvas.restoreState()

    documento.build(
        elementos,
        onFirstPage=pie_pagina,
        onLaterPages=pie_pagina,
    )

    return str(ruta), archivo
