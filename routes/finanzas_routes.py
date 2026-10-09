from flask import flash, jsonify, redirect, request, url_for
from flask_login import login_required

from auth.decorators import permiso_requerido
from services.excel_service import respuesta_excel
from services.finanzas_service import (
    gasto_proveedor_anio,
    leer_vista_financiera,
    resumen_financiero,
)
from services.db_service import hoy_colombia


@login_required
@permiso_requerido("inventario", "ver")
def finanzas_resumen():
    """JSON con totales y gasto por proveedor/año (para futuros gráficos)."""
    try:
        filas = leer_vista_financiera()
        return jsonify({
            "resumen": resumen_financiero(filas),
            "gasto_proveedor_anio": gasto_proveedor_anio(),
        })
    except Exception as error:
        print(f"Error en resumen financiero: {error}")
        return jsonify({"error": "No fue posible leer los datos."}), 500


@login_required
@permiso_requerido("reportes", "ver")
def reporte_finanzas():
    """Excel financiero. Filtro opcional: ?garantia=vencida|por_vencer|vigente|sin_garantia"""
    filtro = request.args.get("garantia", "").strip().lower()

    try:
        filas = leer_vista_financiera("codigo")
    except Exception as error:
        print(f"Error leyendo vista financiera: {error}")
        flash("No fue posible leer los datos financieros.", "danger")
        return redirect(url_for("reportes"))

    if filtro == "por_vencer":
        filas = [f for f in filas if str(f.get("estado_garantia")).startswith("por_vencer")]
    elif filtro in {"vencida", "vigente", "sin_garantia"}:
        filas = [f for f in filas if f.get("estado_garantia") == filtro]

    columnas = [
        ("Código", "codigo"), ("Tipo", "tipo"), ("Marca", "marca"),
        ("Modelo", "modelo"), ("Serial", "serial"),
        ("Disponibilidad", "disponibilidad"), ("Área", "area"),
        ("Sede", "sede"), ("Centro de costo", "centro_costo"),
        ("Proveedor", "proveedor"), ("N° factura", "nro_factura"),
        ("N° orden", "nro_orden"), ("Moneda", "moneda"),
        ("Precio compra", "precio_compra"), ("Valor residual", "valor_residual"),
        ("Fecha compra", "fecha_compra"),
        ("Inicio depreciación", "fecha_inicio_depreciacion"),
        ("Vida útil (meses)", "vida_util_meses"), ("Meses de uso", "meses_uso"),
        ("Depreciación acumulada", "depreciacion_acumulada"),
        ("Valor actual", "valor_actual"),
        ("Garantía inicio", "garantia_inicio"), ("Garantía meses", "garantia_meses"),
        ("Garantía fin", "garantia_fin"), ("Días de garantía", "dias_garantia"),
        ("Estado garantía", "estado_garantia"),
    ]

    try:
        return respuesta_excel(
            f"finanzas_{hoy_colombia().strftime('%Y%m%d')}.xlsx",
            "Finanzas",
            [c[0] for c in columnas],
            [[f.get(c[1]) for c in columnas] for f in filas],
        )
    except ImportError:
        flash("Falta instalar openpyxl (pip install openpyxl).", "danger")
        return redirect(url_for("reportes"))


def registrar_rutas(app):
    app.add_url_rule("/finanzas/resumen.json", endpoint="finanzas_resumen",
                     view_func=finanzas_resumen, methods=["GET"])
    app.add_url_rule("/reportes/finanzas.xlsx", endpoint="reporte_finanzas",
                     view_func=reporte_finanzas, methods=["GET"])