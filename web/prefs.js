/* Hesperos DX – Einstellungen der Oberfläche auf dem Pi, je Profil (PC / Handy).
 * Läuft als erstes Skript im <head>: wählt das Profil, legt dessen Werte in den Browser-Speicher (bevor Design,
 * Sprache und Anordnung gelesen werden) und schickt jede Änderung gesammelt an den Pi (POST /api/ui-prefs).
 * Profil: fest gewählt (Einstellungen → Station) oder automatisch – Handy, wenn der Bildschirm per Finger bedient
 * wird und seine kürzere Seite höchstens 600 Pixel (CSS) hat; so bleibt ein Handy auch quer ein Handy, ein
 * Tablet zählt als PC. Ist das Profil auf dem Pi noch leer, übernimmt es die Werte dieses Browsers.
 */
(function () {
  'use strict';
  var EXACT = ['layout.v1', 'theme', 'lang', 'view', 'dnr', 'practice', 'runs.per', 'histHours', 'mapMode', 'mapH',
               'satCollapsed', 'satShowPast', 'satShowAll', 'liveVol', 'liveGain'];
  function synced(k) { k = String(k); return EXACT.indexOf(k) >= 0 || k.indexOf('f.') === 0; }
  var LS = null;
  try { LS = window.localStorage; LS.getItem('x'); } catch (e) { LS = null; }
  var setOrig = Storage.prototype.setItem, delOrig = Storage.prototype.removeItem;

  function autoProfile() {
    try {
      var coarse = window.matchMedia && matchMedia('(pointer: coarse)').matches;
      var shortSide = Math.min(screen.width || 9999, screen.height || 9999);
      return coarse && shortSide <= 600 ? 'handy' : 'pc';
    } catch (e) { return 'pc'; }
  }
  var choice = '';
  try { choice = LS ? (LS.getItem('prefs.profile') || '') : ''; } catch (e) { }
  if (choice !== 'pc' && choice !== 'handy') choice = '';
  var profile = choice || autoProfile();

  var all = window.HSP_PREFS && window.HSP_PREFS.profiles ? window.HSP_PREFS.profiles : null;
  var mine = all ? (all[profile] || {}) : null;
  var pending = {}, timer = null;

  function localSynced() {
    var out = {};
    if (!LS) return out;
    for (var i = 0; i < LS.length; i++) { var k = LS.key(i); if (synced(k)) out[k] = LS.getItem(k); }
    return out;
  }
  function send(body, keepalive) {
    try {
      return fetch('/api/ui-prefs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
                                      keepalive: !!keepalive, body: JSON.stringify(body) });
    } catch (e) { return Promise.resolve(null); }
  }
  function flush(keepalive) {
    if (timer) { clearTimeout(timer); timer = null; }
    var keys = Object.keys(pending); if (!keys.length) return;
    var set = pending; pending = {};
    send({ profile: profile, set: set }, keepalive);
  }
  function queue(k, v) {
    pending[k] = v;
    if (mine) { if (v === null) delete mine[k]; else mine[k] = String(v); }
    if (!timer) timer = setTimeout(function () { flush(false); }, 2000);
  }

  if (LS && mine) {
    if (Object.keys(mine).length) {
      // Profil vom Pi gilt: Werte übernehmen, was es dort nicht gibt, aus diesem Browser entfernen
      var loc = localSynced();
      Object.keys(loc).forEach(function (k) { if (!(k in mine)) delOrig.call(LS, k); });
      Object.keys(mine).forEach(function (k) { if (synced(k)) setOrig.call(LS, k, mine[k]); });
    } else {
      mine = localSynced();                                   // Profil noch leer: diesen Browser übernehmen
      if (Object.keys(mine).length) send({ profile: profile, replace: mine });
    }
  }
  if (LS) {
    Storage.prototype.setItem = function (k, v) {
      setOrig.call(this, k, v);
      if (this === LS && synced(k)) queue(String(k), String(v));
    };
    Storage.prototype.removeItem = function (k) {
      delOrig.call(this, k);
      if (this === LS && synced(k)) queue(String(k), null);
    };
  }
  // vor dem Neuladen oder Schließen: noch Ausstehendes mitschicken (keepalive überlebt das Neuladen)
  window.addEventListener('pagehide', function () { flush(true); });
  document.addEventListener('visibilitychange', function () { if (document.visibilityState === 'hidden') flush(true); });

  window.HSPPrefs = {
    profile: profile, choice: choice, auto: autoProfile(), onPi: !!all,
    has: function (k) { return !!(mine && Object.prototype.hasOwnProperty.call(mine, k)); },
    // Profil fest wählen ('' = automatisch) – danach neu laden
    choose: function (p) {
      flush(true);
      try { if (p === 'pc' || p === 'handy') setOrig.call(LS, 'prefs.profile', p); else delOrig.call(LS, 'prefs.profile'); } catch (e) { }
      location.reload();
    },
    copyFrom: function (other) {
      flush(true);
      return send({ profile: profile, copy_from: other }).then(function () { location.reload(); });
    },
    reset: function () {
      pending = {};
      if (LS) Object.keys(localSynced()).forEach(function (k) { if (k !== 'lang') delOrig.call(LS, k); });
      var keep = {}; if (mine && mine.lang) keep.lang = mine.lang;          // Sprache bleibt
      return send({ profile: profile, replace: keep }).then(function () { location.reload(); });
    }
  };
})();
