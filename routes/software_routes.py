"""Fase 3: software y licencias.

Registro en app.py (despues de crear `supabase` y `login_required`):

    from routes.software_routes import create_software_bp
    app.register_blueprint(create_software_bp(supabase, login_required))

Permisos: reutiliza el modulo "inventario" (ver / crear / editar / eliminar),
igual que contratos_routes.py.

REQUIERE esta columna (ejecutar una vez en Supabase > SQL Editor):

    alter table software add column if not exists activo boolean not null default true;
"""
import time
from datetime import date, datetime, timezone

import httpx
from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user

from auth.decorators import permiso_requerido
from services.db_service import hoy_colombia

TIPOS_LICENCIA = {"perpetua", "suscripcion", "oem", "volumen", "open_source"}
DIAS_ALERTA = 90


def con_reintento(consulta, intentos=3, espera=0.3):
    """Ejecuta una consulta de LECTURA y la reintenta si Supabase cerro la conexion.

    Solo para select. No usar en insert/update/delete: un reintento podria duplicar.
    """
    for i in range(intentos):
        try:
            return consulta.execute()
        except httpx.TransportError:
            if i == intentos - 1:
                raise
            time.sleep(espera * (i + 1))


def create_software_bp(supabase, login_required):
    bp = Blueprint("software", __name__)

    # ---------- utilidades ----------
    def _perm(accion):
        def deco(f):
            return login_required(permiso_requerido("inventario", accion)(f))
        return deco

    def _usuario():
        return int(current_user.id), current_user.nombre

    def _log(activo_id, campo, anterior, nuevo):
        """Escribe en historial_cambios (Fase 2). No rompe si falla."""
        try:
            uid, nombre = _usuario()
            supabase.table("historial_cambios").insert({
                "activo_id": activo_id, "usuario_id": uid, "usuario_nombre": nombre,
                "campo": campo, "valor_anterior": anterior, "valor_nuevo": nuevo,
            }).execute()
        except Exception as e:
            print("historial_cambios:", e)

    def _texto(v):
        return (v or "").strip() or None

    def _fecha(v, etiqueta):
        if v in (None, ""):
            return None
        try:
            return date.fromisoformat(str(v)[:10]).isoformat()
        except ValueError:
            raise ValueError(f"La fecha de {etiqueta} no es válida.")

    def _costo(v):
        if v in (None, ""):
            return None
        try:
            n = float(v)
        except (TypeError, ValueError):
            raise ValueError("El costo debe ser un número.")
        if n < 0:
            raise ValueError("El costo no puede ser negativo.")
        return n

    def _leer_software(d):
        nombre = _texto(d.get("nombre"))
        if not nombre:
            raise ValueError("El nombre es obligatorio")
        return {
            "nombre": nombre,
            "fabricante": _texto(d.get("fabricante")),
            # nombre + version es unico, por eso la version vacia se guarda como ""
            "version": (d.get("version") or "").strip(),
            "categoria": _texto(d.get("categoria")),
        }

    def _leer_licencia(d):
        """Valida y normaliza los campos de una licencia (crear y editar)."""
        tipo = d.get("tipo") or "perpetua"
        if tipo not in TIPOS_LICENCIA:
            raise ValueError("El tipo de licencia no es válido.")

        try:
            cant = int(d.get("cantidad") or 1)
        except (TypeError, ValueError):
            raise ValueError("Cantidad inválida")
        if cant < 1:
            raise ValueError("La cantidad debe ser >= 1")

        compra = _fecha(d.get("fecha_compra"), "compra")
        vence = _fecha(d.get("fecha_vence"), "vencimiento")
        if compra and vence and vence < compra:
            raise ValueError("El vencimiento no puede ser anterior a la compra.")

        return {
            "tipo": tipo,
            "cantidad": cant,
            "clave": _texto(d.get("clave")),
            "proveedor": _texto(d.get("proveedor")),
            "fecha_compra": compra,
            "fecha_vence": vence,
            "costo": _costo(d.get("costo")),
            "moneda": (_texto(d.get("moneda")) or "COP").upper(),
        }

    def _msg_duplicado(e):
        if "duplicate" in str(e).lower():
            return "Ya existe ese software con esa versión"
        return str(e)

    # ---------- Página del catálogo ----------
    @bp.route("/software")
    @_perm("ver")
    def software_page():
        return render_template("software.html")

    # ---------- Catálogo de software ----------
    @bp.get("/api/software")
    @_perm("ver")
    def listar_software():
        incluir_inactivos = request.args.get("incluir_inactivos") in ("1", "true", "si")

        consulta = supabase.table("software").select("*").order("nombre")
        if not incluir_inactivos:
            consulta = consulta.eq("activo", True)
        sw = con_reintento(consulta).data or []

        lic = con_reintento(
            supabase.table("licencias_uso").select("*").eq("activa", True)
        ).data or []
        por_sw = {}
        for l in lic:
            por_sw.setdefault(l["software_id"], []).append(l)

        inst = con_reintento(
            supabase.table("activo_software").select("software_id")
            .is_("eliminado_at", "null")
        ).data or []
        conteo = {}
        for r in inst:
            conteo[r["software_id"]] = conteo.get(r["software_id"], 0) + 1

        for s in sw:
            s["licencias"] = por_sw.get(s["id"], [])
            s["instalaciones"] = conteo.get(s["id"], 0)
            s["licencias_total"] = sum(l["cantidad"] for l in s["licencias"])
        return jsonify(sw)

    @bp.post("/api/software")
    @_perm("crear")
    def crear_software():
        try:
            fila = _leer_software(request.get_json(force=True) or {})
            r = supabase.table("software").insert(fila).execute()
        except ValueError as e:
            return jsonify(error=str(e)), 400
        except Exception as e:
            return jsonify(error=_msg_duplicado(e)), 400
        return jsonify(r.data[0]), 201

    @bp.put("/api/software/<int:sid>")
    @_perm("editar")
    def editar_software(sid):
        try:
            fila = _leer_software(request.get_json(force=True) or {})
            r = supabase.table("software").update(fila).eq("id", sid).execute()
        except ValueError as e:
            return jsonify(error=str(e)), 400
        except Exception as e:
            return jsonify(error=_msg_duplicado(e)), 400
        if not r.data:
            return jsonify(error="El software no existe."), 404
        return jsonify(r.data[0])

    @bp.delete("/api/software/<int:sid>")
    @_perm("eliminar")
    def desactivar_software(sid):
        # Borrado logico: nada se elimina, se conserva licencias e historial
        r = supabase.table("software").update({"activo": False}).eq("id", sid).execute()
        if not r.data:
            return jsonify(error="El software no existe."), 404
        return jsonify(ok=True)

    @bp.post("/api/software/<int:sid>/reactivar")
    @_perm("eliminar")
    def reactivar_software(sid):
        r = supabase.table("software").update({"activo": True}).eq("id", sid).execute()
        if not r.data:
            return jsonify(error="El software no existe."), 404
        return jsonify(ok=True)

    # ---------- Licencias ----------
    @bp.post("/api/licencias")
    @_perm("crear")
    def crear_licencia():
        d = request.get_json(force=True) or {}
        try:
            if not d.get("software_id"):
                raise ValueError("Falta el software")
            sid = int(d["software_id"])
            if not con_reintento(
                supabase.table("software").select("id").eq("id", sid)
            ).data:
                raise ValueError("El software no existe.")

            fila = _leer_licencia(d)
            fila["software_id"] = sid
            r = supabase.table("licencias").insert(fila).execute()
        except ValueError as e:
            return jsonify(error=str(e)), 400
        except Exception as e:
            return jsonify(error=str(e)), 400
        return jsonify(r.data[0]), 201

    @bp.put("/api/licencias/<int:lid>")
    @_perm("editar")
    def editar_licencia(lid):
        d = request.get_json(force=True) or {}
        try:
            fila = _leer_licencia(d)

            # No permitir bajar la cantidad por debajo de lo que ya esta instalado
            uso = con_reintento(
                supabase.table("licencias_uso").select("usadas").eq("id", lid)
            ).data
            usadas = (uso[0].get("usadas") or 0) if uso else 0
            if fila["cantidad"] < usadas:
                raise ValueError(
                    f"Hay {usadas} instalación(es) usando esta licencia; "
                    "la cantidad no puede ser menor."
                )

            r = supabase.table("licencias").update(fila).eq("id", lid).execute()
        except ValueError as e:
            return jsonify(error=str(e)), 400
        except Exception as e:
            return jsonify(error=str(e)), 400
        if not r.data:
            return jsonify(error="La licencia no existe."), 404
        return jsonify(r.data[0])

    @bp.delete("/api/licencias/<int:lid>")
    @_perm("eliminar")
    def desactivar_licencia(lid):
        # Borrado logico: conserva el historico de instalaciones
        r = supabase.table("licencias").update({"activa": False}).eq("id", lid).execute()
        if not r.data:
            return jsonify(error="La licencia no existe."), 404
        return jsonify(ok=True)

    @bp.get("/api/licencias/alertas")
    @_perm("ver")
    def alertas():
        rows = con_reintento(
            supabase.table("licencias_uso").select("*").eq("activa", True)
        ).data or []
        vencidas, por_vencer, excedidas = [], [], []
        for l in rows:
            d = l.get("dias_para_vencer")
            if d is not None:
                if d < 0:
                    vencidas.append(l)
                elif d <= DIAS_ALERTA:
                    por_vencer.append(l)
            if (l.get("usadas") or 0) > (l.get("cantidad") or 0):
                excedidas.append(l)
        por_vencer.sort(key=lambda x: x["dias_para_vencer"])
        return jsonify(vencidas=vencidas, por_vencer=por_vencer, excedidas=excedidas)

    # ---------- Software por activo ----------
    @bp.get("/api/activos/<int:aid>/software")
    @_perm("ver")
    def software_de_activo(aid):
        rows = con_reintento(
            supabase.table("activo_software")
            .select("id, fecha_instalacion, instalado_por, software(id, nombre, version, fabricante), licencias(id, tipo, clave, fecha_vence)")
            .eq("activo_id", aid).is_("eliminado_at", "null")
            .order("id", desc=True)
        ).data or []
        return jsonify(rows)

    @bp.get("/api/software/<int:sid>/licencias_disponibles")
    @_perm("ver")
    def licencias_disponibles(sid):
        hoy = hoy_colombia().isoformat()
        rows = con_reintento(
            supabase.table("licencias_uso").select("*").eq("software_id", sid)
            .eq("activa", True)
        ).data or []
        ok = [l for l in rows
              if l["disponibles"] > 0 and (not l["fecha_vence"] or l["fecha_vence"] >= hoy)]
        return jsonify(ok)

    @bp.post("/api/activos/<int:aid>/software")
    @_perm("editar")
    def instalar(aid):
        d = request.get_json(force=True) or {}
        if not d.get("software_id"):
            return jsonify(error="Selecciona un software"), 400
        try:
            sid = int(d["software_id"])
            lid = int(d["licencia_id"]) if d.get("licencia_id") else None
        except (TypeError, ValueError):
            return jsonify(error="Datos inválidos"), 400

        sw = con_reintento(
            supabase.table("software").select("nombre, version, activo").eq("id", sid)
        ).data
        if not sw:
            return jsonify(error="El software no existe."), 404
        sw = sw[0]
        if sw.get("activo") is False:
            return jsonify(error="El software está desactivado; reactívalo para instalarlo."), 409

        if lid:
            lic = con_reintento(
                supabase.table("licencias_uso").select("*").eq("id", lid)
            ).data
            if not lic:
                return jsonify(error="La licencia no existe."), 404
            l = lic[0]
            if l["software_id"] != sid:
                return jsonify(error="La licencia no corresponde a ese software"), 400
            if l["disponibles"] <= 0:
                return jsonify(error="No hay cupos disponibles en esa licencia"), 409
            if l["fecha_vence"] and l["fecha_vence"] < hoy_colombia().isoformat():
                return jsonify(error="La licencia está vencida"), 409

        _, nombre = _usuario()
        try:
            r = supabase.table("activo_software").insert({
                "activo_id": aid, "software_id": sid, "licencia_id": lid,
                "fecha_instalacion": d.get("fecha_instalacion") or hoy_colombia().isoformat(),
                "instalado_por": nombre,
            }).execute()
        except Exception as e:
            msg = ("Ese software ya está registrado en este equipo"
                   if "duplicate" in str(e).lower() else str(e))
            return jsonify(error=msg), 409

        _log(aid, "software", None,
             f"Instalado: {sw['nombre']} {sw.get('version') or ''}".strip())
        return jsonify(r.data[0]), 201

    @bp.delete("/api/activos/<int:aid>/software/<int:rid>")
    @_perm("editar")
    def desinstalar(aid, rid):
        row = con_reintento(
            supabase.table("activo_software").select("software(nombre, version)")
            .eq("id", rid).eq("activo_id", aid).is_("eliminado_at", "null")
        ).data
        if not row:
            return jsonify(error="No encontrado"), 404
        supabase.table("activo_software").update({
            "eliminado_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", rid).execute()
        s = row[0].get("software") or {}
        _log(aid, "software",
             f"{s.get('nombre', '')} {s.get('version') or ''}".strip(), "Desinstalado")
        return jsonify(ok=True)

    return bp