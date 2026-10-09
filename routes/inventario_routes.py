from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required

from auth.decorators import permiso_requerido
from services.auditoria_service import actualizar_con_auditoria
from services.config_service import obtener_config
from services.db_service import consultar_tabla, estado_operativo, obtener_registro
from services.documentos_service import subir_documento
from services.finanzas_service import campos_finanzas, datos_financieros
from services.historial_service import registrar_historial, registrar_movimiento
from services.storage_service import subir_factura, url_firmada
from supabase_client import supabase


DISPONIBILIDADES = {
    "Disponible",
    "En reparación",
    "Inhabilitado",
    "Dado de baja",
}


def _es_laptop(tipo):
    t = (tipo or "").lower()
    return "laptop" in t or "portatil" in t or "portátil" in t


# ==================================================
# FACTURAS
# ==================================================

@login_required
@permiso_requerido("inventario", "ver")
def ver_factura(ruta):
    """Redirige a un enlace temporal (5 min) de la factura."""
    try:
        return redirect(url_firmada(ruta, 300))
    except Exception as error:
        print(f"Error abriendo factura ruta={ruta!r}: {type(error).__name__}: {error}")
        flash(f"No fue posible abrir la factura ({type(error).__name__}).", "danger")
        return redirect(url_for("inventario"))


# ==================================================
# LISTADO
# ==================================================

@login_required
@permiso_requerido("inventario", "ver")
def inventario():
    activos = consultar_tabla("activos", "*", "codigo")

    personas_activas = [
        p
        for p in consultar_tabla("personas", "*", "nombre")
        if str(p.get("estado") or "Activo").strip().lower() == "activo"
    ]

    adicionales = {}

    for ad in consultar_tabla("activos_adicionales", "*", "id"):
        ad["factura_url"] = (
            url_for("ver_factura", ruta=ad["factura_ruta"])
            if ad.get("factura_ruta")
            else ""
        )

        adicionales.setdefault(ad["activo_id"], []).append(ad)

    financiero, resumen = datos_financieros()

    return render_template(
        "activos.html",
        activos=activos,
        personas=personas_activas,
        adicionales=adicionales,
        financiero=financiero,
        resumen=resumen,
    )


# ==================================================
# AUXILIARES
# ==================================================

def _campos_activo():
    """Lee y limpia el formulario. Lanza ValueError si hay datos invalidos."""
    f = request.form

    def texto(nombre):
        return f.get(nombre, "").strip() or None

    datos = {
        "codigo": f.get("codigo", "").strip(),
        "tipo": f.get("tipo", "").strip(),
        "marca": texto("marca"),
        "modelo": texto("modelo"),
        "serial": texto("serial"),
        "estado": texto("estado"),
        "cantidad": f.get("cantidad", type=int),
        "procesador": texto("procesador"),
        "ram": texto("ram"),
        "disco": texto("disco"),
        "hostname": texto("hostname"),
        "ip": texto("ip"),
        "mac": texto("mac"),
        "sistema_operativo": texto("sistema_operativo"),
        "observaciones": texto("observaciones"),
    }

    datos.update(campos_finanzas(f))

    return datos


def _codigo_en_uso(codigo, excluir_id=None):
    consulta = (
        supabase
        .table("activos")
        .select("id")
        .eq("codigo", codigo)
    )

    if excluir_id is not None:
        consulta = consulta.neq("id", excluir_id)

    return bool(consulta.limit(1).execute().data)


def _persona_para_asignar():
    """Devuelve la persona elegida en el formulario (o None)."""
    persona_id = request.form.get("persona_id", type=int)

    if not persona_id:
        return None

    persona = obtener_registro("personas", persona_id)

    if persona is None:
        raise ValueError("La persona seleccionada no existe.")

    if str(persona.get("estado") or "Activo").strip().lower() != "activo":
        raise ValueError("La persona seleccionada está inactiva.")

    return persona


