/* Sección "Software instalado" para el modal de detalle del activo.
   Uso: en el HTML del modal pon <div id="sw-activo"></div>
   y al abrir el detalle llama:  swActivoCargar(activoId)
   Incluir en base.html:  <script src="{{ url_for('static', filename='software_activo.js') }}"></script> */
(function () {
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function api(url, method = 'GET', body) {
    const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || 'Error');
    return j;
  }

  window.swActivoCargar = async function (activoId) {
    const box = document.getElementById('sw-activo');
    if (!box) return;
    box.dataset.activo = activoId;
    box.innerHTML = '<p>Cargando software...</p>';
    try {
      const [inst, catalogo] = await Promise.all([api(`/api/activos/${activoId}/software`), api('/api/software')]);
      box.innerHTML = `
        <h4 style="margin:12px 0 6px">Software instalado</h4>
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px">
          <select id="sw-sel" onchange="swActivoLicencias(this.value)" style="padding:6px">
            <option value="">Selecciona software...</option>
            ${catalogo.map(s => `<option value="${s.id}">${esc(s.nombre)} ${esc(s.version)}</option>`).join('')}
          </select>
          <select id="sw-lic" style="padding:6px"><option value="">Sin licencia</option></select>
          <button class="btn btn-primary btn-sm" onclick="swActivoInstalar()">Agregar</button>
        </div>
        <table style="width:100%;border-collapse:collapse">
          ${inst.map(r => `<tr>
            <td>${esc(r.software.nombre)} ${esc(r.software.version)}</td>
            <td>${r.licencias ? esc(r.licencias.tipo) + (r.licencias.fecha_vence ? ' · vence ' + r.licencias.fecha_vence : '') : '<i>sin licencia</i>'}</td>
            <td>${r.fecha_instalacion}</td>
            <td><button class="btn btn-sm" onclick="swActivoQuitar(${r.id})">Quitar</button></td></tr>`).join('')
            || '<tr><td style="padding:8px;opacity:.7">Sin software registrado en este equipo</td></tr>'}
        </table>`;
    } catch (e) { box.innerHTML = `<p style="color:#e11d48">${esc(e.message)}</p>`; }
  };

  window.swActivoLicencias = async function (sid) {
    const sel = document.getElementById('sw-lic');
    sel.innerHTML = '<option value="">Sin licencia</option>';
    if (!sid) return;
    const l = await api(`/api/software/${sid}/licencias_disponibles`);
    sel.innerHTML += l.map(x => `<option value="${x.id}">${esc(x.tipo)} · ${x.disponibles} libres${x.fecha_vence ? ' · vence ' + x.fecha_vence : ''}</option>`).join('');
  };

  window.swActivoInstalar = async function () {
    const box = document.getElementById('sw-activo');
    const sid = document.getElementById('sw-sel').value;
    if (!sid) return alert('Selecciona un software');
    try {
      await api(`/api/activos/${box.dataset.activo}/software`, 'POST',
        { software_id: sid, licencia_id: document.getElementById('sw-lic').value || null });
      swActivoCargar(box.dataset.activo);
      if (window.cargarHistorial) window.cargarHistorial(box.dataset.activo); // refresca historial de Fase 2 si existe
    } catch (e) { alert(e.message); }
  };

  window.swActivoQuitar = async function (rid) {
    const box = document.getElementById('sw-activo');
    if (!confirm('¿Quitar este software del equipo?')) return;
    try {
      await api(`/api/activos/${box.dataset.activo}/software/${rid}`, 'DELETE');
      swActivoCargar(box.dataset.activo);
      if (window.cargarHistorial) window.cargarHistorial(box.dataset.activo);
    } catch (e) { alert(e.message); }
  };
})();