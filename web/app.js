/* HamScanPi – Dashboard ohne Seiten-Reload.
   Alles kommt per JSON-API, Karten werden im DOM aktualisiert statt neu gebaut,
   laufende Audio-Wiedergabe und offene Details bleiben erhalten. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const api = async (path, opts) => {
    const r = await fetch(path, Object.assign({ cache: 'no-store' }, opts || {}));
    const data = await r.json().catch(() => ({}));
    if (r.status === 401 && data.login) { location.href = '/login'; throw new Error('Anmeldung nötig'); }
    if (!r.ok || data.ok === false) throw new Error(data.error || ('HTTP ' + r.status));
    return data;
  };
  const post = (path, body) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });

  // ---------- Zeit (immer UTC, lesbar) ----------
  const pad = n => String(n).padStart(2, '0');
  const EN = (window.I18N && I18N.lang === 'en');
  const WD = EN ? ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] : ['So', 'Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa'];
  function parseT(s) { if (!s) return null; const d = new Date(s); return isNaN(d) ? null : d; }
  function fmtTime(s, withDate = true) {
    const d = parseT(s); if (!d) return '–';
    const t = `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
    if (!withDate) return t + ' UTC';
    const today = new Date();
    const sameDay = d.getUTCFullYear() === today.getUTCFullYear() && d.getUTCMonth() === today.getUTCMonth() && d.getUTCDate() === today.getUTCDate();
    // Englisch: „Sat 24 Oct“ statt „24/10“ – in den USA stünde 05/10 sonst für den 10. Mai
    const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    return (sameDay ? (EN ? 'today ' : 'heute ') : (EN ? `${WD[d.getUTCDay()]} ${d.getUTCDate()} ${MON[d.getUTCMonth()]} ` : `${WD[d.getUTCDay()]} ${pad(d.getUTCDate())}.${pad(d.getUTCMonth() + 1)}. `)) + t + ' UTC';
  }
  function fmtRel(s) {
    const d = parseT(s); if (!d) return '';
    const diff = Math.round((d - Date.now()) / 1000);
    const a = Math.abs(diff);
    const txt = a < 60 ? `${a} s` : a < 3600 ? `${Math.round(a / 60)} min` : a < 86400 ? `${(a / 3600).toFixed(1)} h` : `${Math.round(a / 86400)} ${EN ? 'days' : 'Tage'}`;
    return diff < 0 ? (EN ? `${txt} ago` : `vor ${txt}`) : `in ${txt}`;
  }
  function fmtDur(sec) { if (sec == null) return '–'; sec = Math.round(sec); return sec < 90 ? `${sec} s` : `${Math.floor(sec / 60)} min ${pad(sec % 60)} s`; }
  function fmtMHz(hz) { return (hz / 1e6).toFixed(4); }
  // Kürzel der Ortszeit (MESZ, MEZ, CEST …) – überall dort, wo Ortszeit statt UTC steht
  // Das Kürzel gilt für den jeweiligen Zeitpunkt: in der Nacht der Zeitumstellung stehen MESZ und MEZ nebeneinander
  function zoneAt(d) {
    // Kürzel in der Sprache des Browsers: so heißt es in New York „EDT“, in Tokio „JST“, in Sydney „AEDT“
    // (mit fester Sprache gäbe es dort nur „GMT-4“ usw.); in Deutschland bleibt es „MESZ“/„MEZ“
    for (const lang of [navigator.language, EN ? 'en-GB' : 'de']) {      // ungültige Sprachkennung → Rückfall
      try { const z = new Intl.DateTimeFormat(lang, { timeZoneName: 'short' }).formatToParts(d).find(p => p.type === 'timeZoneName')?.value; if (z) return z; }
      catch (e) { }
    }
    return EN ? 'local' : 'Ortszeit';
  }
  let ZONE = zoneAt(new Date());
  function refreshZone() {                       // jede Minute: Seite kann über die Umstellung hinweg offen sein
    const z = zoneAt(new Date());
    if (z !== ZONE || !$('clockZone').dataset.set) {
      ZONE = z; $('clockZone').textContent = z; $('clockZone').dataset.set = '1';
      $('satTimeHead').textContent = `Zeit (${z})`;
      try { if (satData) renderSatPasses(); } catch (e) { }   // beim ersten Aufruf gibt es satData noch nicht
    }
  }
  refreshZone(); setInterval(refreshZone, 60000);
  setInterval(() => { const d = new Date(); $('clock').textContent = `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`; $('clockLocal').textContent = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; }, 1000);

  let toastTimer;
  function toast(msg, ms = 2500) { const t = $('toast'); t.textContent = msg; t.classList.add('show'); clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove('show'), ms); }
  function note(el, msg, err) { el.textContent = msg; el.classList.toggle('err', !!err); if (msg) setTimeout(() => { if (el.textContent === msg) el.textContent = ''; }, 3000); }

  // ---------- Zustand ----------
  let bandplan = [];          // [{name, segments:[...]}]
  let modes = {};
  let lastStatus = null;
  let settings = null;        // {scan_interval_minutes, selected_bands, selected_segments}
  let lastState = null;
  const filter = { band: localStorage.getItem('f.band') || 'all', mode: localStorage.getItem('f.mode') || 'all', sort: localStorage.getItem('f.sort') || 'time', q: '', protected: false, audio: localStorage.getItem('f.audio') === '1' };
  const open = new Set();     // geöffnete Trefferkarten
  const cards = new Map();    // id -> element

  // ---------- Bandplan + Einstellungen ----------
  async function loadBandplan() {
    const d = await api('/api/bandplan');
    bandplan = d.bands; modes = d.modes;
    const fm = $('filterMode');
    fm.innerHTML = '<option value="all">Alle</option>' + Object.entries(modes).filter(([k]) => k && k !== 'unknown').map(([k, v]) => `<option value="${k}">${v}</option>`).join('') + '<option value="unknown">Unbekannt</option>';
    fm.value = filter.mode;
    renderFilterBands({});
  }

  async function loadSettings() {
    settings = await api('/api/settings');
    const iv = $('interval');
    iv.innerHTML = settings.allowed_intervals.map(m => `<option value="${m}">${intervalLabel(m)}</option>`).join('');
    iv.value = settings.scan_interval_minutes;
    for (const [dev, id] of [['pmsdr', 'rasterPmsdr'], ['rtlsdr', 'rasterRtlsdr']]) {
      const sel = $(id); sel.innerHTML = Object.entries(settings.rasters || { normal: 'normal' }).map(([k, v]) => `<option value="${k}">${v}</option>`).join('');
      sel.value = (settings.scan_raster || {})[dev] || 'normal';
    }
    $('cleanupAuto').value = String(settings.cleanup_auto_days || 0);
    loadEstimate();
    $('myLocator').value = settings.my_locator || '';
    $('myCall').value = settings.my_call || '';
    $('pskOn').checked = !!settings.pskreporter;
    renderSchedules();
    renderWatch();
    const o833 = $('liveStep').querySelector('option[value="8333"]');     // 8,33-kHz-Raster gibt es nur im Flugfunk
    if (o833) o833.hidden = o833.disabled = !settings.allow_restricted;
    renderDeviceSwitch();
    renderBands();
    fillNotify();
  }

  let saveTimer;
  // Jede Änderung geht als Einzelschritt an den Server (nicht die ganze Liste): war eine zweite
  // Browserseite mit altem Stand offen, schrieb sie früher ihre alte Auswahl zurück
  const pending = [];
  function scheduleSave(what, change) {
    if (change) pending.push(change);
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      const todo = pending.splice(0);
      try {
        let d;
        for (const ch of todo) d = await post('/api/settings', ch);
        if (!todo.length) d = await post('/api/settings', { scan_interval_minutes: Number($('interval').value) });
        settings = Object.assign(settings, d.settings);
        renderBands();
        loadEstimate();
        note(what === 'scan' ? $('scanSaveNote') : $('bandSaveNote'), '✓ gespeichert');
      } catch (e) { note(what === 'scan' ? $('scanSaveNote') : $('bandSaveNote'), 'Fehler: ' + e.message, true); }
    }, 400);
  }
  $('interval').addEventListener('change', () => { scheduleSave('scan'); setTimeout(loadEstimate, 600); });
  function intervalLabel(m) { return m === 0 ? 'aus' : m < 60 ? `alle ${m} min` : m === 60 ? 'jede Stunde' : `alle ${m / 60} Stunden`; }
  // Dauer eines Scans mit der aktuellen Auswahl (aus den letzten Läufen gemessen) gegen das Intervall prüfen
  let estimate = null;
  async function loadEstimate() {
    try { estimate = await api('/api/scan-estimate'); } catch (e) { return; }
    const iv = $('interval'), sec = estimate.seconds || 0, cur = Number(iv.value);
    for (const o of iv.options) {
      const m = Number(o.value), tooShort = m > 0 && sec > m * 60;
      o.textContent = intervalLabel(m) + (tooShort ? ' – Scan dauert länger' : '');
      o.disabled = tooShort && m !== cur;
    }
    const hint = $('intervalHint');
    if (!estimate.parts || !estimate.parts.length) { hint.textContent = ''; return; }
    const min = Math.ceil(sec / 60);
    const minOk = settings.allowed_intervals.find(m => m > 0 && m * 60 >= sec);
    const det = estimate.parts.map(p => `${p.band} ${p.tunes}× ≈ ${fmtDur(p.seconds)}${p.measured ? '' : ' (geschätzt)'}`).join(', ');
    let txt = `Ein Scan mit dieser Auswahl dauert etwa ${fmtDur(sec)} (${det}, Vor-/Nachlauf ≈ ${fmtDur(estimate.overhead)}).`;
    if (cur > 0 && sec > cur * 60) txt += ` ⚠ Länger als das Intervall – die Scans laufen dann mit 30 s Pause hintereinander, der Stick ist fast nie frei.`;
    else if (minOk) txt += ` Kürzestes sinnvolles Intervall: ${intervalLabel(minOk).replace('alle ', '')}.`;
    hint.textContent = txt;
    hint.classList.toggle('warn-text', cur > 0 && sec > cur * 60);
  }
  for (const [dev, id] of [['pmsdr', 'rasterPmsdr'], ['rtlsdr', 'rasterRtlsdr']]) {
    $(id).addEventListener('change', async () => {
      try { const d = await post('/api/settings', { scan_raster: { [dev]: $(id).value } }); settings.scan_raster = d.settings.scan_raster; note($('rasterNote'), '✓ gespeichert – gilt ab dem nächsten Scan'); loadEstimate(); }
      catch (e) { note($('rasterNote'), 'Fehler: ' + e.message, true); }
    });
  }
  // Zurück zur Seite (anderer Tab, Handy aus dem Standby): aktuellen Stand holen
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') loadSettings().catch(() => {}); });

  function renderDeviceSwitch() {
    const root = $('deviceSwitch'); root.innerHTML = '';
    const names = settings.devices || { pmsdr: 'PMSDR', rtlsdr: 'RTL-SDR' };
    const sub = { pmsdr: 'Kurzwelle 160 m – 10 m', rtlsdr: 'VHF/UHF 50 MHz – 1.3 GHz' };
    const hw = settings.hardware || { pmsdr: { present: true }, rtlsdr: { present: true } };
    const short = id => names[id].split(' (')[0];
    for (const id of ['pmsdr', 'rtlsdr']) {
      const present = hw[id] && hw[id].present;
      const b = document.createElement('button'); b.type = 'button'; b.setAttribute('role', 'radio');
      const on = settings.active_device === id;
      b.className = (on ? 'on' : '') + (present ? '' : ' absent'); b.setAttribute('aria-checked', on);
      b.disabled = !present;
      let state = present ? sub[id] : 'nicht angeschlossen';
      if (!present && id === 'pmsdr' && hw.pmsdr && (hw.pmsdr.usb || hw.pmsdr.audio))
        state = hw.pmsdr.usb ? 'Soundkarte (Aureon) fehlt' : 'PMSDR-Platine fehlt';
      if (present && id === 'pmsdr' && hw.pmsdr.control && !['active', 'unbekannt'].includes(hw.pmsdr.control))
        state = `<span class="warn-text">steckt – Steuerung läuft nicht (${esc(hw.pmsdr.control)})</span>`;
      b.innerHTML = `<b>${esc(short(id))}</b>${state}`;
      b.addEventListener('click', async () => {
        if (settings.active_device === id || !present) return;
        try { const d = await post('/api/settings', { active_device: id }); settings = Object.assign(settings, d.settings); note($('scanSaveNote'), '✓ Gerät umgestellt – Antenne am ' + short(id) + ' anschließen'); }
        catch (e) { note($('scanSaveNote'), 'Fehler: ' + e.message, true); }
        renderDeviceSwitch(); renderBands(); renderSchedules();
      });
      root.appendChild(b);
    }
    const act = settings.active_device, choice = settings.active_device_choice || act;
    const ant = (settings.antennas || {})[act];
    let hint = `${ant ? ant + ' am ' + short(act) + '. ' : ''}Gescannt werden nur die ${act === 'rtlsdr' ? 'VHF/UHF-Bänder' : 'Kurzwellenbänder'}.`;
    if (choice !== act) hint = `${short(choice)} nicht angeschlossen – automatisch ${short(act)}. ` + hint;
    if (!hw.pmsdr?.present && !hw.rtlsdr?.present) hint = `Kein Empfänger angeschlossen – ${devName('rtlsdr')} oder ${devName('pmsdr')} anstecken.`;
    $('deviceHint').textContent = hint;
  }
  let lastHw = '';
  async function hardwareChanged(hw) {
    const key = JSON.stringify(hw || {});
    if (!lastHw) { lastHw = key; return; }
    if (key === lastHw) return;
    lastHw = key;
    try { await loadSettings(); } catch (e) { return; }
    renderDeviceSwitch(); renderBands();
    toast('Geräte geändert: ' + ['rtlsdr', 'pmsdr'].map(id => `${devName(id)} ${hw[id]?.present ? 'da' : 'weg'}`).join(' · '));
  }
  $('pskOn').addEventListener('change', async e => {
    try { const d = await post('/api/settings', { pskreporter: e.target.checked }); settings.pskreporter = d.settings.pskreporter; note($('stationNote'), e.target.checked ? '✓ PSK Reporter an – erste Spots nach etwa 5 min' : '✓ PSK Reporter aus'); }
    catch (x) { e.target.checked = false; note($('stationNote'), 'Fehler: ' + x.message, true); }
  });
  $('myCall').addEventListener('change', async () => {
    try { const d = await post('/api/settings', { my_call: $('myCall').value }); settings.my_call = d.settings.my_call; note($('stationNote'), '✓ Rufzeichen gespeichert'); }
    catch (e) { note($('stationNote'), 'Fehler: ' + e.message, true); }
  });
  $('myLocator').addEventListener('change', async () => {
    try { const d = await post('/api/settings', { my_locator: $('myLocator').value }); settings.my_locator = d.settings.my_locator; note($('stationNote'), '✓ Locator gespeichert'); cards.forEach(el => { if (el.classList.contains('open')) fillBody(el, el.__hit); }); }
    catch (e) { note($('stationNote'), 'Fehler: ' + e.message, true); }
  });

  function devName(id) { return ((settings && settings.devices && settings.devices[id]) || { pmsdr: 'PMSDR', rtlsdr: 'RTL-SDR' }[id]).split(' (')[0]; }
  function chip(label, on, cls = '') { const b = document.createElement('button'); b.type = 'button'; b.className = 'chip ' + cls + (on ? ' on' : ''); b.innerHTML = label; b.setAttribute('aria-pressed', on ? 'true' : 'false'); return b; }

  const isMore = b => b.legal === 'jedermann' || b.legal === 'restricted';
  function renderBands() {
    const root = $('bandChips'); root.innerHTML = '';
    const moreRoot = $('moreBandChips'); moreRoot.innerHTML = '';
    const active = settings.active_device || 'pmsdr';
    for (const b of bandplan) {
      if ((b.device || 'pmsdr') !== active) continue;
      const on = settings.selected_bands.includes(b.name);
      const c = chip(b.legal === 'restricted' ? `${b.name} ⚠` : b.name, on, b.legal === 'restricted' ? 'chip-restricted' : '');
      if (b.legal === 'restricted') c.title = 'Eingeschränkt: Mithören in vielen Ländern verboten (z. B. Deutschland, § 5 TDDDG) – nur einschalten, wo es erlaubt ist.';
      c.addEventListener('click', () => {
        if (on) settings.selected_bands = settings.selected_bands.filter(x => x !== b.name);
        else settings.selected_bands = bandplan.map(x => x.name).filter(n => n === b.name || settings.selected_bands.includes(n));
        renderBands(); scheduleSave('bands', { toggle_band: { name: b.name, on: !on } }); setTimeout(loadSatPasses, 1500);
      });
      (isMore(b) ? moreRoot : root).appendChild(c);
    }
    $('moreBands').hidden = !moreRoot.childElementCount;
    const seg = $('segmentList'); seg.innerHTML = '';
    const moreSeg = $('moreSegmentList'); moreSeg.innerHTML = '';
    for (const b of bandplan) {
      if ((b.device || 'pmsdr') !== active) continue;
      if (!settings.selected_bands.includes(b.name)) continue;
      const chosen = settings.selected_segments[b.name] || ['all'];
      const all = chosen.includes('all');
      const card = document.createElement('div'); card.className = 'segment-card';
      const legal = { telemetrie: 'Gerätetelemetrie (ISM) – nur eigene Geräte auswerten' }[b.legal];
      card.innerHTML = `<div class="seg-title"><span>${b.name}</span><small>${fmtMHz(b.start_hz)} – ${fmtMHz(b.stop_hz)} MHz</small></div>${legal ? `<p class="hint legal-hint">${legal}</p>` : ''}`;
      const chips = document.createElement('div'); chips.className = 'chips';
      const cAll = chip('Gesamtes Band', all, 'chip-seg');
      cAll.addEventListener('click', () => { settings.selected_segments[b.name] = ['all']; renderBands(); scheduleSave('bands', { band_segments: { band: b.name, ids: ['all'] } }); });
      chips.appendChild(cAll);
      for (const s of b.segments) {
        const on = !all && chosen.includes(s.id);
        const c = chip(s.label, on, 'chip-seg');
        c.title = s.mode_label + (s.note ? ' – ' + s.note : '');
        c.addEventListener('click', () => {
          let list = all ? [] : chosen.filter(x => x !== 'all');
          list = on ? list.filter(x => x !== s.id) : [...list, s.id];
          settings.selected_segments[b.name] = list.length ? list : ['all'];
          renderBands(); scheduleSave('bands', { toggle_segment: { band: b.name, id: s.id, on: !on } });
        });
        chips.appendChild(c);
      }
      card.appendChild(chips); (isMore(b) ? moreSeg : seg).appendChild(card);
    }
    if (!seg.childElementCount && !moreSeg.childElementCount) seg.innerHTML = '<p class="hint">Kein Band für dieses Gerät gewählt – der Scanner hätte nichts zu tun.</p>';
  }

  // ---------- Status ----------
  function humanState(s) {
    const st = s.state, ph = s.phase;
    if (st === 'queued') return 'Scan angefordert';
    if (st === 'running') return { prepare: 'Scan startet', scan: 'Bänder werden abgesucht', analyze: 'Aufnahmen werden ausgewertet', fm: 'FM/VHF-Aufnahmen', decode: 'Signale werden dekodiert', ft8decode: 'FT8 wird dekodiert', report: 'Abschluss' }[ph] || 'Scan läuft';
    if (st === 'done') return 'Scan abgeschlossen';
    if (st === 'error') return 'Fehler beim Scan';
    if (st === 'cancelled') return 'Scan abgebrochen';
    return 'Bereit';
  }
  async function pollStatus() {
    let s;
    try { s = await api('/api/status'); } catch (e) { setPill('Server nicht erreichbar', 'error'); return; }
    if (s.hardware && settings) hardwareChanged(s.hardware);
    const busy = s.state === 'running' || s.state === 'queued';
    if (s.live_active && !busy) { setPill('Live-Empfang', 'running'); }
    $('scanTitle').textContent = humanState(s);
    $('scanMsg').textContent = s.message || '';
    $('progressBar').style.width = (busy ? s.progress : (s.state === 'done' ? 100 : 0)) + '%';
    if (!(s.live_active && !busy)) setPill(busy ? `${humanState(s)} ${s.progress}%` : humanState(s), s.state === 'error' ? 'error' : busy ? 'running' : (s.state === 'done' ? 'done' : ''));
    const btn = $('scanNow'); btn.disabled = busy; btn.textContent = busy ? 'Scan läuft …' : 'Jetzt scannen';
    lastStatus = s;
    $('scanStop').hidden = !(s.state === 'running');
    $('scanStop').textContent = s.phase === 'satellite' ? 'Aufnahme abbrechen' : 'Scan abbrechen';
    const nextPast = s.next_scan_at && new Date(s.next_scan_at) < Date.now();
    const pp = s.scan_postponed;
    $('nextRun').textContent = s.scan_interval_minutes === 0 ? 'aus' : busy ? 'läuft gerade'
      : pp && nextPast ? `nach Überflug ${pp.name} (${new Date(pp.until * 1000).toLocaleTimeString(I18N.locale, { hour: '2-digit', minute: '2-digit' })})`
      : (s.next_scan_at ? (nextPast ? 'fällig – startet gleich' : `${fmtTime(s.next_scan_at, false)} · ${fmtRel(s.next_scan_at)}`) : '–');
    if (s.state === 'error' && lastState !== 'error') { toast('Scan fehlgeschlagen: ' + (s.message || ''), 8000); const tb = document.querySelector('.tabs-main [data-view=logs]'); if (tb && !tb.querySelector('.n')) tb.insertAdjacentHTML('beforeend', '<span class="n">!</span>'); }
    if (lastState && lastState !== s.state && (s.state === 'done' || s.state === 'error' || s.state === 'cancelled')) { refreshData(); if (s.state === 'done') toast('Scan abgeschlossen – Treffer aktualisiert'); }
    if (busy && s.phase !== 'scan' && lastState === 'running') { /* Zwischenstände während der Auswertung */ }
    lastState = s.state;
  }
  function setPill(text, cls) { const p = $('statusPill'); p.className = 'status-pill ' + (cls || ''); $('statusPillText').textContent = text; }
  $('scanStop').addEventListener('click', async () => {
    const sat = lastStatus && lastStatus.phase === 'satellite';
    if (!confirm(sat ? 'Satellitenaufnahme abbrechen? Bisher Aufgenommenes wird noch ausgewertet.' : 'Laufenden Scan abbrechen?')) return;
    try { await post('/api/scan-stop'); toast(sat ? 'Aufnahme wird abgebrochen …' : 'Scan wird abgebrochen …'); } catch (e) { toast('Fehler: ' + e.message); }
  });
  $('scanNow').addEventListener('click', async () => {
    if (!settings.selected_bands.length) { note($('scanSaveNote'), 'Mindestens ein Band auswählen', true); return; }
    clearTimeout(saveTimer);
    try {
      await post('/api/settings', { selected_bands: settings.selected_bands, selected_segments: settings.selected_segments, scan_interval_minutes: Number($('interval').value) });
      const r = await post('/api/scan-now');
      note($('scanSaveNote'), r.already_running ? 'Scan läuft bereits' : (r.fallback ? '✓ Scan direkt gestartet (Scheduler-Dienst läuft nicht!)' : '✓ Scan angefordert'));
      pollStatus();
    } catch (e) { note($('scanSaveNote'), 'Fehler: ' + e.message, true); }
  });

  // ---------- Daten: Statistik, Verlauf, Treffer ----------
  async function refreshData() { await Promise.allSettled([loadStats(), loadRuns(), loadHits(), loadDeletionLog(), loadLog(), loadScanSummary()]); }

  async function loadStats() {
    const s = await api('/api/stats');
    $('statTotal').textContent = s.hits_total;
    $('statNew').textContent = s.hits_new;
    const th = document.querySelector('.tabs-main [data-view=hits]'); if (th) th.innerHTML = 'Treffer' + (s.hits_new ? `<span class="n">${s.hits_new}</span>` : '');
    $('statLastHits').textContent = s.last_run ? s.last_run.peaks : '–';
    $('statRejected').textContent = s.last_run ? s.last_run.rejected : '–';
    if (s.last_run) {
      $('lastRun').textContent = `${fmtTime(s.last_run.started_at)} · ${fmtRel(s.last_run.started_at)}`;
      $('lastDuration').textContent = fmtDur(s.last_run.duration_s);
    }
    renderFilterBands(s.per_band || {});
  }
  function renderFilterBands(counts) {
    const root = $('filterBands'); root.innerHTML = '';
    const mk = (name, label) => { const c = chip(`${label}${counts[name] != null ? ` <span class="n">${counts[name]}</span>` : ''}`, filter.band === name); c.addEventListener('click', () => { filter.band = name; localStorage.setItem('f.band', name); renderFilterBands(counts); loadHits(); }); root.appendChild(c); };
    mk('all', 'Alle');
    bandplan.filter(b => !isMore(b)).forEach(b => mk(b.name, b.name));
    const more = bandplan.filter(isMore);
    if (!more.length) return;
    if (showMoreFilter || more.some(b => b.name === filter.band)) more.forEach(b => mk(b.name, b.name));
    else { const c = chip('…', false, 'chip-more'); c.title = 'weitere'; c.addEventListener('click', () => { showMoreFilter = true; renderFilterBands(counts); }); root.appendChild(c); }
  }
  let showMoreFilter = false;
  const runsPage = { offset: 0, per: Number(localStorage.getItem('runs.per') || 25), total: 0 };
  async function loadRuns() {
    $('runsPerPage').value = String(runsPage.per);
    const d = await api(`/api/runs?limit=${runsPage.per}&offset=${runsPage.offset}`);
    runsPage.total = d.total;
    const dev = { pmsdr: devName('pmsdr'), rtlsdr: devName('rtlsdr') };
    $('runsTable').querySelector('tbody').innerHTML = d.runs.map(r => {
      const st = { success: 'ok', failed: 'Fehler', running: 'läuft', aborted: 'abgebrochen', cancelled: 'abgebrochen' }[r.status] || r.status;
      const hpb = r.hits_per_band || {};
      const bands = (r.bands || []).map(b => hpb[b] ? `${esc(b)} <span class="n">${hpb[b]}</span>` : esc(b)).join(', ') || '<span class="muted">–</span>';
      const why = r.reason ? `<div class="run-why">${esc(r.reason)}</div>` : '';
      return `<tr title="Lauf ${r.id}"><td>${fmtTime(r.started_at)}<br><small class="muted">${r.finished_at ? 'bis ' + fmtTime(r.finished_at, false) : ''}</small></td>
        <td>${r.device ? `<small class="muted">${dev[r.device] || r.device}</small><br>` : ''}${bands}</td>
        <td class="${r.status === 'success' ? 'ok' : r.status === 'running' ? '' : r.status === 'cancelled' ? 'muted' : 'bad'}">${st}${why}</td>
        <td>${r.peaks ?? ''}${r.rejected ? ` <small class="muted">/ ${r.rejected}</small>` : ''}</td><td>${r.tunes ?? ''}</td><td>${fmtDur(r.duration_s)}</td></tr>`;
    }).join('') || '<tr><td colspan="6">noch kein Lauf</td></tr>';
    const a = d.total ? runsPage.offset + 1 : 0, b = Math.min(d.total, runsPage.offset + runsPage.per);
    $('runsPos').textContent = `${a}–${b} von ${d.total}`;
    $('runsPrev').disabled = runsPage.offset === 0; $('runsNext').disabled = b >= d.total;
  }
  $('runsPerPage').addEventListener('change', e => { runsPage.per = Number(e.target.value); runsPage.offset = 0; localStorage.setItem('runs.per', runsPage.per); loadRuns(); });
  $('runsPrev').addEventListener('click', () => { runsPage.offset = Math.max(0, runsPage.offset - runsPage.per); loadRuns(); });
  $('runsNext').addEventListener('click', () => { runsPage.offset += runsPage.per; loadRuns(); });
  async function loadDeletionLog() {
    const d = await api('/api/deletion-log');
    $('deletionLog').innerHTML = d.entries.slice(0, 50).map(e => `<li>${fmtTime(e.deleted_at)} · ${e.action} · ${e.band || ''} ${e.rf_hz ? fmtMHz(e.rf_hz) + ' MHz' : ''} ${e.note || ''}</li>`).join('') || '<li>leer</li>';
  }
  async function loadLog() { try { const d = await api('/api/log?n=80'); $('scanLog').textContent = d.lines.join('\n'); } catch (e) { } }

  // ---------- Treffer ----------
  let hitsTimer;
  async function loadHits() {
    const qs = new URLSearchParams({ band: filter.band, mode: filter.mode, sort: filter.sort, q: filter.q, protected: filter.protected ? '1' : '0', audio: filter.audio ? '1' : '0', limit: '400' });
    const d = await api('/api/hits?' + qs);
    const root = $('hits');
    const seen = new Set();
    let anchor = null;
    for (const h of d.hits) {
      seen.add(h.id);
      let el = cards.get(h.id);
      if (!el) { el = buildCard(h); cards.set(h.id, el); } else updateCard(el, h);
      // in richtiger Reihenfolge einsortieren, ohne alles neu zu bauen
      if (anchor ? el.previousElementSibling !== anchor : root.firstElementChild !== el) root.insertBefore(el, anchor ? anchor.nextElementSibling : root.firstElementChild);
      anchor = el;
    }
    for (const [id, el] of cards) if (!seen.has(id)) { el.remove(); cards.delete(id); open.delete(id); }
    $('hitCount').textContent = `${d.hits.length} Treffer${d.hits.length >= 400 ? ' (erste 400)' : ''}`;
    $('hitsEmpty').hidden = d.hits.length > 0;
  }

  function badges(h) {
    return `<span class="badge badge-band">${h.band}</span>${h.device === 'rtlsdr' ? `<span class="badge badge-device">${esc(devName('rtlsdr'))}</span>` : ''}<span class="badge badge-mode ${h.mode}">${h.mode_label}${h.sideband ? ' ' + h.sideband : ''}</span>${h.audio === 'ton' ? '<span class="badge badge-audio">🔊 Ton</span>' : h.audio === 'traeger' ? '<span class="badge badge-quiet">nur Träger</span>' : ''}${h.is_new ? '<span class="badge badge-new">neu</span>' : ''}${h.protected ? '<span class="badge badge-prot">★ geschützt</span>' : ''}${h.watch ? `<span class="badge badge-watch" title="Beobachtungsliste">🔔 ${esc(h.watch)}</span>` : ''}${(h.decodes || []).filter(d => d.ps).map(d => `<span class="badge badge-rds" title="RDS-Sendername">📻 ${esc(d.ps)}</span>`).join('')}`;
  }
  function buildCard(h) {
    const el = document.createElement('article'); el.className = 'hit'; el.dataset.id = h.id;
    el.innerHTML = `
      <div class="hit-head" role="button" tabindex="0" aria-expanded="false">
        <div class="hit-main">
          <div class="hit-freq">${fmtMHz(h.rf_hz)} <small>MHz</small></div>
          <div class="hit-badges"></div>
          <div class="hit-sub"></div>
        </div>
        <span class="hit-toggle" aria-hidden="true">›</span>
      </div>
      <div class="hit-body"></div>`;
    const head = el.querySelector('.hit-head');
    const toggle = () => { const o = !el.classList.contains('open'); el.classList.toggle('open', o); head.setAttribute('aria-expanded', o); if (o) { open.add(h.id); if (!el.querySelector('.hit-body').childElementCount) fillBody(el, h); } else open.delete(h.id); };
    head.addEventListener('click', toggle);
    head.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } });
    updateCard(el, h);
    return el;
  }
  function updateCard(el, h) {
    el.__hit = h;
    el.classList.toggle('is-new', h.is_new);
    el.classList.toggle('is-protected', h.protected);
    el.querySelector('.hit-freq').innerHTML = `${fmtMHz(h.rf_hz)} <small>MHz</small>`;
    el.querySelector('.hit-badges').innerHTML = badges(h);
    const dec = decodeSummary(h);
    el.querySelector('.hit-sub').textContent = `${h.snr_db.toFixed(0)} dB SNR · ${fmtTime(h.time)}${h.note ? ' · ' + h.note : ''}${dec ? ' · ' + dec : ''}`;
    // Detailbereich nur neu füllen, wenn offen und nichts spielt
    const body = el.querySelector('.hit-body');
    if (el.classList.contains('open') && body.childElementCount && !anyPlaying(body) && document.activeElement?.closest?.('.hit-body') !== body) {
      if (JSON.stringify(body.__data) !== JSON.stringify(h)) fillBody(el, h);
    }
  }
  function anyPlaying(root) { return [...root.querySelectorAll('audio')].some(a => !a.paused); }

  function player(title, src, extra = '') {
    return `<div class="player"><div class="player-title"><span>${title}</span>${extra}</div><audio controls preload="none" src="${src}"></audio></div>`;
  }
  function fillBody(el, h) {
    const body = el.querySelector('.hit-body'); body.__data = h;
    const f = h.files || {};
    let html = '';
    if (f.sstv) {
      // Download mit sprechendem Namen: hamscanpi_<Band>_<MHz>_<UTC>.png
      const stamp = (h.time || '').replace(/[-:]/g, '').slice(0, 15).replace('T', '_');
      const fname = `hamscanpi_${h.band}_${fmtMHz(h.rf_hz)}MHz_${stamp}Z.png`.replace(/[^A-Za-z0-9_.-]/g, '_');
      html += `<img class="sstv-img" src="${f.sstv}" alt="SSTV-Bild" loading="lazy">
        <div class="actions sstv-dl"><a class="btn btn-small" href="${f.sstv}" download="${fname}">↓ Bild herunterladen (PNG)</a></div>`;
    }
    if (f.iq) html += `<img src="/api/spectrum?peak_id=${h.id}&lang=${I18N.lang}&v=${encodeURIComponent(h.rec || '')}" alt="Spektrum um ${fmtMHz(h.rf_hz)} MHz" loading="lazy" onerror="this.remove()">`;
    else if (f.spectrum) html += `<img src="${f.spectrum}" alt="Spektrum um ${fmtMHz(h.rf_hz)} MHz" loading="lazy">`;
    if (h.offset_hz) html += `<p class="hint"><b>Relais</b>: Ausgabe ${fmtMHz(h.rf_hz)} MHz, Eingabe ${fmtMHz(h.rf_hz + h.offset_hz)} MHz (Ablage ${(h.offset_hz / 1e6).toFixed(1)} MHz)</p>`;
    if (h.segment_note) html += `<p class="hint">${esc(h.segment_note)}</p>`;
    const sat = h.band === 'Satelliten';
    html += `<p class="hint">${sat ? `SNR ${h.snr_db.toFixed(0)} dB · Doppler-korrigiert aufgenommen` : `Pegel ${h.level_db.toFixed(0)} dB · Rauschen ${(h.snr_db != null ? h.level_db - h.snr_db : h.noise_db).toFixed(0)} dB · SNR ${(h.snr_db != null ? h.snr_db : h.level_db - h.noise_db).toFixed(0)} dB`} · ${h.segment_id ? 'Segment ' + h.segment_id : 'kein Segment'}${h.carrier_delta_hz ? ` · Trägerkorrektur ${h.carrier_delta_hz > 0 ? '+' : ''}${h.carrier_delta_hz.toFixed(0)} Hz` : ''}</p>`;
    // Audio: FM zuerst, sonst SSB je nach Betriebsart
    if (f.fm && !f.iq) html += player('FM', f.fm);   // sonst übernimmt „Empfang“ (abstimmbar)
    if (f.iq) {
      const digital = ['ft8', 'ft4', 'wspr', 'rtty', 'digital'].includes(h.mode);
      const vhf = h.device === 'rtlsdr';
      let dmod = 'ssb';
      if (h.mode === 'am') dmod = 'am';
      else if (h.mode === 'wfm') dmod = 'wfm';
      else if (['fm', 'relais', 'dv', 'aprs', 'pocsag', 'data'].includes(h.mode) || (['sat', 'sstv'].includes(h.mode) && !h.sideband)) dmod = 'nfm';   // SSTV auf Kurzwelle: SSB
      const side = h.sideband || (digital ? 'USB' : (h.rf_hz < 10e6 ? 'LSB' : 'USB'));
      const hints = { ssb: digital ? 'Digitalmodi werden wie am Funkgerät in USB abgehört. Liegt der Ton außerhalb von 300–3000 Hz, die Abstimmung verschieben.' : 'Klingt die Stimme zu hoch/tief („Donald Duck“), die Abstimmung verschieben – grob mit ±500/±100, fein mit ±10 Hz. Bei doppeltem Signal das Seitenband wechseln.',
        am: 'AM (Flugfunk, CB). Rauscht es nur, ±5 kHz probieren – der Treffer sitzt nicht immer auf der Kanalmitte.',
        nfm: 'Schmales FM (Relais, Simplex, PMR, Pager, Digitalfunk als Rohton). Bei Digitalfunk hörst du nur das Rauschen des Signals – das ist normal.',
        wfm: 'Breites FM (UKW-Rundfunk), Mono.' };
      html += `<div class="player player-ssb" data-side="${side}" data-off="0" data-dmod="${dmod}">
        <div class="player-title"><span>Empfang <b class="side">${dmod === 'ssb' ? side : dmod.toUpperCase()}</b></span>
          <span><select class="dmod" aria-label="Demodulator">
            <option value="ssb-USB">SSB USB</option><option value="ssb-LSB">SSB LSB</option><option value="am">AM</option><option value="nfm">FM schmal</option><option value="wfm">FM breit</option>
          </select> <select class="nr" aria-label="Rauschminderung" title="Rauschminderung (wie DNR am Transceiver)">
            <option value="0">ohne DNR</option><option value="1">DNR mittel</option><option value="2">DNR stark</option>
          </select></span></div>
        <audio controls preload="none"></audio>
        <div class="tune"><span class="hint">Abstimmung:</span>
          ${vhf ? '<button type="button" class="btn btn-small" data-tune="-12500">−12.5k</button>' : ''}<button type="button" class="btn btn-small" data-tune="-500">−500</button><button type="button" class="btn btn-small" data-tune="-100">−100</button><button type="button" class="btn btn-small" data-tune="-10">−10</button>
          <span class="val">0 Hz</span>
          <button type="button" class="btn btn-small" data-tune="10">+10</button><button type="button" class="btn btn-small" data-tune="100">+100</button><button type="button" class="btn btn-small" data-tune="500">+500</button>${vhf ? '<button type="button" class="btn btn-small" data-tune="12500">+12.5k</button>' : ''}
          <button type="button" class="btn btn-small" data-tune="0">0</button>
        </div>
        <p class="hint dmod-hint">${hints[dmod]}</p>
        <a class="btn btn-small audio-dl" href="#" download>↓ Audio so speichern, wie es gerade klingt</a>
      </div>`;
      body.__hints = hints;
    } else if (f.usb || f.lsb) {
      if (f.usb) html += player('USB (fertige Datei)', f.usb);
      if (f.lsb) html += player('LSB (fertige Datei)', f.lsb);
    }
    if (h.decodes.length) html += renderDecodes(h);
    else if (['ft8', 'ft4', 'cw', 'rtty', 'digital', 'wspr', 'aprs', 'pocsag'].includes(h.mode)) html += `<p class="hint">Noch nichts dekodiert${h.mode === 'wspr' ? ' – für WSPR gibt es keinen Dekoder auf dem Pi' : ' (läuft nach dem Scan, oder das Signal war zu kurz/zu schwach)'}.</p>`;
    html += `<div class="note-row"><input type="text" class="note" placeholder="Notiz / Rufzeichen" value="${esc(h.note)}" maxlength="200"><button type="button" class="btn btn-small" data-act="note">Speichern</button></div>`;
    html += `<div class="hit-actions">
      <button type="button" class="btn btn-small btn-live" data-act="live">▶ Jetzt live hören</button>
      <button type="button" class="btn btn-small" data-act="protect">${h.protected ? '☆ Schutz aufheben' : '★ Behalten'}</button>
      ${f.iq ? `<a class="btn btn-small" href="${f.iq}" title="Rohdaten, Stereo I/Q – für SDR-Software, nicht zum Anhören">↓ I/Q-Rohdaten (für SDR-Software)</a>` : ''}
      ${f.usb ? `<a class="btn btn-small" href="${f.usb}" download>↓ USB</a>` : ''}${f.lsb ? `<a class="btn btn-small" href="${f.lsb}" download>↓ LSB</a>` : ''}
      <button type="button" class="btn btn-small btn-danger" data-act="delete">Löschen</button></div>`;
    body.innerHTML = html;

    const ssb = body.querySelector('.player-ssb');
    if (ssb) {
      const audio = ssb.querySelector('audio');
      const sel = ssb.querySelector('.dmod');
      sel.value = ssb.dataset.dmod === 'ssb' ? 'ssb-' + ssb.dataset.side : ssb.dataset.dmod;
      const apply = () => { const side = ssb.dataset.side, off = Number(ssb.dataset.off), dm = ssb.dataset.dmod; ssb.querySelector('.side').textContent = dm === 'ssb' ? side : dm.toUpperCase(); ssb.querySelector('.val').textContent = (off > 0 ? '+' : '') + off + ' Hz'; ssb.querySelector('.dmod-hint').textContent = body.__hints[dm]; const wasPlaying = !audio.paused; const nr = ssb.querySelector('.nr').value; const url = `/api/demod?peak_id=${h.id}&mode=${dm}&side=${side}&offset_hz=${off}&nr=${nr}`; audio.src = url; ssb.querySelector('.audio-dl').href = url + '&download=1'; if (wasPlaying) audio.play().catch(() => { }); };
      const nrSel = ssb.querySelector('.nr');
      nrSel.value = localStorage.getItem('dnr') || '0';          // zuletzt gewählte DNR-Stufe
      nrSel.addEventListener('change', () => { localStorage.setItem('dnr', nrSel.value); apply(); });
      apply();
      sel.addEventListener('change', () => { const v = sel.value; if (v.startsWith('ssb-')) { ssb.dataset.dmod = 'ssb'; ssb.dataset.side = v.slice(4); } else ssb.dataset.dmod = v; apply(); });
      ssb.querySelectorAll('[data-tune]').forEach(b => b.addEventListener('click', () => { const v = Number(b.dataset.tune); ssb.dataset.off = v === 0 ? 0 : Math.max(-100000, Math.min(100000, Number(ssb.dataset.off) + v)); ssb.querySelector('.val').textContent = (Number(ssb.dataset.off) > 0 ? '+' : '') + ssb.dataset.off + ' Hz'; clearTimeout(ssb.__t); ssb.__t = setTimeout(apply, 350); }));
    }
    body.querySelector('[data-act=live]').addEventListener('click', () => window.__liveFromHit(h));
    body.querySelector('[data-act=protect]').addEventListener('click', async () => { try { await post('/api/protect-hit', { peak_id: h.id, protected: !h.protected }); toast(h.protected ? 'Schutz aufgehoben' : 'Treffer wird behalten'); loadHits(); loadStats(); } catch (e) { toast('Fehler: ' + e.message); } });
    body.querySelector('[data-act=delete]').addEventListener('click', async () => { if (!confirm(`Treffer ${fmtMHz(h.rf_hz)} MHz löschen?`)) return; try { await post('/api/delete-hit', { peak_id: h.id }); toast('Gelöscht'); loadHits(); loadStats(); loadDeletionLog(); } catch (e) { toast('Fehler: ' + e.message); } });
    body.querySelector('[data-act=note]').addEventListener('click', async () => { try { await post('/api/note', { peak_id: h.id, note: body.querySelector('.note').value }); toast('Notiz gespeichert'); loadHits(); } catch (e) { toast('Fehler: ' + e.message); } });
  }
  // ---------- Deutung von FT8/FT4-Nachrichten ----------
  const CALL_RX = /^(?:[A-Z0-9]{1,3}\/)?[A-Z0-9]{1,3}[0-9][A-Z0-9]{0,3}[A-Z](?:\/[A-Z0-9]{1,4})?$/;
  const GRID_RX = /^[A-R]{2}[0-9]{2}(?:[A-X]{2})?$/;
  const isCall = t => CALL_RX.test(t) && !/^(CQ|DE|QRZ|RR73|RRR|73|DX|POTA|SOTA|TEST|NA|EU|AS|AF|OC|SA)$/.test(t);
  function gridToLatLon(g) {
    g = g.toUpperCase(); if (!/^[A-R]{2}[0-9]{2}/.test(g)) return null;
    let lon = (g.charCodeAt(0) - 65) * 20 - 180, lat = (g.charCodeAt(1) - 65) * 10 - 90;
    lon += (+g[2]) * 2; lat += (+g[3]);
    if (g.length >= 6) { lon += (g.charCodeAt(4) - 65) * (2 / 24) + 1 / 24; lat += (g.charCodeAt(5) - 65) * (1 / 24) + 1 / 48; }
    else { lon += 1; lat += .5; }
    return { lat, lon };
  }
  function distBearing(a, b) {
    const R = 6371, r = Math.PI / 180, dLat = (b.lat - a.lat) * r, dLon = (b.lon - a.lon) * r;
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(dLon / 2) ** 2;
    const d = 2 * R * Math.asin(Math.sqrt(h));
    const y = Math.sin(dLon) * Math.cos(b.lat * r), x = Math.cos(a.lat * r) * Math.sin(b.lat * r) - Math.sin(a.lat * r) * Math.cos(b.lat * r) * Math.cos(dLon);
    return { km: Math.round(d), deg: Math.round((Math.atan2(y, x) * 180 / Math.PI + 360) % 360) };
  }
  function interpret(text) {
    const t = text.trim().toUpperCase().replace(/\s+/g, ' ').split(' ');
    const out = { text, kind: 'other', calls: [], grid: null, desc: '' };
    if (t[0] === 'CQ') {
      let i = 1; let tag = '';
      while (i < t.length && !isCall(t[i])) tag += (tag ? ' ' : '') + t[i++];
      const call = t[i]; const grid = t[i + 1] && GRID_RX.test(t[i + 1]) && t[i + 1] !== 'RR73' ? t[i + 1] : null;
      if (call) { out.kind = 'cq'; out.calls = [call]; out.grid = grid; out.desc = `CQ${tag ? ' ' + tag : ''} von ${call}${grid ? ' aus ' + grid : ''}`; }
      return out;
    }
    if (t.length >= 2 && isCall(t[0]) && isCall(t[1])) {
      const [to, from] = t; const rest = t.slice(2).join(' ');
      out.calls = [from, to];
      if (!rest) { out.kind = 'call'; out.desc = `${from} ruft ${to}`; }
      else if (/^(RR73|RRR|73)$/.test(rest)) { out.kind = 'end'; out.desc = `${from} → ${to}: ${rest} – QSO abgeschlossen`; }
      else if (GRID_RX.test(rest)) { out.kind = 'answer'; out.grid = rest; out.desc = `${from} (${rest}) antwortet ${to}`; }
      else if (/^R?[+-]\d{1,2}$/.test(rest)) { out.kind = 'report'; out.desc = `${from} gibt ${to} Rapport ${rest.replace('R', '')}${rest.startsWith('R') ? ' (bestätigt)' : ''}`; }
      else { out.desc = `${from} → ${to}: ${rest}`; }
      return out;
    }
    out.desc = text; return out;
  }
  function decodeSummary(h) {
    const parts = [];
    for (const d of h.decodes) {
      if ((d.type === 'FT8' || d.type === 'FT4') && d.messages?.length) {
        const calls = new Set(); let cq = 0;
        d.messages.forEach(m => { const i = interpret(m.text); i.calls.forEach(c => calls.add(c)); if (i.kind === 'cq') cq++; });
        parts.push(`${d.type}: ${d.messages.length} Nachr., ${calls.size} Stationen${cq ? `, ${cq}× CQ` : ''}`);
      } else if (d.type === 'RDS' && d.rds) parts.push(`RDS: ${rdsShort(d.rds)}`);
      else if (d.plausible && d.text) parts.push(`${d.type}: ${d.text.slice(0, 40)}`);
    }
    return parts.join(' · ');
  }
  function renderDecodes(h) {
    const me = settings?.my_locator ? gridToLatLon(settings.my_locator) : null;
    let html = '';
    for (const d of h.decodes) {
      if ((d.type === 'FT8' || d.type === 'FT4') && d.messages?.length) {
        const stations = new Map();
        const rows = d.messages.map(m => {
          const i = interpret(m.text);
          i.calls.forEach((c, k) => { const st = stations.get(c) || { call: c, grid: null, n: 0 }; st.n++; if (k === 0 && i.grid) st.grid = i.grid; stations.set(c, st); });
          return `<tr><td>${m.snr > 0 ? '+' : ''}${Math.round(m.snr)}</td><td>${m.audio_hz}</td><td class="mono">${esc(m.text)}</td><td>${esc(i.desc)}</td></tr>`;
        });
        html += `<div class="decode-block"><div class="decode-title"><b>${d.type}</b> · ${d.messages.length} Nachrichten${d.detail ? ' · ' + esc(d.detail) : ''}</div>
          <div class="table-wrap"><table class="table"><thead><tr><th>SNR dB</th><th>Hz</th><th>Nachricht</th><th>Bedeutung</th></tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
        const list = [...stations.values()].sort((a, b) => b.n - a.n).map(st => {
          let extra = st.grid ? ` <span class="muted">${st.grid}` : '';
          if (st.grid && me) { const p = gridToLatLon(st.grid); if (p) { const db = distBearing(me, p); extra += ` · ${db.km} km · ${db.deg}°`; } }
          return `<span class="station">${st.call}${extra ? extra + '</span>' : ''}</span>`;
        });
        html += `<div class="stations">${list.join('')}</div>${!me ? '<p class="hint">Locator unter „Bänder &amp; Betriebsarten“ eintragen, dann stehen hier Entfernung und Richtung.</p>' : ''}</div>`;
      } else if (d.type === 'RDS' && d.rds) {
        const r = d.rds, ok = r.blocks ? Math.round(100 * r.blocks_ok / r.blocks) : 0;
        html += `<div class="decode-block rds-box"><div class="decode-title"><b>RDS</b> · <span>${r.groups} Gruppen, ${ok} % fehlerfrei</span> · ${(h.rf_hz / 1e6).toFixed(1)} MHz</div>${rdsBody(r)}</div>`;
      } else {
        html += `<div class="decode-block ${d.plausible ? '' : 'implausible'}"><div class="decode-title"><b>${d.type}</b> · ${d.confidence}%${d.detail ? ' · ' + esc(d.detail) : ''}${d.plausible ? '' : ' · vermutlich Rauschen'}</div>${d.text ? `<div class="mono pre">${esc(d.text)}</div>` : ''}</div>`;
      }
    }
    return html;
  }
  function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  // Filter
  $('filterMode').addEventListener('change', e => { filter.mode = e.target.value; localStorage.setItem('f.mode', filter.mode); loadHits(); });
  $('sort').value = filter.sort;
  $('sort').addEventListener('change', e => { filter.sort = e.target.value; localStorage.setItem('f.sort', filter.sort); loadHits(); });
  $('search').addEventListener('input', e => { filter.q = e.target.value.trim(); clearTimeout(hitsTimer); hitsTimer = setTimeout(loadHits, 300); });
  $('filterProtected').addEventListener('change', e => { filter.protected = e.target.checked; loadHits(); });
  $('filterAudio').checked = filter.audio;
  $('filterAudio').addEventListener('change', e => { filter.audio = e.target.checked; localStorage.setItem('f.audio', filter.audio ? '1' : '0'); loadHits(); });

  // Aufräumen
  $('cleanupPreview').addEventListener('click', async () => { try { const d = await api('/api/delete-preview?days=' + $('cleanupDays').value); note($('cleanupNote'), `${d.delete} würden gelöscht, ${d.protected} geschützte bleiben`); } catch (e) { note($('cleanupNote'), e.message, true); } });
  $('cleanupOld').addEventListener('click', async () => { const days = $('cleanupDays').value; if (!confirm(`Alle ungeschützten Treffer älter als ${days} Tage löschen?`)) return; try { const d = await post('/api/delete-old', { days: Number(days) }); note($('cleanupNote'), `${d.deleted} gelöscht`); refreshData(); } catch (e) { note($('cleanupNote'), e.message, true); } });
  $('cleanupAll').addEventListener('click', async () => { if (!confirm('Wirklich ALLE ungeschützten Treffer samt Aufnahmen löschen?')) return; try { const d = await post('/api/delete-all-unprotected'); note($('cleanupNote'), `${d.deleted} gelöscht`); refreshData(); } catch (e) { note($('cleanupNote'), e.message, true); } });

  // ---------- Live-Empfang ----------
  const live = { ctx: null, reader: null, nextT: 0, active: false, rate: 24000 };
  let liveTouched = 0;                 // nach eigener Abstimmung nicht sofort vom Status überschreiben
  const lf = () => Math.round(parseFloat($('liveFreq').value.replace(',', '.')) * 1e6);
  function liveSetUI(st) {
    const on = !!st.active;
    live.active = on;
    $('panel-live').classList.toggle('on', on);
    $('liveStart').hidden = on; $('liveStop').hidden = !on;
    $('liveText').textContent = st.text || (on ? 'läuft' : 'aus');
    $('liveText').classList.toggle('warn', on && (st.clip_pct || 0) > 0.2);
    if (on && st.active_hz && Date.now() - liveTouched > 2000 && !document.activeElement?.isSameNode($('liveFreq'))) $('liveFreq').value = (st.active_hz / 1e6).toFixed(st.device === 'rtlsdr' ? 4 : 5);
    if (on && st.scan && st.active_hz) $('liveText').textContent = st.text;
    if (on && st.mode && Date.now() - liveTouched > 2000 && document.activeElement !== $('liveMode') && $('liveMode').value !== st.mode) $('liveMode').value = st.mode;
    const lvl = Math.max(0, Math.min(100, (Number(st.level_db ?? -120) + 60) * 2));
    $('liveLevel').style.width = (on ? lvl : 0) + '%';
    renderRds(on ? st.rds : null);
  }
  function liveEnsureCtx() {
    // muss synchron im Klick passieren, sonst bleibt der Browser stumm
    if ($('liveFallback').checked) return;
    try {
      if (!live.ctx) live.ctx = new (window.AudioContext || window.webkitAudioContext)();
      if (live.ctx.state === 'suspended') live.ctx.resume();
    } catch (e) { $('liveFallback').checked = true; }
  }
  async function liveStart(body) {
    liveEnsureCtx();
    $('liveText').textContent = 'starte …';
    try {
      const d = await post('/api/live/start', body);
      liveSetUI(Object.assign({ active: true }, d.live));
      if (!body.preset && !body.seek) liveRemember(body.freq_hz, body.mode);
      await liveAudio();
    } catch (e) {
      // Stick belegt: anbieten, den Scan bzw. die Satellitenaufnahme dafür abzubrechen
      if (!body.stop_scan && /läuft ein Scan/.test(e.message) && confirm('Gerade läuft ein Scan oder eine Satellitenaufnahme. Abbrechen und live hören?')) {
        $('liveText').textContent = 'Scan wird abgebrochen …';
        return liveStart(Object.assign({}, body, { stop_scan: true }));
      }
      $('liveText').textContent = 'Fehler: ' + e.message; toast('Live: ' + e.message, 6000); liveSetUI({ active: false }); $('liveText').textContent = 'Fehler: ' + e.message;
    }
  }
  async function liveAudio() {
    const el = $('liveAudio');
    if ($('liveFallback').checked) {
      el.hidden = false; el.src = '/api/live/stream.wav?t=' + Date.now();
      el.volume = Math.min(1, liveVolume());
      el.play().catch(e => { $('liveText').textContent = 'Player: ' + e.message; });
      return;
    }
    el.hidden = true; el.removeAttribute('src');
    if (!live.ctx) liveEnsureCtx();
    if (live.ctx && live.ctx.state === 'suspended') { try { await live.ctx.resume(); } catch (e) { } }
    if (!live.ctx || live.ctx.state !== 'running') { $('liveText').textContent = 'Browser lässt kein Audio zu – „einfacher Player“ anhaken und nochmal Hören drücken'; return; }
    if (live.reader) { try { await live.reader.cancel(); } catch (e) { } }
    const r = await fetch('/api/live/audio', { cache: 'no-store' });
    if (!r.ok) { $('liveText').textContent = 'Audio-Strom nicht verfügbar (' + r.status + ')'; return; }
    live.rate = Number(r.headers.get('X-Audio-Rate') || 24000);
    live.reader = r.body.getReader(); live.nextT = 0;
    let carry = new Uint8Array(0);
    (async () => {
      while (true) {
        const { value, done } = await live.reader.read();
        if (done) break;
        let buf = new Uint8Array(carry.length + value.length); buf.set(carry); buf.set(value, carry.length);
        const n = Math.floor(buf.length / 2);
        const pcm = new Int16Array(buf.buffer, 0, n);
        carry = buf.slice(n * 2);
        if (!n) continue;
        const ab = live.ctx.createBuffer(1, n, live.rate); const ch = ab.getChannelData(0);
        for (let i = 0; i < n; i++) ch[i] = pcm[i] / 32768;
        const src = live.ctx.createBufferSource(); src.buffer = ab; src.connect(liveGainNode());
        const now = live.ctx.currentTime;
        if (live.nextT < now + 0.05) live.nextT = now + 0.25;   // kleiner Vorlauf gegen Aussetzer
        src.start(live.nextT); live.nextT += ab.duration;
      }
    })().catch(() => { });
  }
  async function liveStop() {
    try { await post('/api/live/stop'); } catch (e) { }
    if (live.reader) { try { await live.reader.cancel(); } catch (e) { } live.reader = null; }
    const el = $('liveAudio'); el.pause(); el.removeAttribute('src'); el.hidden = true;
    liveSetUI({ active: false });
  }
  $('liveStart').addEventListener('click', () => liveStart({ freq_hz: lf(), mode: $('liveMode').value, squelch_db: Number($('liveSquelch').value), gain: $('liveGain').value }));
  $('liveStop').addEventListener('click', liveStop);
  // ausstehendes Nachstimmen (Feld verlassen) verwerfen – es würde den Suchlauf sofort wieder beenden
  const liveSeek = dir => (clearTimeout(tuneTimer), liveStart({ seek: dir, freq_hz: lf(), mode: $('liveMode').value, step_hz: Number($('liveStep').value), squelch_db: Number($('liveSquelch').value), gain: $('liveGain').value }));
  $('liveSeekUp').addEventListener('click', () => liveSeek('up'));
  $('liveSeekDown').addEventListener('click', () => liveSeek('down'));
  let tuneTimer;
  // ---------- Wasserfall ----------
  const wfPal = (() => { const p = []; for (let i = 0; i < 256; i++) { const t = i / 255;
    const r = Math.round(255 * Math.min(1, Math.max(0, (t - .55) * 3))), g = Math.round(255 * Math.min(1, Math.max(0, t < .5 ? t * 1.6 : 1.3 - (t - .5) * .8))),
          b = Math.round(255 * Math.min(1, Math.max(0, t < .35 ? .25 + t * 2 : 1 - (t - .35) * 2.2))); p.push([r, g, b]); } return p; })();
  let wfSeq = 0, wfInfo = null, wfBusy = false;
  async function pollWaterfall() {
    if (wfBusy || !live.active || $('wfWrap').parentElement.closest('[hidden]') || document.visibilityState !== 'visible') return;
    wfBusy = true;
    try {
      const w = await api('/api/live/waterfall');
      if (w.row && w.row.length && w.seq !== wfSeq) {
        wfSeq = w.seq; wfInfo = w; $('wfWrap').hidden = false;
        const c = $('wf'), ctx = c.getContext('2d');
        ctx.drawImage(c, 0, 0, c.width, c.height - 1, 0, 1, c.width, c.height - 1);      // nach unten schieben
        const img = ctx.createImageData(c.width, 1), n = w.row.length;
        for (let x = 0; x < c.width; x++) { const v = w.row[Math.floor(x * n / c.width)], p = wfPal[v]; img.data.set([p[0], p[1], p[2], 255], x * 4); }
        ctx.putImageData(img, 0, 0);
        const off = w.active_hz && w.span_hz ? (w.active_hz - (w.center_hz - w.span_hz / 2)) / w.span_hz : -1;
        if (off >= 0 && off <= 1) { ctx.fillStyle = 'rgba(255,255,255,.85)'; ctx.fillRect(Math.round(off * c.width), 0, 1, 6); }
        const k = v => (v / 1e6).toFixed(w.span_hz < 100000 ? 4 : 3);
        $('wfLo').textContent = k(w.center_hz - w.span_hz / 2); $('wfMid').textContent = k(w.center_hz); $('wfHi').textContent = k(w.center_hz + w.span_hz / 2);
      }
    } catch (e) { } finally { wfBusy = false; }
  }
  setInterval(pollWaterfall, 150);
  $('wf').addEventListener('click', e => {                 // antippen: auf diese Frequenz abstimmen
    if (!wfInfo || !wfInfo.span_hz) return;
    const r = $('wf').getBoundingClientRect(), x = (e.clientX - r.left) / r.width;
    const step = ['usb', 'lsb'].includes($('liveMode').value) ? 100 : 12500;
    const f = Math.round((wfInfo.center_hz - wfInfo.span_hz / 2 + x * wfInfo.span_hz) / step) * step;
    $('liveFreq').value = (f / 1e6).toFixed(4); liveRetune();
  });
  const liveRetune = () => { liveTouched = Date.now(); if (!live.active) return; clearTimeout(tuneTimer); tuneTimer = setTimeout(() => post('/api/live/tune', { freq_hz: lf(), mode: $('liveMode').value, squelch_db: Number($('liveSquelch').value), gain: $('liveGain').value }).then(() => liveRememberLater(lf(), $('liveMode').value)).catch(e => toast(e.message)), 250); };
  // „Zuletzt gehört“ erst nach 5 s auf derselben Frequenz (kein Eintrag für jeden Zwischenschritt)
  let rememberTimer = null;
  function liveRememberLater(f, m) { clearTimeout(rememberTimer); rememberTimer = setTimeout(() => { if (live.active && Math.abs(lf() - f) < 100 && $('liveMode').value === m) liveRemember(f, m); }, 5000); }
  document.querySelectorAll('[data-livestep]').forEach(b => b.addEventListener('click', () => {
    let v = Number($('liveStep').value); if ($('liveMode').value === 'wfm' && v < 100000) v = 100000;   // UKW: 100-kHz-Raster
    const s = v === 8333 ? 25000 / 3 : v, dir = Number(b.dataset.livestep);
    const f = Math.round((Math.round(lf() / s) + dir) * s);       // auf das Raster einrasten
    $('liveFreq').value = (f / 1e6).toFixed(v % 1000 ? 5 : 4); liveRetune();
  }));
  $('liveFreq').addEventListener('change', liveRetune);
  $('liveMode').addEventListener('change', liveRetune);
  $('liveSquelch').addEventListener('change', liveRetune);
  $('liveGain').addEventListener('change', () => { try { localStorage.setItem('liveGain', $('liveGain').value); } catch (e) { } liveRetune(); });
  try { const g = localStorage.getItem('liveGain'); if (g !== null && [...$('liveGain').options].some(o => o.value === g)) $('liveGain').value = g; } catch (e) { }
  // Lautstärke (0–150 %, über 100 % lauter als die Sendung), je Browser gemerkt
  function liveVolume() { return Number($('liveVol').value) / 100; }
  function liveGainNode() {
    if (!live.gain || live.gain.context !== live.ctx) { live.gain = live.ctx.createGain(); live.gain.connect(live.ctx.destination); }
    live.gain.gain.value = liveVolume();
    return live.gain;
  }
  function setVol() {
    $('liveVolTxt').textContent = $('liveVol').value + ' %';
    if (live.gain) live.gain.gain.value = liveVolume();
    $('liveAudio').volume = Math.min(1, liveVolume());
    try { localStorage.setItem('liveVol', $('liveVol').value); } catch (e) { }
  }
  try { const v = localStorage.getItem('liveVol'); if (v !== null) $('liveVol').value = v; } catch (e) { }
  $('liveVol').addEventListener('input', setVol); setVol();
  $('liveFallback').addEventListener('change', () => { if (live.active) { liveEnsureCtx(); liveAudio(); } });

  async function pollLive() { try { const st = await api('/api/live/status'); if (st.active || live.active) liveSetUI(st); } catch (e) { } }
  // Treffer direkt live hören
  window.__liveFromHit = (h) => {
    const m = h.mode === 'am' ? 'am' : h.mode === 'wfm' ? 'wfm' : (h.sideband ? h.sideband.toLowerCase() : (['fm', 'relais', 'dv', 'aprs', 'pocsag', 'data', 'sat'].includes(h.mode) ? 'nfm' : (h.rf_hz < 10e6 ? 'lsb' : 'usb')));
    const f = h.rf_hz + (h.carrier_delta_hz || 0);
    $('liveFreq').value = (f / 1e6).toFixed(h.device === 'rtlsdr' ? 4 : 5); $('liveMode').value = m;
    showView('live');
    liveStart({ freq_hz: Math.round(f), mode: m, squelch_db: 0, device: h.device });
  };

  // ---------- Protokoll ----------
  const EVENT_TEXT = {
    run_start: 'Scan gestartet', run_done: 'Scan fertig', run_failed: 'Scan fehlgeschlagen', run_cancelled: 'Scan abgebrochen',
    run_interrupted: 'Scan unterbrochen', scan_start: 'Scan wird gestartet', scan_postponed: 'Scan verschoben', scan_stopped: 'Scan beendet',
    trigger_ignored: 'Scan-Anforderung ignoriert', band_start: 'Band wird abgesucht', band_skipped: 'Band übersprungen',
    device_opened: 'Empfänger geöffnet', device_open_failed: 'Empfänger lässt sich nicht öffnen', hit: 'Treffer',
    rtl_no_data: 'RTL-SDR liefert keine Daten', rtl_recovered: 'RTL-SDR läuft wieder',
    satellite_start: 'Überflug: Aufnahme startet', satellite_skipped: 'Überflug übersprungen', satellite_retry: 'Überflug: Start wiederholt', satellite_problem: 'Satelliten: Problem', satellite_paused: 'Satelliten pausiert (PMSDR gewählt)', band_scanned: 'Band abgehört', 'live.start': 'Live-Empfang gestartet', 'live.stop': 'Live-Empfang beendet', 'live.tune': 'Live-Empfang abgestimmt', api_error: 'Fehler bei einer Anfrage', stopped_for_live: 'Für Live-Empfang abgebrochen', pass_cancelled: 'Überflug abgebrochen (Benutzer)', satellite_stop_requested: 'Abbruch der Satellitenaufnahme angefordert', pskreporter_sent: 'An PSK Reporter gemeldet', pskreporter_failed: 'PSK Reporter: Fehler', watch_hit: 'Beobachtung ausgelöst', watch_failed: 'Beobachtungsliste: Fehler', auto_cleanup: 'Automatisch aufgeräumt', sat_raw_deleted: 'Rohdaten eines Überflugs gelöscht', disk_full: 'Speicherkarte fast voll', disk_low: 'Speicherkarte wird voll', disk_ok: 'Speicherplatz wieder ok', pass_aborted: 'Überflug abgebrochen', scan_skipped_disk_full: 'Scan ausgelassen (Speicher voll)', housekeeping_failed: 'Haushalt fehlgeschlagen',
    satellite_failed: 'Satelliten: Fehler', satellite_warning: 'Satelliten: Hinweis', pass_start: 'Überflug: Aufnahme läuft',
    pass_done: 'Überflug fertig', pass_failed: 'Überflug fehlgeschlagen', pass_eval_failed: 'Überflug: Auswertung fehlgeschlagen',
    pass_recording_problem: 'Überflug: Aufnahme gestört', loop_exception: 'Scheduler: Fehler in der Schleife',
    channel_watch_failed: 'Kanalbeobachtung fehlgeschlagen', voice_watch_failed: 'Sprachbeobachtung fehlgeschlagen',
    live_start: 'Live-Empfang gestartet', login: 'Anmeldung', login_failed: 'Anmeldung fehlgeschlagen', login_blocked: 'Anmeldung gesperrt', password_set: 'Passwort gesetzt', auth_disabled: 'Zugangsschutz ausgeschaltet', run_summary: 'Scan-Zusammenfassung', phase_start: 'Auswertung', decode_done: 'Dekodierung fertig', live_stop: 'Live-Empfang beendet', notify_sent: 'Mail verschickt', notify_failed: 'Mail fehlgeschlagen',
  };
  const KEY_TEXT = { run_id: 'Lauf', peaks: 'Treffer', rejected: 'verworfen', duration_s: 'Dauer s', band: 'Band', sat: 'Satellit', error: 'Fehler',
    reason: 'Grund', hits: 'Treffer', channels: 'Kanäle', max_el: 'max. Höhe°', freq_hz: 'Frequenz', rf_hz: 'Frequenz', device: 'Gerät',
    problem: 'Problem', interval_min: 'Intervall min', rule: 'Regel', detail: 'Was', coverage: 'abgehört (MHz)', windows: 'Fenstermitten (MHz)', segments: 'Segmente', channels_n: 'Kanäle', digital: 'Digital (MHz)', freq_mhz: 'Frequenzen (MHz)', seconds_band: 'Dauer s', seconds: 'Sekunden', tune_hz: 'Mitte', until: 'bis', per_channel: 'je Kanal', plan: 'Plan' };
  function fmtVal(k, v) {
    if (v == null) return '';
    if ((k.endsWith('_hz') || k === 'rf_hz') && typeof v === 'number') return (v / 1e6).toFixed(4) + ' MHz';
    if (Array.isArray(v)) return v.join(', ');
    if (typeof v === 'object') return Object.entries(v).map(([a, b]) => `${a}: ${typeof b === 'object' ? JSON.stringify(b) : b}`).join(', ');
    return String(v);
  }
  const logPage = { offset: 0 };
  async function loadLogs() {
    try {
      const per = Number($('logPerPage').value);
      const sum = await api('/api/logs/summary?hours=24');
      $('logSummary').innerHTML = ['ERROR', 'WARN', 'INFO', 'DEBUG'].map(l => `<span class="lvl ${l}">${l} ${sum.by_level[l] || 0}</span>`).join('') + `<span class="lvl">24 h · ${sum.total} Einträge</span>`;
      const qs = new URLSearchParams({ level: $('logLevel').value, component: $('logComponent').value, q: $('logQuery').value, limit: per, offset: logPage.offset });
      const d = await api('/api/logs?' + qs);
      // gleiche, direkt aufeinanderfolgende Meldungen zusammenfassen (z. B. 36× derselbe Fehler)
      const rows = [];
      for (const r of d.entries) {
        const sig = r.level + r.component + r.event + JSON.stringify(r.data || {});
        const last = rows[rows.length - 1];
        if ($('logGroup').checked && last && last.sig === sig) { last.n++; last.first = r.ts; continue; }
        rows.push({ r, sig, n: 1, first: r.ts });
      }
      $('logList').innerHTML = rows.map(({ r, n, first }) => {
        const data = r.data || {};
        const mainKeys = r.event === 'band_scanned' ? ['band', 'coverage', 'hits'] : r.event === 'pass_start' ? ['sat', 'freq_mhz'] : r.event === 'watch_hit' ? ['rule', 'detail'] : ['error', 'reason', 'problem', 'sat', 'band', 'hits', 'peaks'];
        const evName = EVENT_TEXT[`${r.component}.${r.event}`] || EVENT_TEXT[r.event] || r.event;
        const main = mainKeys.filter(k => data[k] != null).map(k => `${KEY_TEXT[k] || k}: ${esc(fmtVal(k, data[k]))}`).join(' · ');
        const all = Object.entries(data).map(([k, v]) => `<tr><td>${esc(KEY_TEXT[k] || k)}</td><td class="mono">${esc(fmtVal(k, v))}</td></tr>`).join('');
        // Uhrzeit; ältere Tage mit Datum darunter (passt in die schmale Spalte)
        const full = fmtTime(r.ts, true).replace(' UTC', '').replace(/^(heute|today) /, '');
        const mt = full.match(/^(.*\S)\s+(\d\d:\d\d:\d\d)$/);
        const when = mt ? `${mt[2]}<br><small class="muted">${esc(mt[1].replace(/^\S+\s+/, ''))}</small>` : full;
        const span = n > 1 ? `<span class="rep">${n}× seit ${fmtTime(first, true).replace(' UTC', '').replace(/^(heute|today) /, '')}</span>` : '';
        return `<li class="${r.level}"><span>${when}</span><span class="lv">${r.level}</span><span>${esc(r.component)}</span><div class="lmain"><details><summary><b>${esc(evName)}</b> ${span} <span class="d">${main}</span></summary>
          <table class="table log-detail"><tr><td>Ereignis</td><td class="mono">${esc(r.event)}</td></tr>${all}<tr><td>Zeit (UTC)</td><td class="mono">${esc(r.ts)}</td></tr></table></details></div></li>`;
      }).join('') || '<li>keine Einträge</li>';
      $('logPos').textContent = d.entries.length ? `Einträge ${logPage.offset + 1}–${logPage.offset + d.entries.length}` : '';
      $('logPrev').disabled = logPage.offset === 0; $('logNext').disabled = !d.has_more;
    } catch (e) { }
  }
  $('logPrev').addEventListener('click', () => { logPage.offset = Math.max(0, logPage.offset - Number($('logPerPage').value)); loadLogs(); });
  $('logNext').addEventListener('click', () => { logPage.offset += Number($('logPerPage').value); loadLogs(); });
  ['logPerPage', 'logGroup'].forEach(id => $(id).addEventListener('change', () => { logPage.offset = 0; loadLogs(); }));
  $('logReload').addEventListener('click', loadLogs);
  ['logLevel', 'logComponent'].forEach(id => $(id).addEventListener('change', () => { logPage.offset = 0; loadLogs(); }));
  $('logQuery').addEventListener('input', () => { clearTimeout(hitsTimer); hitsTimer = setTimeout(() => { logPage.offset = 0; loadLogs(); }, 300); });


  // ---------- Benachrichtigungen ----------
  function fillNotify() {
    const n = settings?.notify || {}; const f = $('notifyForm');
    for (const el of f.elements) { if (!el.name) continue; if (el.type === 'checkbox') el.checked = !!n[el.name]; else if (n[el.name] !== undefined) el.value = n[el.name]; }
    pushFields();
  }
  function pushFields() {
    const t = $('pushType').value;
    document.querySelectorAll('.push-ntfy').forEach(e => e.hidden = t !== 'ntfy');
    document.querySelectorAll('.push-tg').forEach(e => e.hidden = t !== 'telegram');
    $('pushTest').hidden = !t;
  }
  $('pushType').addEventListener('change', pushFields);
  $('tgFind').addEventListener('click', async () => {
    const tok = document.querySelector('[name=tg_token]').value.trim();
    note($('notifyNote'), 'frage Telegram …');
    try {
      const d = await post('/api/telegram-chats', { token: tok });
      if (!d.chats.length) { note($('notifyNote'), 'Noch keine Nachricht an den Bot gefunden – schreib deinem Bot in Telegram etwas (z. B. „hallo“) und drück dann noch einmal auf „Chat-ID ermitteln“.', true); return; }
      const c = d.chats.find(x => x.type === 'private') || d.chats[0];
      $('tgChat').value = c.id;
      note($('notifyNote'), `✓ Chat-ID ${c.id}${c.name ? ' (' + c.name + ')' : ''} eingetragen – jetzt Speichern, dann „Test-Push senden“.`);
    } catch (err) { note($('notifyNote'), 'Fehler: ' + err.message, true); }
  });
  $('pushTest').addEventListener('click', async () => { note($('notifyNote'), 'sende …'); try { await post('/api/push-test'); note($('notifyNote'), '✓ Test-Push verschickt – kommt er an?'); } catch (err) { note($('notifyNote'), 'Fehler: ' + err.message + ' (vorher speichern)', true); } });
  $('notifyForm').addEventListener('submit', async e => {
    e.preventDefault(); const f = $('notifyForm'); const n = {};
    for (const el of f.elements) { if (!el.name) continue; n[el.name] = el.type === 'checkbox' ? el.checked : el.value; }
    try { const d = await post('/api/settings', { notify: n }); settings.notify = d.settings.notify; fillNotify(); note($('notifyNote'), '✓ gespeichert'); } catch (err) { note($('notifyNote'), 'Fehler: ' + err.message, true); }
  });
  $('notifyTest').addEventListener('click', async () => { note($('notifyNote'), 'sende …'); try { await post('/api/notify-test'); note($('notifyNote'), '✓ Testmail verschickt'); } catch (err) { note($('notifyNote'), 'Fehler: ' + err.message, true); } });

  // ---------- Bereiche (Tabs) ----------
  function fillStationBands() {
    const sel = $('stBand'); if (sel.options.length > 1) return;
    (bandplan || []).forEach(b => { const o = document.createElement('option'); o.value = b.name; o.textContent = b.name; sel.appendChild(o); });
  }
  function showView(name) {
    if (name === 'stations') setTimeout(() => { fillStationBands(); loadStations(); loadActivity(); loadFm(); }, 0);   // nach dem Start des Skripts
    document.querySelectorAll('.view').forEach(v => v.hidden = v.dataset.view !== name);
    document.querySelectorAll('.tabs-main button').forEach(b => b.classList.toggle('on', b.dataset.view === name));
    localStorage.setItem('view', name);
    window.scrollTo({ top: 0 });
    if (name === 'logs') loadLogs();
  }
  document.querySelectorAll('.tabs-main button').forEach(b => b.addEventListener('click', () => showView(b.dataset.view)));
  showView(localStorage.getItem('view') || 'scan');
  const liveHist = [];                 // [{f, m, name}] – name: Sendername aus RDS
  function liveRemember(f, m, name) {
    f = Math.round(f / 100) * 100;
    const old = liveHist.findIndex(x => x.f === f && x.m === m);
    if (old >= 0) { name = name || liveHist[old].name; liveHist.splice(old, 1); }
    liveHist.unshift({ f, m, name: name || '' }); liveHist.splice(8); renderLiveHist();
  }
  function renderLiveHist() {
    $('liveHistory').innerHTML = liveHist.map((x, i) => `<li><button type="button" class="linklike" data-hi="${i}">${(x.f / 1e6).toFixed(4)} MHz · ${x.m.toUpperCase()}${x.name ? ' · <b>' + esc(x.name) + '</b>' : ''}</button></li>`).join('');
    $('liveHistory').querySelectorAll('[data-hi]').forEach(el => el.addEventListener('click', () => {
      const x = liveHist[Number(el.dataset.hi)]; $('liveFreq').value = (x.f / 1e6).toFixed(4); $('liveMode').value = x.m;
      liveStart({ freq_hz: x.f, mode: x.m, squelch_db: Number($('liveSquelch').value) });
    }));
  }
  // RDS-Anzeige (UKW, „FM breit“)
  // RDS-Inhalt (Live-Kasten und Trefferkarte): einzelne Zeilen, damit die Übersetzung greift
  function rdsBody(r, withPs = true) {
    const rp = r.rtplus || {};
    const rows = [
      ['Programmart', r.pty ? r.pty_name + (r.ptyn ? ` (${r.ptyn})` : '') : null],
      ['Jetzt', rp.artist || rp.title ? [rp.artist, rp.title].filter(Boolean).join(' – ') : null],
      ['Radiotext', r.rt],
      ['Verkehrsfunk', r.tp ? (r.ta ? 'TP · Durchsage läuft' : 'TP') : 'nein'],
      ['Uhrzeit Sender', r.ct ? `${r.ct.local} (UTC${r.ct.local_offset_min >= 0 ? '+' : ''}${r.ct.local_offset_min / 60})` : null],
      ['Alternativfrequenzen', r.af && r.af.length ? r.af.map(f => f.toFixed(1)).join(' · ') + ' MHz' : null],
      ['Kennung', `PI ${r.pi}${r.ecc ? ' · ECC ' + r.ecc : ''}${r.ms ? ' · ' + r.ms : ''}`],
      ['Datendienste', r.oda && r.oda.length ? r.oda.join(', ') : null],
      ['Empfang', `${r.groups} Gruppen, ${r.blocks ? Math.round(100 * r.blocks_ok / r.blocks) : 0} % fehlerfrei`],
    ].filter(x => x[1]);
    const t = r.tmc || {};
    const tmcHead = t.encrypted ? 'verschlüsselt – nicht lesbar' : t.ltn ? `Ortstabelle ${t.ltn}${t.ltn === 1 ? ' (Deutschland)' : ''}${t.scope ? ' · ' + t.scope : ''}` : null;
    const msgs = (t.messages || []).slice(0, 20);
    return (withPs ? `<div class="rds-ps">${esc((r.ps || (r.ps_partial ? r.ps_partial : '…')).trim() || '…')}</div>` : '') +
      rows.map(([k, v]) => `<div class="kv"><span class="k">${k}</span><span class="v">${esc(String(v))}</span></div>`).join('') +
      (tmcHead || msgs.length ? `<div class="tmc"><div class="kv"><span class="k">Verkehrsmeldungen (TMC)</span><span class="v">${esc(tmcHead || '')}${msgs.length ? ` · ${msgs.length} Meldung${msgs.length > 1 ? 'en' : ''}` : ''}</span></div>
        ${msgs.length ? '<ul class="tmc-list">' + msgs.map(m => `<li class="no-i18n">🚧 ${esc((EN && m.text_en) || m.text || ('Ereignis ' + m.event + ', Ort ' + m.location))}</li>`).join('') + '</ul>' : ''}</div>` : '');
  }
  function rdsShort(r) {                     // Kurzfassung für die Kopfzeile der Trefferkarte (schon übersetzt)
    const tr = x => (window.I18N ? I18N.t(x) : x);
    const rp = r.rtplus || {};
    const ps = (r.ps || r.ps_partial || '').trim();
    return [ps ? `„${ps}“` : null, r.pi ? `PI ${r.pi}` : null, r.pty ? tr(r.pty_name) : null,
            rp.artist || rp.title ? [rp.artist, rp.title].filter(Boolean).join(' – ') : (r.rt_complete ? r.rt : null)]
      .filter(Boolean).join(' · ');
  }
  // RDS-Anzeige (UKW, „FM breit“)
  function renderRds(r) {
    const box = $('liveRds');
    if (!r || !r.groups) { box.hidden = true; return; }
    box.hidden = false;
    box.innerHTML = rdsBody(r);
    if (r.ps && r.ps.trim() && live.active) {
      const h = liveHist[0];
      if (h && Math.abs(h.f - r.freq_hz) < 1000) { if (h.name !== r.ps.trim()) { h.name = r.ps.trim(); renderLiveHist(); } }
      else liveRemember(r.freq_hz, 'wfm', r.ps.trim());       // z. B. per Suchlauf gefunden: mit Namen merken
    }
  }


  // ---------- Satelliten ----------
  let satData = null;
  async function loadSatPasses() {
    const root = $('satPasses'); if (!root) return;
    try { satData = await api('/api/sat/passes'); } catch (e) { return; }
    renderSatPasses();
    if ($('ssNext')) renderNextPass();
    if (!document.activeElement || !document.activeElement.dataset || !document.activeElement.dataset.satprio) renderSatPrio();
  }
  function renderSatPasses() {
    const d = satData; if (!d) return; const root = $('satPasses');
    $('panel-sat').hidden = !(settings && settings.active_device === 'rtlsdr');   // nur mit Antenne am Stick
    const hint = $('satHint');
    const now = Date.now() / 1000;
    const future = (d.passes || []).filter(p => p.los > now);
    if (!d.enabled) hint.textContent = 'Unter „Bänder & Betriebsarten“ das Band „Satelliten“ einschalten – dann nimmt der RTL-SDR jeden Überflug automatisch auf (Doppler-Korrektur, SSTV und APRS werden dekodiert). Die mit „SSTV ·“ markierten Bildsatelliten senden nur gelegentlich; sie werden nur aufgenommen, wenn der Stick frei ist.';
    else {
      const parts = [];
      if (d.problem) parts.push('⚠ ' + d.problem);
      if (!d.nesdr) parts.push('⚠ ' + devName('rtlsdr') + ' nicht angeschlossen – Überflüge werden nur angezeigt.');
      (d.warnings || []).forEach(w => parts.push('⚠ ' + w));
      if (!parts.length) parts.push(future.length ? 'Aufnahme startet 15 s vor dem Aufgang. Überschneiden sich Überflüge, entscheidet die Punktzahl (Priorität, Höhe, Wartezeit, Aktivität – einstellbar unter Einstellungen). Passen die Frequenzen in ein Empfangsfenster, werden die Satelliten gemeinsam aufgenommen.' : 'In den nächsten 24 h kein Überflug über 10° Höhe.');
      hint.textContent = parts.join(' ');
    }
    const t = x => new Date(x * 1000).toLocaleTimeString(I18N.locale, { hour: '2-digit', minute: '2-digit' });
    const day = x => new Date(x * 1000).toLocaleDateString(I18N.locale, { weekday: 'short' });
    const showPast = $('satShowPast').checked, showAll = $('satShowAll').checked;
    let list = (d.passes || []).filter(p => showPast || p.los > now);
    // „vergangene zeigen“: die jüngsten 10 vergangenen und die nächsten Überflüge (nicht die ältesten)
    if (!showAll) list = showPast ? [...list.filter(p => p.los <= now).slice(-10), ...list.filter(p => p.los > now).slice(0, 6)]
                                  : list.slice(0, 10);
    const MHz = id => { const s = (d.satellites || []).find(x => x.id === id); return s && s.center_hz ? (s.center_hz / 1e6).toFixed(3) : id; };
    root.innerHTML = list.map(p => {
      const live = p.aos <= now && p.los > now, past = p.los <= now;
      const ch = p.channels.map(c => MHz(c)).join(' + ');
      const dropped = (p.dropped || []).length ? `<br><small class="muted">nicht gleichzeitig: ${p.dropped.map(MHz).join(', ')}</small>` : '';
      let plan = '';
      if (p.status === 'joined' && !p.result) plan = `<span class="ok">gemeinsam</span><br><small class="muted">${esc(p.reason)}</small>`;
      else if (p.result) {
        const r = p.result, cls = { done: 'ok', running: 'run', error: 'bad', skipped: 'bad', missed: 'muted', cancelled: 'muted' }[r.state] || '';
        // Signal: höchster Kanalpegel über dem Rauschen – zeigt, ob der Satellit überhaupt zu hören war
        const sig = Object.values(r.signal || {}).filter(x => x && x.max_db != null);
        const smax = sig.length ? Math.max(...sig.map(x => x.max_db)) : null, s6 = sig.reduce((a, x) => a + (x.above6_s || 0), 0);
        const sigTxt = smax == null ? '' : smax < 6 ? `<br><small class="bad">kein Signal (SNR höchstens ${smax.toFixed(0)} dB)</small>`
          : `<br><small class="muted">SNR bis ${smax.toFixed(0)} dB, ${s6} s über 6 dB</small>`;
        // je Kanal: Einordnung der stärksten Linie und Wasserfallbild
        const multi = Object.keys(r.signal || {}).length > 1;
        const lineTxt = Object.entries(r.signal || {}).map(([cid, x]) => {
          const L = x && x.line, pre = multi ? `${esc(cid)}: ` : '';
          let t = '';
          if (L && L.kind === 'satellite') t = `<small class="ok">${pre}Satellit gehört${L.beacon ? ' (Bake)' : ''} – Linie steht fest bei ${(L.offset_hz / 1000).toFixed(1)} kHz</small>`;
          else if (L && L.kind === 'local') t = `<small class="warn">${pre}örtliche Störung bei ${L.rf_mhz.toFixed(4)} MHz (wandert gegen den Doppler)</small>`;
          else if (L) t = `<small class="muted">${pre}Linie nicht eindeutig zuzuordnen</small>`;
          const wf = x && x.waterfall ? ` <a href="/files/${encodeURIComponent(x.waterfall)}" target="_blank" rel="noopener"><small>📊 Wasserfall${multi ? ' ' + esc(cid) : ''}</small></a>` : '';
          return t || wf ? `<br>${t}${wf}` : '';
        }).join('');
        plan = `<span class="${cls}"><span>${esc(r.text)}</span>${r.state === 'done' ? ` · <span>${r.hits || 0} Treffer</span>` : ''}</span>${r.error ? `<br><small class="muted">${esc(r.error)}</small>` : ''}${sigTxt}${lineTxt}`;
      } else if (p.status === 'skipped') plan = `<span class="bad">übersprungen</span><br><small class="muted">${esc(p.reason)}</small>`;
      else if (p.status === 'partial') plan = `<span class="part">${t(p.rec_start)}–${t(p.rec_end)} ${zoneAt(new Date(p.rec_start * 1000))}</span><br><small class="muted">${esc(p.reason)}</small>`;
      else plan = p.prio <= 2 ? '<span class="muted">wenn Stick frei</span>' : '<span class="ok">voll</span>';
      const when = past ? `vor ${fmtSpan(now - p.los)}` : live ? 'läuft gerade' : 'in ' + fmtSpan(p.aos - now);
      if (p.score != null) {
        const parts = Object.entries(p.score_parts || {}).filter(([, v]) => v).map(([k, v]) => `${k} ${v > 0 ? '+' : ''}${v}`).join(', ');
        plan += `<br><small class="muted score" title="${esc(parts)}">${Math.round(p.score)} P${(p.why || []).length ? ' · ' + esc(p.why.join(' · ')) : ''}</small>`;
      }
      return `<tr class="${live ? 'sat-now' : ''}${past ? ' sat-past' : ''}">
        <td><b>${esc(p.name)}</b><br><small>${ch} MHz</small>${dropped}</td>
        <td>${day(p.aos)} ${t(p.aos)}–${t(p.los)}<span class="${zoneAt(new Date(p.aos * 1000)) === ZONE ? 'tz-m' : 'tz-diff'}"> ${zoneAt(new Date(p.aos * 1000))}</span><br><small class="muted">${when}</small></td>
        <td>${Math.round(p.max_el)}°<br><small class="muted">${p.aos_az ?? ''}°→${p.los_az ?? ''}°</small></td><td>${plan}</td></tr>`;
    }).join('') || '<tr><td colspan="4" class="muted">keine Überflüge</td></tr>';
    const foot = [];
    if (d.tle_age_h != null) foot.push(`Bahndaten vor ${d.tle_age_h < 1 ? 'weniger als 1 h' : Math.round(d.tle_age_h) + ' h'} geholt${d.tle_source ? ' (' + esc(d.tle_source) + ')' : ''}`);
    const errs = Object.entries(d.tle_errors || {}); if (errs.length) foot.push('letzter Abruf: ' + errs.map(([k, v]) => `${k}: ${v}`).join('; '));
    $('satFoot').textContent = foot.join(' · ');
  }
  function renderSatPrio() {
    const root = $('satPrio'); if (!root || !satData || !satData.satellites) return;
    const sp = (settings && settings.sat_prio) || {}, stats = satData.stats || {};
    const ago = t => { if (!t) return '–'; const d = (Date.now() / 1000 - t) / 86400; return d < 1 ? 'heute' : d < 2 ? 'gestern' : `vor ${Math.floor(d)} Tagen`; };
    root.innerHTML = satData.satellites.map(x => {
      const st = stats[String(x.norad)] || {};
      const cur = sp[x.id] ?? '';
      const opts = [`<option value="">Standard (${x.prio})</option>`, '<option value="0">0 – nie</option>']
        .concat([1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map(n => `<option value="${n}">${n}</option>`), ['<option value="pin">immer zuerst</option>']).join('');
      return `<tr><td>${esc(x.label)}</td><td><select data-satprio="${x.id}">${opts}</select></td>
        <td><small>${ago(st.last)}${st.passes_14d ? ` · ${st.passes_14d}× aufgenommen, ${st.hits_14d} Treffer` : ''}</small></td></tr>`;
    }).join('');
    root.querySelectorAll('[data-satprio]').forEach(sel => {
      sel.value = String(sp[sel.dataset.satprio] ?? '');
      sel.addEventListener('change', async () => {
        try { const d = await post('/api/settings', { sat_prio: { [sel.dataset.satprio]: sel.value } }); settings.sat_prio = d.settings.sat_prio; note($('satPrioNote'), '✓ gespeichert – der Plan wird neu berechnet'); setTimeout(loadSatPasses, 800); }
        catch (e) { note($('satPrioNote'), 'Fehler: ' + e.message, true); }
      });
    });
  }
  ['satShowPast', 'satShowAll'].forEach(id => { $(id).checked = localStorage.getItem(id) === '1'; $(id).addEventListener('change', e => { localStorage.setItem(id, e.target.checked ? '1' : '0'); renderSatPasses(); }); });
  function fmtSpan(s) { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60); return h ? `${h} h ${m} min` : `${m} min`; }

  // ---------- Kennzahlen auf der Scan-Seite ----------
  async function loadScanSummary() {
    let d; try { d = await api('/api/scan-summary'); } catch (e) { return; }
    $('ssHits').textContent = d.hits;
    $('ssRuns').textContent = d.runs;
    $('ssRunsLbl').innerHTML = d.runs_failed ? `Scans 24 h · <span class="bad">${d.runs_failed} Fehler</span>` : 'Scans 24 h';
    $('ssBusy').textContent = d.scan_minutes >= 90 ? `${(d.scan_minutes / 60).toFixed(1).replace('.', ',')} h` : `${d.scan_minutes} min`;
    $('ssSat').textContent = d.sat_passes;
    $('ssSatLbl').textContent = d.sat_hits ? `Überflüge 24 h · ${d.sat_hits} Treffer` : 'Überflüge 24 h';
    $('ssSstv').textContent = d.sstv;
    renderNextPass();
  }
  function renderNextPass() {
    const now = Date.now() / 1000;
    const p = (satData?.passes || []).find(x => x.los > now && x.status !== 'skipped' && x.status !== 'joined');
    const card = document.querySelector('#scanStats .stat-next');
    card.hidden = !(satData && satData.enabled);
    if (!p) { $('ssNext').textContent = '–'; $('ssNextLbl').textContent = 'kein Überflug geplant'; return; }
    const live = p.aos <= now;
    const dt = Math.max(0, Math.round((p.aos - now) / 60));
    $('ssNext').textContent = live ? 'läuft' : dt < 60 ? `in ${dt} min` : `in ${Math.floor(dt / 60)}:${String(dt % 60).padStart(2, '0')} h`;
    $('ssNextLbl').textContent = `Überflug ${p.name.split(' (')[0]} · ${Math.round(p.max_el)}°`;
  }
  document.querySelectorAll('#scanStats [data-go]').forEach(b => b.addEventListener('click', () => {
    const go = b.dataset.go;
    if (go === 'sat') { $('panel-sat').scrollIntoView({ behavior: 'smooth', block: 'start' }); return; }
    if (go === 'sstv') { filter.mode = 'sstv'; localStorage.setItem('f.mode', 'sstv'); $('filterMode').value = 'sstv'; }
    showView('hits'); loadHits();
  }));

  // ---------- Zeitpläne ----------
  const DAYS = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'];
  const scSel = { days: new Set([0, 1, 2, 3, 4, 5, 6]), bands: new Set() };
  function renderSchedules() {
    if (!settings) return;
    const act = settings.schedule_active;
    $('schedActiveNote').hidden = !act;
    if (act) $('schedActiveNote').textContent = `Zeitplan „${act.name}“ aktiv bis ${act.to} – gescannt wird gerade: ${act.bands.join(', ')}`;
    $('schedHint').dataset.pi = `${settings.pi_time} ${settings.pi_tz}`;
    const list = settings.schedules || [];
    $('schedList').innerHTML = list.length ? `<table class="table watch-table"><tbody>${list.map((r, i) => {
      const days = r.days.length === 7 ? 'täglich' : r.days.map(d => DAYS[d]).join(' ');
      const on = act && act.name === r.name;
      return `<tr class="${on ? 'sched-now' : ''}"><td><label class="check"><input type="checkbox" data-si="${i}" ${r.enabled ? 'checked' : ''}> <b>${esc(r.name)}</b>${on ? ' <span class="badge badge-new">aktiv</span>' : ''}</label>
        <small class="muted">${esc(days)} · ${r.from}–${r.to} · ${esc(r.bands.join(', '))}</small></td>
        <td><button type="button" class="btn btn-small btn-danger" data-sdel="${i}">✕</button></td></tr>`; }).join('')}</tbody></table>
        <p class="hint">Uhrzeiten in der Ortszeit des Pi (jetzt ${esc(settings.pi_time)} ${esc(settings.pi_tz)}).</p>` : '<p class="muted">Noch kein Zeitplan.</p>';
    $('schedList').querySelectorAll('[data-si]').forEach(cb => cb.addEventListener('change', () => { const l = [...settings.schedules]; l[cb.dataset.si] = { ...l[cb.dataset.si], enabled: cb.checked }; saveSchedules(l); }));
    $('schedList').querySelectorAll('[data-sdel]').forEach(b => b.addEventListener('click', () => { const l = [...settings.schedules]; l.splice(Number(b.dataset.sdel), 1); saveSchedules(l); }));
    $('scDays').innerHTML = ''; DAYS.forEach((d, i) => { const c = chip(d, scSel.days.has(i)); c.addEventListener('click', () => { scSel.days.has(i) ? scSel.days.delete(i) : scSel.days.add(i); renderSchedules(); }); $('scDays').appendChild(c); });
    $('scBands').innerHTML = '';
    bandplan.filter(b => b.device === settings.active_device && b.legal !== 'restricted').forEach(b => {
      const c = chip(b.name, scSel.bands.has(b.name)); c.addEventListener('click', () => { scSel.bands.has(b.name) ? scSel.bands.delete(b.name) : scSel.bands.add(b.name); renderSchedules(); }); $('scBands').appendChild(c);
    });
  }
  async function saveSchedules(list, okText) {
    try { const d = await post('/api/settings', { schedules: list }); settings = Object.assign(settings, d.settings); renderSchedules(); loadEstimate(); note($('schedNote'), okText || '✓ gespeichert'); return true; }
    catch (e) { note($('schedNote'), 'Fehler: ' + e.message, true); return false; }
  }
  setInterval(async () => {                               // aktiver Zeitplan wechselt mit der Uhr
    try { const d = await api('/api/settings'); Object.assign(settings, { schedule_active: d.schedule_active, pi_time: d.pi_time, pi_tz: d.pi_tz }); renderSchedules(); } catch (e) { }
  }, 60000);
  $('scAdd').addEventListener('click', async () => {
    const sc = { name: $('scName').value.trim(), from: $('scFrom').value, to: $('scTo').value, days: [...scSel.days], bands: [...scSel.bands], enabled: true };
    if (await saveSchedules([...(settings.schedules || []), sc], '✓ Zeitplan gespeichert')) { $('scName').value = ''; scSel.bands.clear(); renderSchedules(); }
  });

  // ---------- Gehörte Stationen ----------
  const stPage = { offset: 0, per: 100 };
  function stParams() { return new URLSearchParams({ q: $('stQuery').value.trim(), band: $('stBand').value, days: $('stDays').value }); }
  async function loadStations() {
    let d; try { d = await api(`/api/stations?${stParams()}&limit=${stPage.per}&offset=${stPage.offset}`); } catch (e) { return; }
    const dir = b => b == null ? '' : ['N', 'NO', 'O', 'SO', 'S', 'SW', 'W', 'NW'][Math.round(b / 45) % 8];
    $('stTable').innerHTML = d.stations.map(r => `<tr>
        <td><a href="https://www.qrz.com/db/${encodeURIComponent(r.call.split('-')[0])}" target="_blank" rel="noopener"><b>${esc(r.call)}</b></a></td>
        <td>${esc(r.grid || '–')}</td>
        <td>${r.distance_km != null ? `${r.distance_km} km <small class="muted">${r.bearing}° ${dir(r.bearing)}</small>` : '–'}</td>
        <td>${esc((r.bands || '').split(',').join(', '))}</td><td>${esc((r.modes || '').split(',').join(', '))}</td>
        <td>${fmtTime(r.last)}</td><td>${r.n}</td><td>${r.best_snr != null ? Math.round(r.best_snr) + ' dB' : '–'}</td></tr>`).join('')
      || '<tr><td colspan="8" class="muted">Noch keine Stationen gehört – Rufzeichen kommen aus FT8/FT4, APRS, CW und RTTY.</td></tr>';
    const a = d.total ? stPage.offset + 1 : 0, b = Math.min(d.total, stPage.offset + stPage.per);
    $('stPos').textContent = `${a}–${b} von ${d.total}`;
    $('stPrev').disabled = stPage.offset === 0; $('stNext').disabled = b >= d.total;
    $('stAdif').href = `/api/stations.adi?${stParams()}`;
    stLast = d;
    if ($('mapBox').open) drawMap();
  }
  // ---------- Karte (Leaflet + OpenStreetMap, wird erst beim Aufklappen geladen) ----------
  let stLast = null, map = null, mapLayer = null, leafletLoading = null;
  function loadLeaflet() {
    if (window.L) return Promise.resolve();
    if (leafletLoading) return leafletLoading;
    leafletLoading = new Promise((res, rej) => {
      const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css'; document.head.appendChild(css);
      const js = document.createElement('script'); js.src = 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js'; js.onload = res; js.onerror = rej; document.head.appendChild(js);
    });
    return leafletLoading;
  }
  async function drawMap() {
    try { await loadLeaflet(); } catch (e) { $('mapNote').textContent = 'Karte nicht verfügbar – der Browser erreicht cdnjs.cloudflare.com bzw. OpenStreetMap nicht.'; return; }
    if (!map) {
      map = L.map('stMap', { worldCopyJump: true }).setView([50, 10], 3);
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 12, attribution: '© OpenStreetMap' }).addTo(map);
      mapLayer = L.layerGroup().addTo(map);
    }
    mapLayer.clearLayers();
    const d = stLast || { stations: [] }; const pts = [];
    const color = m => /FT8|FT4/.test(m || '') ? '#38bdf8' : /APRS/.test(m || '') ? '#f5b942' : '#2dd4bf';
    if (d.home) { L.circleMarker(d.home, { radius: 7, color: '#ffffff', fillColor: '#ef4444', fillOpacity: 1, weight: 2 }).bindTooltip('QTH').addTo(mapLayer); pts.push(d.home); }
    for (const r of d.stations) {
      if (r.lat == null) continue;
      const p = [r.lat, r.lon]; pts.push(p);
      if (d.home) L.polyline([d.home, p], { color: color(r.modes), weight: 1, opacity: .35 }).addTo(mapLayer);
      L.circleMarker(p, { radius: 5, color: color(r.modes), fillOpacity: .8, weight: 1 })
        .bindPopup(`<b>${esc(r.call)}</b> ${esc(r.grid || '')}<br>${r.distance_km != null ? r.distance_km + ' km, ' + r.bearing + '°<br>' : ''}${esc((r.modes || '').replace(/,/g, ', '))} · ${esc((r.bands || '').replace(/,/g, ', '))}<br>${fmtTime(r.last)}`).addTo(mapLayer);
    }
    setTimeout(() => map.invalidateSize(), 50);
    if (pts.length > 1) map.fitBounds(pts, { padding: [20, 20], maxZoom: 7 });
  }
  $('mapBox').addEventListener('toggle', () => { if ($('mapBox').open) drawMap(); });

  // ---------- UKW-Senderliste (RDS) ----------
  async function loadFm() {
    let d; try { d = await api('/api/fmstations'); } catch (e) { return; }
    $('fmTable').innerHTML = d.stations.map(r => `<tr>
        <td class="mono-num">${(r.freq_khz / 1000).toFixed(1)}</td><td><b class="rds-name">${esc(r.ps || '–')}</b></td><td>${esc(r.pi || '')}${r.ecc ? ` <small class="muted">${esc(r.ecc)}</small>` : ''}</td>
        <td>${esc(r.pty || '')}</td><td class="wrap">${esc(r.now_playing || r.rt || '')}</td><td>${r.last_snr != null ? Math.round(r.last_snr) + ' dB' : '–'}${r.best_snr != null && r.best_snr > (r.last_snr ?? -99) + 0.5 ? ` <small class="muted">(max ${Math.round(r.best_snr)})</small>` : ''}</td><td>${fmtTime(r.last)}</td><td>${r.n}</td></tr>`).join('')
      || '<tr><td colspan="8" class="muted">Noch keine UKW-Sender – Band „Rundfunk“ unter Scan einschalten.</td></tr>';
  }

  // ---------- Aktivität ----------
  async function loadActivity() {
    let d; try { d = await api('/api/activity?days=' + $('actDays').value); } catch (e) { return; }
    const H = [...Array(24).keys()];
    const cell = v => { if (!v || !v.tunes) return '<td class="h0"></td>';
      const rate = v.hits / v.tunes; return `<td style="--a:${Math.min(1, rate * 4).toFixed(2)}" title="${v.hits} Treffer / ${v.tunes} Abstimmungen">${v.hits ? v.hits : ''}</td>`; };
    const head = `<tr><th></th>${H.map(h => `<th>${String(h).padStart(2, '0')}</th>`).join('')}</tr>`;
    $('actBands').innerHTML = d.bands.length ? head + d.bands.map(b => `<tr><th>${esc(b.band)}</th>${H.map(h => cell(b.hours[h])).join('')}</tr>`).join('')
      : '<tr><td class="muted">Noch keine Daten.</td></tr>';
    const WD = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'];
    $('actWeek').innerHTML = head + WD.map((w, i) => `<tr><th>${w}</th>${H.map(h => cell((d.week[i] || {})[h])).join('')}</tr>`).join('');
  }
  $('actDays').addEventListener('change', loadActivity);
  let stTimer = null;
  $('stQuery').addEventListener('input', () => { clearTimeout(stTimer); stTimer = setTimeout(() => { stPage.offset = 0; loadStations(); }, 300); });
  ['stBand', 'stDays'].forEach(id => $(id).addEventListener('change', () => { stPage.offset = 0; loadStations(); }));
  $('stPrev').addEventListener('click', () => { stPage.offset = Math.max(0, stPage.offset - stPage.per); loadStations(); });
  $('stNext').addEventListener('click', () => { stPage.offset += stPage.per; loadStations(); });

  // ---------- Beobachtungsliste ----------
  const W_TYPES = { freq: 'Frequenz', call: 'Rufzeichen', dx: 'Entfernung ab', event: 'Ereignis', traffic: 'Verkehr' };
  function renderWatch() {
    const list = (settings && settings.watchlist) || [], ev = (settings && settings.watch_events) || {};
    if (!$('wEvent').options.length) $('wEvent').innerHTML = Object.entries(ev).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('');
    $('watchList').innerHTML = list.length ? `<table class="table watch-table"><tbody>${list.map((r, i) => {
      const what = r.type === 'event' ? (ev[r.value] || r.value) : r.type === 'freq' ? `${r.value} MHz ±${r.tolerance_khz} kHz` : r.type === 'dx' ? `≥ ${r.value} km` : r.value;
      return `<tr><td><label class="check"><input type="checkbox" data-wi="${i}" ${r.enabled ? 'checked' : ''}> <b>${esc(r.label || what)}</b></label>
        <small class="muted">${esc(W_TYPES[r.type] || r.type)}: ${esc(what)}${r.mail ? ' · Mail/Push' : ''} · Ruhe ${r.quiet_min} min</small></td>
        <td><button type="button" class="btn btn-small btn-danger" data-wdel="${i}">✕</button></td></tr>`; }).join('')}</tbody></table>`
      : '<p class="muted">Noch keine Beobachtungen.</p>';
    $('watchList').querySelectorAll('[data-wi]').forEach(cb => cb.addEventListener('change', () => { const l = [...settings.watchlist]; l[cb.dataset.wi] = { ...l[cb.dataset.wi], enabled: cb.checked }; saveWatch(l); }));
    $('watchList').querySelectorAll('[data-wdel]').forEach(b => b.addEventListener('click', () => { const l = [...settings.watchlist]; l.splice(Number(b.dataset.wdel), 1); saveWatch(l); }));
  }
  async function saveWatch(list, okText) {
    try { const d = await post('/api/settings', { watchlist: list }); settings.watchlist = d.settings.watchlist; renderWatch(); note($('watchNote'), okText || '✓ gespeichert'); return true; }
    catch (e) { note($('watchNote'), 'Fehler: ' + e.message, true); return false; }
  }
  function wTypeChanged() {
    const t = $('wType').value;
    $('wEvWrap').hidden = t !== 'event'; $('wValWrap').hidden = t === 'event'; $('wTolWrap').hidden = t !== 'freq';
    $('wValue').placeholder = { freq: '439.400', call: 'DL1ABC oder DL*', dx: '2000', traffic: 'A9 oder Nürnberg' }[t] || '';
  }
  $('wType').addEventListener('change', wTypeChanged); wTypeChanged();
  $('wAdd').addEventListener('click', async () => {
    const t = $('wType').value;
    const rule = { type: t, value: t === 'event' ? $('wEvent').value : $('wValue').value.trim(), label: $('wLabel').value.trim(),
                   tolerance_khz: Number($('wTol').value) || 5, quiet_min: Number($('wQuiet').value) || 0, mail: $('wMail').checked, enabled: true };
    if (await saveWatch([...(settings.watchlist || []), rule], '✓ Beobachtung hinzugefügt')) { $('wValue').value = ''; $('wLabel').value = ''; }
  });

  // ---------- System ----------
  async function loadSystem() {
    let s; try { s = await api('/api/system'); } catch (e) { return; }
    const tiles = [], warn = [];
    const tile = (val, label, cls = '') => tiles.push(`<div class="stat ${cls}"><b>${val}</b><span>${label}</span></div>`);
    const d = s.disk; const dcls = d.used_pct >= 95 ? 'bad' : d.used_pct >= 85 ? 'warn' : '';
    tile(`${d.free_gb} GB`, `frei von ${d.total_gb} GB (${d.used_pct} % belegt)`, dcls);
    if (dcls) warn.push(d.used_pct >= 95 ? 'Speicherkarte fast voll – keine neuen Scans und Überflüge, bis wieder Platz ist.' : 'Speicherkarte wird voll – alte Treffer aufräumen oder automatisch löschen lassen.');
    tile(`${s.recordings.gb} GB`, `Aufnahmen (${s.recordings.files} Dateien)`);
    if (s.sat_raw.files) { tile(`${s.sat_raw.files}`, `abgebrochene Überflüge (${s.sat_raw.mb} MB)`, 'warn'); warn.push('Abgebrochene Überflüge liegen noch als Rohdaten vor – auf dem Pi: python3 bin/sat_recover.py'); }
    if (s.memory) tile(`${s.memory.available_mb} MB`, `Arbeitsspeicher frei von ${s.memory.total_mb} MB`, s.memory.available_mb < 200 ? 'warn' : '');
    if (s.cpu_temp_c != null) tile(`${s.cpu_temp_c} °C`, 'CPU-Temperatur', s.cpu_temp_c >= 80 ? 'bad' : s.cpu_temp_c >= 70 ? 'warn' : '');
    if (s.throttled) {
      const t = s.throttled; const now = t.now, ever = t.since_boot;
      tile(now ? 'gedrosselt' : ever ? 'war gedrosselt' : 'nein', 'Drosselung / Unterspannung', now ? 'bad' : ever ? 'warn' : '');
      if (t.undervoltage) warn.push('Unterspannung erkannt – Netzteil prüfen (der RTL-Stick zieht zusätzlich Strom).');
    }
    if (s.time_sync != null) { tile(s.time_sync ? 'ja' : 'nein', 'Uhr synchron (NTP)', s.time_sync ? '' : 'bad'); if (!s.time_sync) warn.push('Die Uhr des Pi ist nicht synchron – Satellitenzeiten und Doppler werden ungenau.'); }
    if (s.load) tile(s.load[0].toFixed(2), 'Last (1 min)');
    if (s.uptime_h != null) tile(s.uptime_h >= 48 ? `${Math.round(s.uptime_h / 24)} Tage` : `${s.uptime_h} h`, 'läuft seit');
    const svc = s.services || {};
    for (const [u, st] of Object.entries(svc)) { if (u === 'pmsdr-control' && st !== 'active' && st !== 'activating') continue; tile(st === 'active' ? 'läuft' : st, u, st === 'active' ? '' : 'warn'); }
    const v = s.version || {};
    tile(`${v.version}${v.commit ? ' <small class="muted">' + v.commit + '</small>' : ''}`, v.newer ? `Version – neu: ${v.latest}` : 'Version', v.newer ? 'warn' : '');
    if (v.newer) warn.push(`Neue Version ${v.latest} verfügbar – auf dem Pi: bin/update.sh`);
    $('sysGrid').innerHTML = tiles.join('');
    $('sysNote').innerHTML = warn.map(w => `<span>${esc(w)}</span>`).join(' ');
    $('sysNote').classList.toggle('warn-text', warn.length > 0);
  }
  loadSystem(); setInterval(() => { if (document.visibilityState === 'visible' && !$('panel-system').closest('[hidden]')) loadSystem(); }, 60000);
  $('cleanupAuto').addEventListener('change', async e => {
    try { const d = await post('/api/settings', { cleanup_auto_days: Number(e.target.value) }); settings.cleanup_auto_days = d.settings.cleanup_auto_days;
          note($('cleanupNote'), Number(e.target.value) ? '✓ gespeichert – wird alle 15 min geprüft, geschützte Treffer (★) bleiben' : '✓ automatisches Löschen aus'); }
    catch (x) { note($('cleanupNote'), 'Fehler: ' + x.message, true); }
  });

  // ---------- Zugangsschutz ----------
  async function loadAuth() {
    let a; try { a = await api('/api/auth'); } catch (e) { return; }
    let txt = a.enabled ? 'Zugangsschutz aktiv' : 'Kein Passwort gesetzt – das Dashboard ist für jeden im Netz offen.';
    if (a.enabled && a.trusted_here) txt += ' · dieses Gerät ist in einem freigegebenen Netz';
    else if (a.enabled && a.logged_in) txt += ' · angemeldet';
    $('authState').textContent = txt;
    $('authState').classList.toggle('warn-text', !a.enabled);
    $('authCurWrap').hidden = !a.enabled;
    $('authOff').hidden = !a.enabled;
    $('authLogout').hidden = !(a.enabled && a.logged_in);
    $('authSave').textContent = a.enabled ? 'Passwort ändern' : 'Passwort setzen';
    $('authTrusted').value = (a.trusted_networks || []).join(', ');
  }
  $('authSave').addEventListener('click', async () => {
    if ($('authNew').value !== $('authNew2').value) { note($('authNote'), 'Die neuen Passwörter stimmen nicht überein', true); return; }
    try {
      await post('/api/auth/password', { current: $('authCur').value, new: $('authNew').value });
      ['authCur', 'authNew', 'authNew2'].forEach(id => $(id).value = '');
      note($('authNote'), '✓ Passwort gespeichert – andere Geräte müssen sich neu anmelden'); loadAuth();
    } catch (e) { note($('authNote'), 'Fehler: ' + e.message, true); }
  });
  $('authOff').addEventListener('click', async () => {
    if (!confirm('Zugangsschutz wirklich ausschalten?')) return;
    try { await post('/api/auth/off', { current: $('authCur').value }); $('authCur').value = ''; note($('authNote'), '✓ Zugangsschutz ausgeschaltet'); loadAuth(); }
    catch (e) { note($('authNote'), 'Fehler: ' + e.message, true); }
  });
  $('authLogout').addEventListener('click', async () => { try { await post('/api/logout'); } catch (e) { } location.href = '/login'; });
  $('authTrustedSave').addEventListener('click', async () => {
    try { await post('/api/auth/trusted', { networks: $('authTrusted').value }); note($('authNote'), '✓ Netze gespeichert'); loadAuth(); }
    catch (e) { note($('authNote'), 'Fehler: ' + e.message, true); }
  });
  loadAuth();

  // ---------- Sprache ----------
  $('uiLang').value = I18N.lang;
  $('uiLang').addEventListener('change', e => I18N.set(e.target.value));

  // ---------- Start ----------
  (async () => {
    try { await loadBandplan(); await loadSettings(); } catch (e) { toast('Einstellungen nicht ladbar: ' + e.message, 6000); }
    await pollStatus();
    await refreshData();

    setInterval(pollLive, 1000);
    setInterval(pollStatus, 2000);
    loadSatPasses(); setInterval(loadSatPasses, 30000);
    loadScanSummary(); setInterval(() => { if (document.visibilityState === 'visible') loadScanSummary(); }, 30000);
    setInterval(renderNextPass, 15000);
    setInterval(() => { if (document.visibilityState === 'visible') { loadStats(); loadHits(); } }, 20000);
    setInterval(() => { if (document.visibilityState === 'visible') { loadRuns(); loadLog(); } }, 60000);
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') { pollStatus(); refreshData(); } });
  })();
})();