def _asignar_a_persona(activo_id, codigo, persona, anterior=None):
    """Asigna el activo y registra la modificación mediante auditoría."""

    actualizar_con_auditoria(
        activo_id,
        {
            "disponibilidad": "Asignado",
            "asignado_a": persona.get("nombre"),
            "area": persona.get("area"),
        },
    )

    registrar_movimiento(
        activo_id,
        persona["id"],
        "Asignacion",
        None,
    )

    detalle = f"Activo {codigo} asignado a {persona.get('nombre')}."

    if anterior:
        detalle += f" Antes: {anterior}."

    registrar_historial(
        activo_id,
        "Asignacion",
        detalle,
    )


def _guardar_archivos(activo_id, tipo):
    """Sube la factura y los adicionales. Devuelve una lista de avisos."""
    avisos = []

    # ==================================================
    # FACTURA PRINCIPAL
    # ==================================================

    try:
        doc = subir_documento(
            request.files.get("factura"),
            activo_id,
            "factura",
        )

        if doc:
            supabase.table("activos").update(
                {
                    "factura_ruta": doc["ruta"]
                }
            ).eq("id", activo_id).execute()

    except Exception as error:
        print(f"Error subiendo factura: {error}")
        avisos.append(
            f"No se pudo guardar la factura: {error}"
        )

    # ==================================================
    # ADICIONALES
    # ==================================================

    if _es_laptop(tipo):
        tipos = request.form.getlist("adicional_tipo")
        descs = request.form.getlist("adicional_descripcion")
        provs = request.form.getlist("adicional_proveedor")
        archivos = request.files.getlist("adicional_factura")

        for i, desc in enumerate(descs):
            desc = desc.strip()

            if not desc:
                continue

            try:
                ruta = subir_factura(
                    archivos[i] if i < len(archivos) else None,
                    f"activos/{activo_id}/adicionales",
                )

                supabase.table("activos_adicionales").insert(
                    {
                        "activo_id": activo_id,
                        "tipo": tipos[i] if i < len(tipos) else None,
                        "descripcion": desc,
                        "proveedor": (
                            provs[i].strip() or None
                            if i < len(provs)
                            else None
                        ),
                        "factura_ruta": ruta,
                    }
                ).execute()

            except Exception as error:
                print(f"Error guardando adicional: {error}")

                avisos.append(
                    f"No se pudo guardar el adicional "
                    f"'{desc}': {error}"
                )

    return avisos


# ==================================================
# CREAR / EDITAR
# ==================================================

@login_required
@permiso_requerido("inventario", "crear")
def crear_activo_web():
    try:
        datos = _campos_activo()

    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("inventario"))

    if not datos["codigo"] or not datos["tipo"]:
        flash(
            "El codigo y el tipo son obligatorios.",
            "danger",
        )
        return redirect(url_for("inventario"))

    if obtener_config().get("exigir_serial") and not datos["serial"]:
        flash(
            "El serial es obligatorio.",
            "danger",
        )
        return redirect(url_for("inventario"))

    try:
        if _codigo_en_uso(datos["codigo"]):
            raise ValueError(
                f"Ya existe un activo con el codigo {datos['codigo']}."
            )

        persona = _persona_para_asignar()

        disp = request.form.get(
            "disponibilidad",
            "Disponible",
        )

        if disp not in DISPONIBILIDADES:
            disp = "Disponible"

        # Si se eligio persona, queda Asignado al terminar de crear
        datos["disponibilidad"] = (
            "Disponible"
            if persona
            else disp
        )

        nuevo = (
            supabase
            .table("activos")
            .insert(datos)
            .execute()
        )

        activo = nuevo.data[0]

        registrar_historial(
            activo["id"],
            "Creacion",
            f"Activo {datos['codigo']} registrado en el inventario.",
        )

        if persona:
            _asignar_a_persona(
                activo["id"],
                datos["codigo"],
                persona,
            )

        for aviso in _guardar_archivos(
            activo["id"],
            datos["tipo"],
        ):
            flash(aviso, "warning")

        flash(
            f"Activo {datos['codigo']} creado correctamente.",
            "success",
        )

    except Exception as error:
        print(f"Error creando activo: {error}")

        flash(
            f"No fue posible crear el activo: {error}",
            "danger",
        )

    return redirect(url_for("inventario"))


