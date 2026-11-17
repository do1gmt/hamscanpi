/* Hesperos DX – Karten verschieben und einklappen, gespeichert im Ansicht-Profil auf dem Pi (PC und Handy
 * unabhängig, web/prefs.js gleicht den Browser-Speicher mit dem Pi ab).
 * Griff ⠿ oben rechts an jeder Karte: ziehen (Maus oder Finger), oder antippen und mit ↑/↓ verschieben.
 * ▾ klappt eine Karte ein. Anordnung zurücksetzen: Einstellungen → Station → Anordnung.
 */
(function () {
  'use strict';
  var KEY = 'layout.v1';
  var EN = (document.documentElement.lang || '').indexOf('en') === 0;
  var T = function (de, en) { return (window.I18N && I18N.lang === 'en') || EN ? en : de; };
  function load() { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) { return {}; } }
  function save(st) { try { localStorage.setItem(KEY, JSON.stringify(st)); } catch (e) { /* Speicher gesperrt: gilt bis zum Neuladen */ } }
  var state = load();
  state.order = state.order || {};
  state.folded = state.folded || [];

  // Bereiche, in denen Karten liegen: die Spalten eines Reiters, sonst der Reiter selbst
  function containers() {
    var out = [];
    document.querySelectorAll('section.view').forEach(function (v) {
      var cols = v.querySelectorAll(':scope > .two-col > .col');
      cols.forEach(function (c, i) { c.dataset.lkey = v.dataset.view + '-col' + i; c.dataset.lview = v.dataset.view; out.push(c); });
      v.dataset.lkey = v.dataset.view + '-root'; v.dataset.lview = v.dataset.view; out.push(v);
    });
    return out;
  }
  function panelsOf(c) { return Array.prototype.filter.call(c.children, function (x) { return x.classList && x.classList.contains('panel') && x.id; }); }
  function viewOf(el) { var v = el.closest('section.view'); return v ? v.dataset.view : ''; }

  // gespeicherte Anordnung herstellen (unbekannte Kennungen übergehen, neue Karten bleiben, wo sie sind)
  function restore() {
    var conts = containers(), byKey = {};
    conts.forEach(function (c) { byKey[c.dataset.lkey] = c; });
    Object.keys(state.order).forEach(function (k) {
      var c = byKey[k]; if (!c) return;
      var root = k.slice(-5) === '-root', want = [];
      state.order[k].forEach(function (id) {
        var p = document.getElementById(id);
        if (!p || !p.classList.contains('panel') || viewOf(p) !== c.dataset.lview) return;
        if (root ? p.parentElement !== c : !p.parentElement.classList.contains('col')) return;   // nicht zwischen Ebenen
        if (want.indexOf(p) < 0) want.push(p);
      });
      // Karten, die in der gespeicherten Anordnung fehlen (neu nach einem Update), kommen dahinter in ihrer Reihenfolge
      var cur = panelsOf(c);
      cur.forEach(function (p) { if (want.indexOf(p) < 0) want.push(p); });
      // nur die Plätze der Karten neu belegen – andere Teile des Reiters (Spalten, Sprungleiste) bleiben, wo sie sind.
      // Vorher hängte appendChild die Karten ans Ende: „Hinweise“ rutschte unter die Spalten und ließ sich nicht zurückziehen.
      var marks = cur.map(function (p) { var m = document.createComment('slot'); c.insertBefore(m, p); return m; });
      var last = null;
      marks.forEach(function (m, i) { var p = want[i]; if (p) { c.insertBefore(p, m); last = p; } m.remove(); });
      // Karten, die aus der anderen Spalte hierher gezogen wurden, haben hier noch keinen Platz: dahinter einreihen.
      // Vorher fielen sie weg und landeten nach dem Neuladen wieder in ihrer alten Spalte.
      for (var i = marks.length; i < want.length; i++) {
        if (last) last.after(want[i]); else c.appendChild(want[i]);
        last = want[i];
      }
    });
    state.folded.forEach(function (id) { var p = document.getElementById(id); if (p) p.classList.add('folded'); });
  }
  function remember() {
    containers().forEach(function (c) { state.order[c.dataset.lkey] = panelsOf(c).map(function (p) { return p.id; }); });
    save(state);
    document.dispatchEvent(new CustomEvent('layoutchange'));
  }

  // Griffe einsetzen
  function tools(p) {
    if (p.querySelector(':scope > .panel-tools')) return;
    var box = document.createElement('div'); box.className = 'panel-tools';
    var h2 = p.querySelector(':scope > h2');
    if (h2 && !h2.querySelector('.collapse-btn') && p.tagName !== 'DETAILS') {
      var f = document.createElement('button'); f.type = 'button'; f.className = 'pt-fold';
      f.title = T('Karte ein-/ausklappen', 'Fold/unfold card'); f.setAttribute('aria-label', f.title);
      f.textContent = p.classList.contains('folded') ? '▸' : '▾';
      f.addEventListener('click', function () {
        var on = p.classList.toggle('folded'); f.textContent = on ? '▸' : '▾';
        state.folded = state.folded.filter(function (x) { return x !== p.id; }); if (on) state.folded.push(p.id);
        save(state); document.dispatchEvent(new CustomEvent('layoutchange'));
      });
      box.appendChild(f);
    }
    var g = document.createElement('button'); g.type = 'button'; g.className = 'pt-grip';
    g.title = T('Karte verschieben: ziehen – oder antippen und mit ↑/↓ bewegen', 'Move card: drag – or focus and use ↑/↓');
    g.setAttribute('aria-label', g.title); g.textContent = '⠿';
    g.addEventListener('pointerdown', function (e) { startDrag(e, p, g); });
    g.addEventListener('keydown', function (e) {
      if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
      e.preventDefault();
      var sib = e.key === 'ArrowUp' ? prevPanel(p) : nextPanel(p);
      if (sib) { if (e.key === 'ArrowUp') sib.before(p); else sib.after(p); g.focus(); p.scrollIntoView({ block: 'nearest' }); remember(); }
    });
    box.appendChild(g);
    p.classList.add('has-tools');
    p.prepend(box);
  }
  function prevPanel(p) { var x = p.previousElementSibling; while (x && !(x.classList.contains('panel') && x.id)) x = x.previousElementSibling; return x; }
  function nextPanel(p) { var x = p.nextElementSibling; while (x && !(x.classList.contains('panel') && x.id)) x = x.nextElementSibling; return x; }

  // Ziehen: die Karte wandert im Dokument mit (kein schwebendes Abbild – geht auch am Handy flüssig)
  var drag = null;
  function startDrag(e, p, g) {
    if (e.button !== undefined && e.button !== 0) return;
    e.preventDefault();
    drag = { p: p, g: g, view: viewOf(p), root: p.parentElement.classList.contains('view'), x: e.clientX, y: e.clientY, dir: 0, moved: false, raf: 0 };
    // am Fenster lauschen, nicht am Griff: wandert die Karte im Dokument, verliert der Griff sonst die Maus
    // am Bildschirmrand stehen bleiben scrollt weiter (auch ohne Bewegung)
    drag.timer = setInterval(function () {
      if (!drag || drag.raf) return;
      var B = bounds();
      if (drag.y < B.top + 60 || drag.y > B.bottom - 60) drag.raf = requestAnimationFrame(step);
    }, 40);
    p.classList.add('dragging');
    document.body.classList.add('dragging-card');
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', endDrag);
    window.addEventListener('pointercancel', endDrag);
  }
  function onMove(e) {
    if (!drag) return;
    if (Math.abs(e.clientY - drag.y) >= 2) drag.dir = e.clientY < drag.y ? -1 : 1;
    drag.x = e.clientX; drag.y = e.clientY; drag.moved = true;
    if (!drag.raf) drag.raf = requestAnimationFrame(step);
  }
  // sichtbarer Bereich zwischen den festen Leisten (Kopf, Reiter, Sprungleiste oben; Reiter unten am Handy)
  function bounds() {
    var top = 0, bottom = window.innerHeight;
    document.querySelectorAll('.topbar, .tabs-main, section.view:not([hidden]) > .jump-bar').forEach(function (el) {
      var r = el.getBoundingClientRect(), pos = getComputedStyle(el).position;
      if (!r.height || (pos !== 'sticky' && pos !== 'fixed')) return;
      if (r.top < window.innerHeight / 2) top = Math.max(top, r.bottom); else bottom = Math.min(bottom, r.top);
    });
    return { top: top, bottom: bottom };
  }
  function step() {
    if (!drag) return;
    drag.raf = 0;
    var x = drag.x, y = drag.y, p = drag.p, B = bounds(), edge = 60;
    // am Rand: mitscrollen (je näher am Rand, desto schneller)
    if (y < B.top + edge) { window.scrollBy(0, -Math.ceil((B.top + edge - y) / 3)); drag.dir = -1; }
    else if (y > B.bottom - edge) { window.scrollBy(0, Math.ceil((y - (B.bottom - edge)) / 3)); drag.dir = 1; }
    y = Math.min(Math.max(y, B.top + 8), B.bottom - 8);   // unter den Leisten nach der Karte suchen
    p.style.visibility = 'hidden';
    var el = document.elementFromPoint(x, y);
    p.style.visibility = '';
    if (!el) return;
    var tgt = el.closest('.panel[id]');
    if (tgt && tgt !== p && viewOf(tgt) === drag.view && tgt.parentElement.classList.contains('view') === drag.root) {
      // Schwelle höchstens eine Kartenhöhe tief: auch an sehr hohen Karten (Bänder) vorbei, ohne Hin- und Herspringen
      // Karte wandert nur in Zugrichtung (kein Hin- und Herspringen) – sobald der Finger ein Stück in die Nachbarkarte
      // hineinreicht, auch bei sehr hohen Karten wie „Bänder“
      var r = tgt.getBoundingClientRect(), m = Math.min(r.height / 2, p.getBoundingClientRect().height, 120);
      if (tgt.parentElement !== p.parentElement) {
        // in die andere Spalte: rein seitliches Ziehen hat keine Zugrichtung nach oben/unten – vor oder hinter die
        // Karte unter dem Finger, je nachdem, ob er in ihrer oberen oder unteren Hälfte steht
        if (y < r.top + r.height / 2) tgt.before(p); else tgt.after(p);
        return;
      }
      var above = tgt.compareDocumentPosition(p) & Node.DOCUMENT_POSITION_FOLLOWING;   // Ziel steht vor der Karte
      if (above && drag.dir < 0 && y < r.bottom - m) tgt.before(p);
      else if (!above && drag.dir > 0 && y > r.top + m) tgt.after(p);
      return;
    }
    if (!drag.root) {                                      // leere Stelle in einer Spalte: ans Ende dieser Spalte
      var col = el.closest('.col');
      if (col && viewOf(col) === drag.view && col !== p.parentElement) {
        var last = panelsOf(col).pop();
        if (!last || y > last.getBoundingClientRect().bottom) col.appendChild(p);
      }
    }
  }
  function endDrag(e) {
    if (!drag) return;
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', endDrag);
    window.removeEventListener('pointercancel', endDrag);
    if (drag.raf) cancelAnimationFrame(drag.raf);
    clearInterval(drag.timer);
    drag.p.classList.remove('dragging');
    document.body.classList.remove('dragging-card');
    var p = drag.p; drag = null;
    p.classList.add('flash-ok'); setTimeout(function () { p.classList.remove('flash-ok'); }, 900);
    remember();
  }

  function init() {
    restore();
    document.querySelectorAll('section.view .panel[id]').forEach(tools);
    window.HSPLayout = {
      reset: function () { state = { order: {}, folded: [] }; save(state); location.reload(); },
      customized: function () { return Object.keys(state.order).length > 0 || state.folded.length > 0; }
    };
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
