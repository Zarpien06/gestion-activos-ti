"""Fase 4: contratos y mantenimientos.

Registro en app.py (junto al de software):

    from routes.contratos_routes import create_contratos_bp
    app.register_blueprint(create_contratos_bp(supabase))

Permisos: reutiliza el modulo "inventario" (ver / crear / editar / eliminar).
"""
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from auth.decorators import permiso_requerido
from services.config_service import obtener_config
from services.db_service import estado_operativo, hoy_colombia

TIPOS_CONTRATO = {
    "mantenimiento": "Mantenimiento",
    "leasing": "Leasing",
    "soporte": "Soporte",
    "garantia_extendida": "Garantía extendida",
    "otro": "Otro",
}
RENOVACIONES = {
    "manual": "Renovación manual",
    "automatica": "Renovación automática",
    "no_renueva": "No se renueva",
}
TIPOS_MANT = {"preventivo": "Preventivo", "correctivo": "Correctivo"}


def create_contratos_bp(supabase):
    bp = Blueprint("contratos", __name__)

    # ---------- utilidades ----------
    def _perm(accion):
        def deco(f):
            return login_required(permiso_requerido("inventario", accion)(f))
        return deco

    def _usuario():
        return int(current_user.id), current_user.nombre

    def _log(activo_id, campo, anterior, nuevo):
        """Escribe en historial_cambios (fase 2). No rompe si falla."""
        try:
            uid, nombre = _usuario()
            supabase.table("historial_cambios").insert({
                "activo_id": activo_id,
                "usuario_id": uid,
                "usuario_nombre": nombre,
                "campo": campo,
                "valor_anterior": anterior,
                "valor_nuevo": nuevo,
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

    def _dinero(v):
        if v in (None, ""):
            return None
        try:
            n = float(v)
        except (TypeError, ValueError):
            raise ValueError("El costo debe ser un número.")
        if n < 0:
            raise ValueError("El costo no puede ser negativo.")
        return n

    def _estado(c, hoy, umbral):
        """(estado, dias_para_vencer) de un contrato."""
        if not c.get("activo"):
            return "inactivo", None
        if not c.get("fecha_fin"):
            return "sin_fecha", None
        dias = (date.fromisoformat(c["fecha_fin"][:10]) - hoy).days
        if dias < 0:
            return "vencido", dias
        if dias <= umbral:
            return "por_vencer", dias
        return "vigente", dias

    def _umbral():
        try:
            return int(obtener_config(usar_cache=True).get("dias_alerta_garantia") or 30)
        except (TypeError, ValueError):
            return 30

    def _leer_contrato(d):
        tipo = d.get("tipo") or "mantenimiento"
        if tipo not in TIPOS_CONTRATO:
            raise ValueError("El tipo de contrato no es válido.")
        proveedor = _texto(d.get("proveedor"))
        if not proveedor:
            raise ValueError("El proveedor es obligatorio.")
        renovacion = d.get("renovacion") or "manual"
        if renovacion not in RENOVACIONES:
            raise ValueError("La condición de renovación no es válida.")
        inicio = _fecha(d.get("fecha_inicio"), "inicio")
        fin = _fecha(d.get("fecha_fin"), "fin")
        if inicio and fin and fin < inicio:
            raise ValueError("La fecha de fin no puede ser anterior a la de inicio.")
        return {
            "tipo": tipo,
            "proveedor": proveedor,
            "numero": _texto(d.get("numero")),
            "fecha_inicio": inicio,
            "fecha_fin": fin,
            "costo": _dinero(d.get("costo")),
            "moneda": _texto(d.get("moneda")) or "COP",
            "renovacion": renovacion,
            "notas": _texto(d.get("notas")),
        }

    def _contratos(solo_activos=False):
        q = supabase.table("contratos").select("*")
        if solo_activos:
            q = q.eq("activo", True)
        filas = q.execute().data or []
        vinc = (
            supabase.table("activo_contrato").select("contrato_id")
            .is_("eliminado_at", "null").execute().data or []
        )
        conteo = Counter(v["contrato_id"] for v in vinc)
        hoy, umbral = hoy_colombia(), _umbral()
        for c in filas:
            c["equipos"] = conteo.get(c["id"], 0)
            c["estado"], c["dias"] = _estado(c, hoy, umbral)
        orden = {"vencido": 0, "por_vencer": 1, "vigente": 2, "sin_fecha": 3, "inactivo": 4}
        filas.sort(key=lambda c: (
            orden[c["estado"]], c["dias"] if c["dias"] is not None else 10**6
        ))
        return filas, umbral

    def _nombre_contrato(c):
        partes = [TIPOS_CONTRATO.get(c.get("tipo"), c.get("tipo")), c.get("proveedor"), c.get("numero")]
        return " ".join(str(p) for p in partes if p)

    # ---------- pagina ----------
    @bp.get("/contratos")
    @_perm("ver")
    def contratos_page():
        return render_template(
            "contratos.html", tipos=TIPOS_CONTRATO, renovaciones=RENOVACIONES
        )

    # ---------- CRUD de contratos ----------
    @bp.get("/api/contratos")
    @_perm("ver")
    def listar_contratos():
        filas, umbral = _contratos()
        resumen = Counter(c["estado"] for c in filas)
        return jsonify(contratos=filas, umbral=umbral, resumen=dict(resumen))

    @bp.post("/api/contratos")
    @_perm("crear")
    def crear_contrato():
        try:
            fila = _leer_contrato(request.get_json(force=True) or {})
            r = supabase.table("contratos").insert(fila).execute()
        except ValueError as e:
            return jsonify(error=str(e)), 400
        except Exception as e:
            return jsonify(error=str(e)), 400
        return jsonify(r.data[0]), 201

    @bp.put("/api/contratos/<int:cid>")
    @_perm("editar")
    def editar_contrato(cid):
        try:
            fila = _leer_contrato(request.get_json(force=True) or {})
            r = supabase.table("contratos").update(fila).eq("id", cid).execute()
        except ValueError as e:
            return jsonify(error=str(e)), 400
        except Exception as e:
            return jsonify(error=str(e)), 400
        if not r.data:
            return jsonify(error="El contrato no existe."), 404
        return jsonify(r.data[0])

    @bp.delete("/api/contratos/<int:cid>")
    @_perm("eliminar")
    def desactivar_contrato(cid):
        # Borrado logico: conserva el historial de equipos cubiertos
        supabase.table("contratos").update({"activo": False}).eq("id", cid).execute()
        return jsonify(ok=True)

    @bp.post("/api/contratos/<int:cid>/reactivar")
    @_perm("eliminar")
    def reactivar_contrato(cid):
        supabase.table("contratos").update({"activo": True}).eq("id", cid).execute()
        return jsonify(ok=True)

    @bp.get("/api/contratos/disponibles")
    @_perm("ver")
    def contratos_disponibles():
        """Contratos activos y no vencidos, para vincularlos a un equipo."""
        filas, _ = _contratos(solo_activos=True)
        return jsonify([
            {"id": c["id"], "nombre": _nombre_contrato(c), "fecha_fin": c.get("fecha_fin")}
            for c in filas if c["estado"] != "vencido"
        ])

    # ---------- renovacion ----------
    @bp.post("/api/contratos/<int:cid>/renovar")
    @_perm("crear")
    def renovar_contrato(cid):
        """Crea el contrato siguiente, copia sus equipos y desactiva el anterior."""
        d = request.get_json(force=True) or {}
        viejo = supabase.table("contratos").select("*").eq("id", cid).execute().data
        if not viejo:
            return jsonify(error="El contrato no existe."), 404
        viejo = viejo[0]

        try:
            inicio = _fecha(d.get("fecha_inicio"), "inicio")
            fin = _fecha(d.get("fecha_fin"), "fin")

            # Si no llegan fechas: empieza el dia despues del fin anterior
            # y dura lo mismo que el contrato anterior.
            if not inicio and viejo.get("fecha_fin"):
                inicio = (date.fromisoformat(viejo["fecha_fin"][:10]) + timedelta(days=1)).isoformat()
            if not fin and inicio:
                if viejo.get("fecha_inicio") and viejo.get("fecha_fin"):
                    dur = (date.fromisoformat(viejo["fecha_fin"][:10])
                           - date.fromisoformat(viejo["fecha_inicio"][:10]))
                else:
                    dur = timedelta(days=364)
                fin = (date.fromisoformat(inicio) + dur).isoformat()
            if inicio and fin and fin < inicio:
                raise ValueError("La fecha de fin no puede ser anterior a la de inicio.")

            costo = _dinero(d["costo"]) if d.get("costo") not in (None, "") else viejo.get("costo")

            nuevo = supabase.table("contratos").insert({
                "tipo": viejo["tipo"],
                "proveedor": viejo["proveedor"],
                "numero": viejo.get("numero"),
                "fecha_inicio": inicio,
                "fecha_fin": fin,
                "costo": costo,
                "moneda": viejo.get("moneda") or "COP",
                "renovacion": viejo.get("renovacion") or "manual",
                "notas": viejo.get("notas"),
            }).execute().data[0]
        except ValueError as e:
            return jsonify(error=str(e)), 400
        except Exception as e:
            return jsonify(error=str(e)), 400

        # Copiar los equipos que cubria el contrato anterior
        copiados = 0
        try:
            _, nombre = _usuario()
            vinc = (
                supabase.table("activo_contrato").select("activo_id")
                .eq("contrato_id", cid).is_("eliminado_at", "null").execute().data or []
            )
            if vinc:
                supabase.table("activo_contrato").insert([
                    {"activo_id": v["activo_id"], "contrato_id": nuevo["id"], "vinculado_por": nombre}
                    for v in vinc
                ]).execute()
                copiados = len(vinc)
                for v in vinc:
                    _log(v["activo_id"], "contrato", _nombre_contrato(viejo),
                         f"Renovado: {_nombre_contrato(nuevo)}")
        except Exception as e:
            print("renovar contrato (copiar equipos):", e)
            return jsonify(error=(
                "El contrato nuevo se creó, pero no se pudieron copiar los equipos: "
                f"{e}. Agrégalos desde «Equipos»."
            )), 500

        supabase.table("contratos").update({"activo": False}).eq("id", cid).execute()
        return jsonify(contrato=nuevo, equipos=copiados), 201

    # ---------- vinculos activo <-> contrato ----------
    @bp.get("/api/contratos/<int:cid>/activos")
    @_perm("ver")
    def activos_de_contrato(cid):
        filas = (
            supabase.table("activo_contrato")
            .select("id, created_at, activos(id, codigo, tipo, marca, modelo)")
            .eq("contrato_id", cid).is_("eliminado_at", "null")
            .order("id", desc=True).execute().data or []
        )
        return jsonify(filas)

    @bp.get("/api/contratos/buscar_activos")
    @_perm("ver")
    def buscar_activos():
        q = (request.args.get("q") or "").strip()
        if len(q) < 2:
            return jsonify([])
        filas = (
            supabase.table("activos")
            .select("id, codigo, tipo, marca, modelo")
            .ilike("codigo", f"%{q}%").limit(10).execute().data or []
        )
        return jsonify(filas)

    @bp.get("/api/contratos/tipos_activos")
    @_perm("ver")
    def tipos_de_activos():
        """Tipos de equipo existentes (para la carga masiva)."""
        filas = supabase.table("activos").select("tipo").execute().data or []
        tipos = sorted({str(f.get("tipo")).strip() for f in filas if f.get("tipo")})
        return jsonify(tipos)

    def _vincular(activo_id, contrato_id):
        c = supabase.table("contratos").select("*").eq("id", contrato_id).execute().data
        if not c:
            return jsonify(error="El contrato no existe."), 404
        c = c[0]
        if not c.get("activo"):
            return jsonify(error="El contrato está desactivado."), 409
        a = supabase.table("activos").select("id").eq("id", activo_id).execute().data
        if not a:
            return jsonify(error="El equipo no existe."), 404
        _, nombre = _usuario()

        # Si el equipo ya estuvo en el contrato y se quito, se reactiva el vinculo
        previos = (
            supabase.table("activo_contrato").select("id, eliminado_at")
            .eq("activo_id", activo_id).eq("contrato_id", contrato_id)
            .execute().data or []
        )
        if any(p.get("eliminado_at") is None for p in previos):
            return jsonify(error="Ese equipo ya está en el contrato"), 409

        try:
            if previos:
                r = supabase.table("activo_contrato").update({
                    "eliminado_at": None,
                    "vinculado_por": nombre,
                }).eq("id", previos[0]["id"]).execute()
            else:
                r = supabase.table("activo_contrato").insert({
                    "activo_id": activo_id,
                    "contrato_id": contrato_id,
                    "vinculado_por": nombre,
                }).execute()
        except Exception as e:
            msg = ("Ese equipo ya está en el contrato"
                   if "duplicate" in str(e).lower() else str(e))
            return jsonify(error=msg), 409
        _log(activo_id, "contrato", None, f"Vinculado: {_nombre_contrato(c)}")
        return jsonify(r.data[0]), 201

    @bp.post("/api/contratos/<int:cid>/activos")
    @_perm("editar")
    def vincular_desde_contrato(cid):
        d = request.get_json(force=True) or {}
        if not d.get("activo_id"):
            return jsonify(error="Selecciona un equipo."), 400
        return _vincular(int(d["activo_id"]), cid)

    @bp.post("/api/contratos/<int:cid>/activos/masivo")
    @_perm("editar")
    def vincular_masivo(cid):
        """Agrega al contrato todos los equipos de un tipo (sin dados de baja)."""
        d = request.get_json(force=True) or {}
        tipo = _texto(d.get("tipo"))
        if not tipo:
            return jsonify(error="Selecciona un tipo de equipo."), 400

        c = supabase.table("contratos").select("*").eq("id", cid).execute().data
        if not c:
            return jsonify(error="El contrato no existe."), 404
        c = c[0]
        if not c.get("activo"):
            return jsonify(error="El contrato está desactivado."), 409

        try:
            activos = supabase.table("activos").select("*").eq("tipo", tipo).execute().data or []
            activos = [a for a in activos if "baja" not in estado_operativo(a)]

            filas = (
                supabase.table("activo_contrato").select("id, activo_id, eliminado_at")
                .eq("contrato_id", cid).execute().data or []
            )
            vivos = {f["activo_id"] for f in filas if f.get("eliminado_at") is None}
            muertos = {f["activo_id"]: f["id"] for f in filas if f.get("eliminado_at") is not None}

            nuevos = [a for a in activos if a["id"] not in vivos]
            if not nuevos:
                return jsonify(agregados=0, omitidos=len(activos))

            _, nombre = _usuario()
            a_insertar = []
            for a in nuevos:
                if a["id"] in muertos:
                    supabase.table("activo_contrato").update({
                        "eliminado_at": None, "vinculado_por": nombre,
                    }).eq("id", muertos[a["id"]]).execute()
                else:
                    a_insertar.append({
                        "activo_id": a["id"], "contrato_id": cid, "vinculado_por": nombre,
                    })
            if a_insertar:
                supabase.table("activo_contrato").insert(a_insertar).execute()
        except Exception as e:
            return jsonify(error=str(e)), 400

        for a in nuevos:
            _log(a["id"], "contrato", None, f"Vinculado: {_nombre_contrato(c)}")
        return jsonify(agregados=len(nuevos), omitidos=len(activos) - len(nuevos))

    @bp.post("/api/activos/<int:aid>/contratos")
    @_perm("editar")
    def vincular_desde_activo(aid):
        d = request.get_json(force=True) or {}
        if not d.get("contrato_id"):
            return jsonify(error="Selecciona un contrato."), 400
        return _vincular(aid, int(d["contrato_id"]))

    @bp.get("/api/activos/<int:aid>/contratos")
    @_perm("ver")
    def contratos_de_activo(aid):
        filas = (
            supabase.table("activo_contrato")
            .select("id, created_at, contratos(id, tipo, proveedor, numero, fecha_fin, activo)")
            .eq("activo_id", aid).is_("eliminado_at", "null")
            .order("id", desc=True).execute().data or []
        )
        hoy, umbral = hoy_colombia(), _umbral()
        for f in filas:
            f["contratos"]["estado"], f["contratos"]["dias"] = _estado(
                f["contratos"], hoy, umbral
            )
        return jsonify(filas)

    @bp.delete("/api/vinculos/<int:rid>")
    @_perm("editar")
    def quitar_vinculo(rid):
        fila = (
            supabase.table("activo_contrato")
            .select("activo_id, contratos(tipo, proveedor, numero)")
            .eq("id", rid).is_("eliminado_at", "null").execute().data
        )
        if not fila:
            return jsonify(error="No encontrado."), 404
        supabase.table("activo_contrato").update({
            "eliminado_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", rid).execute()
        _log(fila[0]["activo_id"], "contrato",
             _nombre_contrato(fila[0]["contratos"]), "Desvinculado")
        return jsonify(ok=True)

    # ---------- mantenimientos ----------
    @bp.get("/api/activos/<int:aid>/mantenimientos")
    @_perm("ver")
    def mantenimientos_de_activo(aid):
        filas = (
            supabase.table("mantenimientos")
            .select("*, tickets(id, titulo)")
            .eq("activo_id", aid).is_("eliminado_at", "null")
            .order("fecha", desc=True).order("id", desc=True).execute().data or []
        )
        return jsonify(filas)

    @bp.get("/api/activos/<int:aid>/tickets")
    @_perm("ver")
    def tickets_de_activo(aid):
        filas = (
            supabase.table("tickets").select("id, titulo, estado")
            .eq("activo_id", aid).order("id", desc=True).limit(50).execute().data or []
        )
        return jsonify(filas)

    @bp.post("/api/activos/<int:aid>/mantenimientos")
    @_perm("crear")
    def crear_mantenimiento(aid):
        d = request.get_json(force=True) or {}
        try:
            tipo = d.get("tipo") or "preventivo"
            if tipo not in TIPOS_MANT:
                raise ValueError("El tipo de mantenimiento no es válido.")
            descripcion = _texto(d.get("descripcion"))
            if not descripcion:
                raise ValueError("La descripción es obligatoria.")
            hoy = hoy_colombia()
            fecha = _fecha(d.get("fecha"), "mantenimiento") or hoy.isoformat()
            if fecha > hoy.isoformat():
                raise ValueError("La fecha del mantenimiento no puede ser futura. "
                                 "Para programar uno usa «Próximo mantenimiento».")
            proximo = _fecha(d.get("proximo_mantenimiento"), "próximo mantenimiento")
            if proximo and proximo < fecha:
                raise ValueError("El próximo mantenimiento no puede ser anterior a este.")
            costo = _dinero(d.get("costo"))

            ticket_id = d.get("ticket_id") or None
            if ticket_id:
                t = (supabase.table("tickets").select("id, activo_id")
                     .eq("id", int(ticket_id)).execute().data)
                if not t or t[0].get("activo_id") != aid:
                    raise ValueError("El ticket no pertenece a este equipo.")
                ticket_id = int(ticket_id)

            _, nombre = _usuario()
            r = supabase.table("mantenimientos").insert({
                "activo_id": aid,
                "ticket_id": ticket_id,
                "tipo": tipo,
                "fecha": fecha,
                "tecnico": _texto(d.get("tecnico")),
                "costo": costo,
                "moneda": _texto(d.get("moneda")) or "COP",
                "descripcion": descripcion,
                "proximo_mantenimiento": proximo,
                "registrado_por": nombre,
            }).execute()
        except ValueError as e:
            return jsonify(error=str(e)), 400
        except Exception as e:
            return jsonify(error=str(e)), 400

        resumen = descripcion if len(descripcion) <= 80 else descripcion[:77] + "..."
        _log(aid, "mantenimiento", None, f"{TIPOS_MANT[tipo]}: {resumen}")
        return jsonify(r.data[0]), 201

    @bp.delete("/api/mantenimientos/<int:mid>")
    @_perm("eliminar")
    def borrar_mantenimiento(mid):
        fila = (
            supabase.table("mantenimientos").select("activo_id, tipo, descripcion")
            .eq("id", mid).is_("eliminado_at", "null").execute().data
        )
        if not fila:
            return jsonify(error="No encontrado."), 404
        supabase.table("mantenimientos").update({
            "eliminado_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", mid).execute()
        f = fila[0]
        _log(f["activo_id"], "mantenimiento",
             f"{TIPOS_MANT.get(f['tipo'], f['tipo'])}: {f['descripcion'][:80]}", "Eliminado")
        return jsonify(ok=True)

    return bp