@login_required
@permiso_requerido("inventario", "editar")
def editar_activo_web(activo_id):
    try:
        datos = _campos_activo()

    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("inventario"))

    if not datos["codigo"] or not datos["tipo"]:
        flash(
            "El codigo y el tipo son obligatorios.",
            "danger",
        )
        return redirect(url_for("inventario"))

    if obtener_config().get("exigir_serial") and not datos["serial"]:
        flash(
            "El serial es obligatorio.",
            "danger",
        )
        return redirect(url_for("inventario"))

    try:
        anterior = obtener_registro(
            "activos",
            activo_id,
        )

        if anterior is None:
            raise ValueError(
                "El activo no existe."
            )

        if _codigo_en_uso(
            datos["codigo"],
            excluir_id=activo_id,
        ):
            raise ValueError(
                f"Otro activo ya usa el codigo {datos['codigo']}."
            )

        persona = _persona_para_asignar()

        esta_asignado = (
            estado_operativo(anterior)
            == "asignado"
        )

        # La disponibilidad solo se edita a mano
        # si el activo no esta asignado
        if not esta_asignado and not persona:
            disp = request.form.get(
                "disponibilidad",
                "",
            )

            if disp in DISPONIBILIDADES:
                datos["disponibilidad"] = disp

        # ==================================================
        # ACTUALIZACION CON AUDITORIA
        # ==================================================

        actualizar_con_auditoria(
            activo_id,
            datos,
            anterior,
        )

        detalle = (
            f"Datos del activo {datos['codigo']} actualizados."
        )

        if anterior.get("codigo") != datos["codigo"]:
            detalle = (
                f"Datos actualizados. Codigo: "
                f"{anterior.get('codigo')} -> "
                f"{datos['codigo']}."
            )

        registrar_historial(
            activo_id,
            "Edicion",
            detalle,
        )

        # Asignar o reasignar solo si cambio la persona.
        # Para quitar la asignacion se usa Devoluciones.
        if (
            persona
            and persona.get("nombre")
            != anterior.get("asignado_a")
        ):
            _asignar_a_persona(
                activo_id,
                datos["codigo"],
                persona,
                anterior=(
                    anterior.get("asignado_a")
                    if esta_asignado
                    else None
                ),
            )

        for aviso in _guardar_archivos(
            activo_id,
            datos["tipo"],
        ):
            flash(aviso, "warning")

        flash(
            f"Activo {datos['codigo']} actualizado.",
            "success",
        )

    except Exception as error:
        print(f"Error editando activo: {error}")

        flash(
            f"No fue posible actualizar el activo: {error}",
            "danger",
        )

    return redirect(url_for("inventario"))


# ==================================================
# INHABILITAR / BAJA / REACTIVAR
# ==================================================

