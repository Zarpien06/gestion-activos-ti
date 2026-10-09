/* Fase 4: contratos y mantenimientos dentro del modal del activo.
 *
 * Uso: poner <div id="ct-activo"></div> en el modal y llamar
 *      ctActivoCargar(activoId) cuando se abra un activo.
 * Tambien expone window.ctUtil (helpers que reutiliza contratos.html).
 */
(function () {
  'use strict';

  var TIPO_CONTRATO = {
    mantenimiento: 'Mantenimiento', leasing: 'Leasing', soporte: 'Soporte',
    garantia_extendida: 'Garantía extendida', otro: 'Otro'
  };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  async function api(url, opts) {
    opts = opts || {};
    if (opts.body && typeof opts.body !== 'string') opts.body = JSON.stringify(opts.body);
    opts.headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
    var r;
    try { r = await fetch(url, opts); }
    catch (e) { throw new Error('No hay conexión con el servidor.'); }
    var data = null;
    try { data = await r.json(); } catch (e) { /* respuesta no JSON */ }
    if (!r.ok) {
      throw new Error((data && data.error) ||
        (r.status === 403 ? 'No tienes permiso para esta acción.' : 'Error ' + r.status));
    }
    if (data === null) throw new Error('La sesión expiró. Recarga la página.');
    return data;
  }

  function fecha(f) { return f ? String(f).slice(0, 10) : '—'; }

  function dinero(n, m) {
    if (n == null) return '—';
    return new Intl.NumberFormat('es-CO', { maximumFractionDigits: 0 }).format(n) + ' ' + (m || 'COP');
  }

  function hoyLocal() { return new Date().toLocaleDateString('en-CA'); }

  function badge(estado, dias) {
    switch (estado) {
      case 'vencido':
        return '<span class="badge badge-danger">Vencido hace ' + Math.abs(dias) + ' d</span>';
      case 'por_vencer':
        return '<span class="badge badge-warning">Vence en ' + dias + ' d</span>';
      case 'vigente':
        return '<span class="badge badge-success">Vigente</span>';
      case 'sin_fecha':
        return '<span class="badge badge-info">Sin fecha de fin</span>';
      default:
        return '<span class="badge">Inactivo</span>';
    }
  }

  window.ctUtil = { esc: esc, api: api, fecha: fecha, dinero: dinero, badge: badge, hoyLocal: hoyLocal };

  // ---------- estilos (una sola vez) ----------
  if (!document.getElementById('ct-estilos')) {
    var st = document.createElement('style');
    st.id = 'ct-estilos';
    st.textContent =
      '.ct-sec{margin-top:18px}' +
      '.ct-sec h4{margin:0 0 8px;font-size:14px;font-weight:600}' +
      '.ct-fila{display:flex;align-items:center;justify-content:space-between;gap:12px;' +
      'padding:9px 12px;border:1px solid var(--border);border-radius:var(--radius);' +
      'background:var(--surface-2);margin-bottom:6px;flex-wrap:wrap}' +
      '.ct-fila small{color:var(--text-light)}' +
      '.ct-form{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;' +
      'padding:12px;border:1px solid var(--border);border-radius:var(--radius);margin-top:8px}' +
      '.ct-form .ancho{grid-column:1/-1}' +
      '.ct-acciones{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:8px}' +
      '.ct-vacio{color:var(--text-light);padding:6px 0}' +
      '.ct-btn-sm{padding:6px 10px;font-size:12.5px}';
    document.head.appendChild(st);
  }

  var actual = null;

  function msg(box, texto, tipo) {
    var el = box.querySelector('.ct-msg');
    if (!el) return;
    el.innerHTML = texto ? '<div class="alert alert-' + (tipo || 'danger') + '">' + esc(texto) + '</div>' : '';
  }

  function recargarHistorial(aid) {
    if (typeof window.cargarHistorial === 'function') {
      try { window.cargarHistorial(aid); } catch (e) { /* ignorar */ }
    }
  }

  function pintar(box, aid, vinculos, mants) {
    var h = '<div class="ct-msg"></div>';

    // ----- Contratos -----
    h += '<div class="ct-sec"><h4>Contratos</h4>';
    if (!vinculos.length) h += '<div class="ct-vacio">Este equipo no tiene contratos vinculados.</div>';
    vinculos.forEach(function (v) {
      var c = v.contratos;
      h += '<div class="ct-fila"><div><strong>' + esc(TIPO_CONTRATO[c.tipo] || c.tipo) + '</strong> · ' +
        esc(c.proveedor) + (c.numero ? ' · N.º ' + esc(c.numero) : '') +
        '<br><small>Vence: ' + fecha(c.fecha_fin) + '</small></div><div>' +
        badge(c.estado, c.dias) +
        ' <button type="button" class="btn btn-outline ct-btn-sm" data-acc="quitar" data-id="' + v.id + '">Quitar</button>' +
        '</div></div>';
    });
    h += '<div class="ct-acciones"><button type="button" class="btn btn-outline ct-btn-sm" data-acc="abrir-vinculo">Vincular contrato</button></div>';
    h += '<div class="ct-vinculo"></div></div>';

    // ----- Mantenimientos -----
    h += '<div class="ct-sec"><h4>Mantenimientos</h4>';
    if (!mants.length) h += '<div class="ct-vacio">Todavía no hay mantenimientos registrados.</div>';
    mants.forEach(function (m) {
      var det = [m.tecnico ? 'Técnico: ' + esc(m.tecnico) : '',
        m.costo != null ? dinero(m.costo, m.moneda) : '',
        m.tickets ? 'Ticket #' + m.tickets.id : '',
        m.proximo_mantenimiento ? 'Próximo: ' + fecha(m.proximo_mantenimiento) : '']
        .filter(Boolean).join(' · ');
      h += '<div class="ct-fila"><div><strong>' + fecha(m.fecha) + '</strong> · ' +
        (m.tipo === 'correctivo' ? 'Correctivo' : 'Preventivo') + '<br>' + esc(m.descripcion) +
        (det ? '<br><small>' + det + '</small>' : '') + '</div>' +
        '<button type="button" class="btn btn-outline ct-btn-sm" data-acc="borrar-mant" data-id="' + m.id + '">Eliminar</button></div>';
    });
    h += '<div class="ct-acciones"><button type="button" class="btn btn-outline ct-btn-sm" data-acc="abrir-mant">Registrar mantenimiento</button></div>';
    h += '<div class="ct-form-mant"></div></div>';

    box.innerHTML = h;
  }

  async function cargar(aid) {
    var box = document.getElementById('ct-activo');
    if (!box) return;
    actual = aid;
    box.innerHTML = '<div class="ct-vacio">Cargando contratos y mantenimientos…</div>';
    try {
      var res = await Promise.all([
        api('/api/activos/' + aid + '/contratos'),
        api('/api/activos/' + aid + '/mantenimientos')
      ]);
      if (actual !== aid) return;
      pintar(box, aid, res[0], res[1]);
    } catch (e) {
      box.innerHTML = '<div class="alert alert-danger">' + esc(e.message) + '</div>';
    }
  }

  async function abrirVinculo(box, aid) {
    var cont = box.querySelector('.ct-vinculo');
    try {
      var lista = await api('/api/contratos/disponibles');
      if (!lista.length) {
        cont.innerHTML = '<div class="ct-vacio">No hay contratos vigentes. Créalos en la página Contratos.</div>';
        return;
      }
      cont.innerHTML = '<div class="ct-form"><div class="ancho"><label>Contrato</label>' +
        '<select class="form-select" data-campo="contrato">' +
        lista.map(function (c) {
          return '<option value="' + c.id + '">' + esc(c.nombre) +
            (c.fecha_fin ? ' (vence ' + fecha(c.fecha_fin) + ')' : '') + '</option>';
        }).join('') + '</select></div>' +
        '<div class="ct-acciones ancho"><button type="button" class="btn ct-btn-sm" data-acc="guardar-vinculo">Vincular</button>' +
        '<button type="button" class="btn btn-outline ct-btn-sm" data-acc="cancelar-vinculo">Cancelar</button></div></div>';
    } catch (e) { msg(box, e.message); }
  }

  async function abrirMant(box, aid) {
    var cont = box.querySelector('.ct-form-mant');
    var tickets = [];
    try { tickets = await api('/api/activos/' + aid + '/tickets'); } catch (e) { /* sin tickets */ }
    cont.innerHTML = '<div class="ct-form">' +
      '<div><label>Tipo</label><select class="form-select" data-campo="tipo">' +
      '<option value="preventivo">Preventivo</option><option value="correctivo">Correctivo</option></select></div>' +
      '<div><label>Fecha</label><input type="date" class="form-control" data-campo="fecha" value="' + hoyLocal() + '" max="' + hoyLocal() + '"></div>' +
      '<div><label>Técnico</label><input type="text" class="form-control" data-campo="tecnico"></div>' +
      '<div><label>Costo (COP)</label><input type="number" min="0" step="any" class="form-control" data-campo="costo"></div>' +
      '<div><label>Ticket relacionado</label><select class="form-select" data-campo="ticket_id"><option value="">Ninguno</option>' +
      tickets.map(function (t) {
        return '<option value="' + t.id + '">#' + t.id + ' · ' + esc(t.titulo) + '</option>';
      }).join('') + '</select></div>' +
      '<div><label>Próximo mantenimiento</label><input type="date" class="form-control" data-campo="proximo_mantenimiento"></div>' +
      '<div class="ancho"><label>Descripción</label><textarea class="form-control" rows="2" data-campo="descripcion"></textarea></div>' +
      '<div class="ct-acciones ancho"><button type="button" class="btn ct-btn-sm" data-acc="guardar-mant">Guardar mantenimiento</button>' +
      '<button type="button" class="btn btn-outline ct-btn-sm" data-acc="cancelar-mant">Cancelar</button></div></div>';
  }

  function valor(cont, campo) {
    var el = cont.querySelector('[data-campo="' + campo + '"]');
    return el ? el.value : '';
  }

  document.addEventListener('click', async function (ev) {
    var btn = ev.target.closest('#ct-activo [data-acc]');
    if (!btn) return;
    var box = document.getElementById('ct-activo');
    var aid = actual;
    var acc = btn.dataset.acc;
    msg(box, '');
    try {
      if (acc === 'abrir-vinculo') return abrirVinculo(box, aid);
      if (acc === 'cancelar-vinculo') { box.querySelector('.ct-vinculo').innerHTML = ''; return; }
      if (acc === 'abrir-mant') return abrirMant(box, aid);
      if (acc === 'cancelar-mant') { box.querySelector('.ct-form-mant').innerHTML = ''; return; }

      if (acc === 'guardar-vinculo') {
        var cv = box.querySelector('.ct-vinculo');
        await api('/api/activos/' + aid + '/contratos', {
          method: 'POST', body: { contrato_id: valor(cv, 'contrato') }
        });
      } else if (acc === 'quitar') {
        if (!window.confirm('¿Quitar este contrato del equipo?')) return;
        await api('/api/vinculos/' + btn.dataset.id, { method: 'DELETE' });
      } else if (acc === 'guardar-mant') {
        var cm = box.querySelector('.ct-form-mant');
        await api('/api/activos/' + aid + '/mantenimientos', {
          method: 'POST',
          body: {
            tipo: valor(cm, 'tipo'), fecha: valor(cm, 'fecha'), tecnico: valor(cm, 'tecnico'),
            costo: valor(cm, 'costo'), ticket_id: valor(cm, 'ticket_id'),
            proximo_mantenimiento: valor(cm, 'proximo_mantenimiento'),
            descripcion: valor(cm, 'descripcion')
          }
        });
      } else if (acc === 'borrar-mant') {
        if (!window.confirm('¿Eliminar este mantenimiento?')) return;
        await api('/api/mantenimientos/' + btn.dataset.id, { method: 'DELETE' });
      } else {
        return;
      }
      await cargar(aid);
      recargarHistorial(aid);
    } catch (e) {
      msg(box, e.message);
    }
  });

  window.ctActivoCargar = cargar;
})();