def _cambiar_estado_activo(
    activo_id,
    estado_nuevo,
    accion,
    motivo_obligatorio=False,
):
    """Inhabilita o da de baja un activo que no este asignado."""

    motivo = request.form.get(
        "motivo",
        "",
    ).strip()

    if motivo_obligatorio and not motivo:
        flash(
            "El motivo es obligatorio.",
            "danger",
        )
        return redirect(url_for("inventario"))

    try:
        activo = obtener_registro(
            "activos",
            activo_id,
        )

        if activo is None:
            raise ValueError(
                "El activo no existe."
            )

        actual = estado_operativo(activo)

        if actual == "asignado":
            raise ValueError(
                "El activo esta asignado. "
                "Registra primero la devolucion."
            )

        if (
            "baja" in actual
            or "inhabilit" in actual
        ):
            raise ValueError(
                "El activo ya esta fuera del inventario operativo."
            )

        # ==================================================
        # ACTUALIZACION CON AUDITORIA
        # ==================================================

        actualizar_con_auditoria(
            activo_id,
            {
                "disponibilidad": estado_nuevo
            },
            activo,
        )

        codigo = activo.get("codigo")

        detalle = (
            f"Activo {codigo}: {accion.lower()}."
        )

        if motivo:
            detalle += (
                f" Motivo: {motivo}"
            )

        registrar_historial(
            activo_id,
            accion,
            detalle,
        )

        flash(
            f"{codigo}: {accion.lower()} registrada.",
            "success",
        )

    except Exception as error:
        print(
            f"Error en {accion.lower()} de activo: {error}"
        )

        flash(
            str(error),
            "danger",
        )

    return redirect(url_for("inventario"))


@login_required
@permiso_requerido("inventario", "editar")
def inhabilitar_activo_web(activo_id):
    return _cambiar_estado_activo(
        activo_id,
        "Inhabilitado",
        "Inhabilitacion",
    )


@login_required
@permiso_requerido("inventario", "eliminar")
def dar_de_baja_activo_web(activo_id):
    return _cambiar_estado_activo(
        activo_id,
        "Dado de baja",
        "Baja",
        motivo_obligatorio=True,
    )


@login_required
@permiso_requerido("inventario", "eliminar")
def reactivar_activo_web(activo_id):
    try:
        activo = obtener_registro(
            "activos",
            activo_id,
        )

        if activo is None:
            raise ValueError(
                "El activo no existe."
            )

        actual = estado_operativo(activo)

        if (
            "baja" not in actual
            and "inhabilit" not in actual
        ):
            raise ValueError(
                "Solo se pueden reactivar activos "
                "inhabilitados o dados de baja."
            )

        # ==================================================
        # ACTUALIZACION CON AUDITORIA
        # ==================================================

        actualizar_con_auditoria(
            activo_id,
            {
                "disponibilidad": "Disponible"
            },
            activo,
        )

        codigo = activo.get("codigo")

        registrar_historial(
            activo_id,
            "Reactivacion",
            f"Activo {codigo} reactivado. "
            f"Estado anterior: "
            f"{activo.get('disponibilidad') or activo.get('estado')}.",
        )

        flash(
            f"{codigo} reactivado y disponible.",
            "success",
        )

    except Exception as error:
        print(
            f"Error reactivando activo: {error}"
        )

        flash(
            str(error),
            "danger",
        )

    return redirect(url_for("inventario"))


# ==================================================
# REGISTRO
# Mantiene los mismos nombres de endpoint
# ==================================================

def registrar_rutas(app):
    reglas = [
        (
            "/inventario/factura/<path:ruta>",
            "ver_factura",
            ver_factura,
            ["GET"],
        ),
        (
            "/inventario",
            "inventario",
            inventario,
            ["GET"],
        ),
        (
            "/inventario/crear",
            "crear_activo_web",
            crear_activo_web,
            ["POST"],
        ),
        (
            "/inventario/<int:activo_id>/editar",
            "editar_activo_web",
            editar_activo_web,
            ["POST"],
        ),
        (
            "/inventario/<int:activo_id>/inhabilitar",
            "inhabilitar_activo_web",
            inhabilitar_activo_web,
            ["POST"],
        ),
        (
            "/inventario/<int:activo_id>/baja",
            "dar_de_baja_activo_web",
            dar_de_baja_activo_web,
            ["POST"],
        ),
        (
            "/inventario/<int:activo_id>/reactivar",
            "reactivar_activo_web",
            reactivar_activo_web,
            ["POST"],
        ),
    ]

    for ruta, endpoint, vista, metodos in reglas:
        app.add_url_rule(
            ruta,
            endpoint=endpoint,
            view_func=vista,
            methods=metodos,
        )