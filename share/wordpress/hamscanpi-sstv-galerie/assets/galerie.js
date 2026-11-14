/* HamScanPi SSTV-Galerie: lädt die Bilder über die WordPress-Schnittstelle, gruppiert (Tag, Quelle, Überflug
 * oder gar nicht – Einstellungen → SSTV-Galerie), Filter je Quelle, Lightbox mit Überblenden, Pfeiltasten, Wischen
 * und Infotafel. Weitere Bilder kommen beim Herunterscrollen von selbst. Direktlink: …/seite/#sstv-123 */
(function () {
  'use strict';
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); };
  var DAYS = ['Sonntag', 'Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag'];
  var MONTHS = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember'];
  function dayLabel(d) { var x = new Date(d + 'T12:00:00Z'); return DAYS[x.getUTCDay()] + ', ' + x.getUTCDate() + '. ' + MONTHS[x.getUTCMonth()] + ' ' + x.getUTCFullYear(); }
  // Uhrzeit mit Kennung der Zeitzone (UTC oder Ortszeit, wie in HamScanPi eingestellt)
  function utc(s, tz) { tz = tz || 'UTC'; return esc(s) + '<abbr class="hsp-utc" title="' + (tz === 'UTC' ? 'koordinierte Weltzeit' : 'Ortszeit') + '">' + esc(tz) + '</abbr>'; }
  function pct(x) { return Math.round((Number(x) || 0) * 100); }

  function Gallery(root) {
    this.root = root;
    this.endpoint = root.dataset.endpoint;
    this.per = Number(root.dataset.perPage) || 48;
    this.quelle = root.dataset.quelle || '';
    this.items = []; this.view = []; this.featured = []; this.page = 0; this.pages = 1; this.total = 0; this.busy = false;
    this.opts = { group: 'day', chips: 'count', order: 'desc' };
    this.$stats = root.querySelector('.hsp-stats'); this.$filter = root.querySelector('.hsp-filter');
    this.$days = root.querySelector('.hsp-days'); this.$more = root.querySelector('.hsp-more');
    var self = this;
    if (this.$more) {
      this.$more.addEventListener('click', function () { self.load(); });
      // Nachladen von selbst, sobald der Besucher unten ankommt; der Knopf bleibt nur als Ersatz
      if ('IntersectionObserver' in window) {
        new IntersectionObserver(function (en) { if (en[0].isIntersecting) self.more(); }, { rootMargin: '0px 0px 400px 0px' }).observe(this.$more);
      }
    }
    this.load(true);
  }
  Gallery.prototype.more = function () {
    if (this.busy || this.page >= this.pages || !this.$more || this.$more.hidden) return;
    var r = this.$more.getBoundingClientRect();
    if (r.top < window.innerHeight + 400) this.load(false);
  }
  Gallery.prototype.load = function (first) {
    var self = this, url = this.endpoint + (this.endpoint.indexOf('?') < 0 ? '?' : '&') + 'page=' + (this.page + 1) + '&per_page=' + this.per
      + (this.quelle ? '&quelle=' + encodeURIComponent(this.quelle) : '');
    if (this.busy) return;
    this.busy = true;
    if (this.$more) { this.$more.disabled = true; this.$more.textContent = 'Bilder werden geladen …'; }
    var failed = false, reset = this.page === 0;
    fetch(url, { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (d) {
      if (reset) { self.items = []; if (d.featured) self.featured = d.featured; }
      self.page += 1; self.pages = d.pages || 0; self.total = d.total || 0;
      self.items = self.items.concat(d.items || []);
      if (d.opts) self.opts = d.opts;
      if (first) { self.stats(d); self.chips(d.sources || []); }
      self.render();
      if (first) self.openFromHash();
    }).catch(function () {
      failed = true;
      if (!self.items.length) self.$days.innerHTML = '<p class="hsp-empty">Die Bilder ließen sich gerade nicht laden.</p>';
    }).then(function () {
      self.busy = false;
      if (!self.$more) return;
      var rest = Math.max(0, self.total - self.items.length);
      // nur zeigen, wenn es wirklich noch Bilder gibt (oder nach einem Fehler zum erneuten Versuch)
      self.$more.hidden = failed ? false : !(self.page < self.pages && rest > 0);
      self.$more.disabled = false;
      self.$more.textContent = failed ? 'Erneut versuchen' : 'Weitere Bilder laden' + (rest ? ' (noch ' + rest + ')' : '');
      if (!failed) self.more();                    // Seite noch nicht voll: gleich weiter
    });
  };
  Gallery.prototype.stats = function (d) {
    if (!this.$stats) return;
    var src = d.sources || [], b = d.best, first = d.latest || this.items[0];
    var box = function (v, t) { return '<div class="hsp-stat"><b>' + v + '</b><span>' + t + '</span></div>'; };
    var h = box(d.total || 0, 'SSTV-Bilder') + box(src.length, src.length === 1 ? 'Quelle' : 'Quellen');
    if (first) h += box(esc(first.day.slice(8, 10) + '.' + first.day.slice(5, 7) + '.' + first.day.slice(0, 4)), 'zuletzt empfangen');
    if (b && b.summary && b.summary.max_el != null) h += box(esc(b.summary.max_el) + '°', 'höchster Überflug (' + esc(b.source) + ')');
    this.$stats.innerHTML = h;
  };
  Gallery.prototype.chips = function (src) {
    if (!this.$filter || this.quelle || src.length < 2 || this.opts.chips === 'off') { if (this.$filter) this.$filter.hidden = true; return; }
    var self = this, total = src.reduce(function (a, s) { return a + s.count; }, 0);
    src = src.slice().sort(this.opts.chips === 'alpha' ? function (a, b) { return a.name.localeCompare(b.name, 'de'); }
      : function (a, b) { return b.count - a.count || a.name.localeCompare(b.name, 'de'); });
    this.sourceOrder = src.map(function (s) { return s.slug; });
    this.$filter.innerHTML = '<button type="button" class="hsp-chip on" data-s="">Alle<span class="n">' + total + '</span></button>'
      + src.map(function (s) { return '<button type="button" class="hsp-chip" data-s="' + esc(s.slug) + '">' + esc(s.name) + '<span class="n">' + s.count + '</span></button>'; }).join('');
    this.$filter.addEventListener('click', function (e) {
      var c = e.target.closest('.hsp-chip'); if (!c) return;
      self.$filter.querySelectorAll('.hsp-chip').forEach(function (x) { x.classList.toggle('on', x === c); });
      // gefiltert neu vom Server (dort sind auch die noch nicht geladenen Bilder dieser Quelle)
      self.quelle = c.dataset.s; self.page = 0; self.pages = 1; self.load(false);
    });
  };
  // Gruppen nach Einstellung; this.view = Reihenfolge wie angezeigt (für die Lightbox)
  Gallery.prototype.groups = function () {
    var list = this.items, G = this.opts.group, out = [], idx = {};
    var n = function (k) { return k + (k === 1 ? ' Bild' : ' Bilder'); };
    var head = [];
    if (this.featured.length && !this.quelle) head.push({ title: '★ Hervorgehoben', small: n(this.featured.length), items: this.featured.slice() });
    if (G === 'none') return head.concat([{ title: '', items: list.slice() }]);
    if (G === 'pass') {                                  // Bilder einer Quelle mit weniger als 20 min Abstand = ein Überflug
      var open = {};
      list.forEach(function (it) {
        var k = it.slug || it.source, g = open[k];
        if (!g || Math.abs(it.t - g.last) > 1200) { g = open[k] = { items: [], src: it.source, first: it, max: null }; out.push(g); }
        g.items.push(it); g.last = it.t;
        var e = it.summary && it.summary.max_el; if (e != null && (g.max == null || e > g.max)) g.max = e;
      });
      out.forEach(function (g) {
        g.title = esc(g.src) + ' · ' + dayLabel(g.first.day);
        g.small = utc(g.first.time, g.first.tz) + ' · ' + n(g.items.length) + (g.max != null ? ' · max. ' + esc(g.max) + '°' : '');
      });
      return head.concat(out);
    }
    list.forEach(function (it) {
      var k = G === 'source' ? (it.slug || it.source) : it.day;
      if (!idx[k]) { idx[k] = { key: k, items: [], src: it.source, day: it.day, tz: it.tz }; out.push(idx[k]); }
      idx[k].items.push(it);
    });
    if (G === 'source') {
      var so = this.sourceOrder || [];
      out.sort(function (a, b) { return ((so.indexOf(a.key) + 1) || 999) - ((so.indexOf(b.key) + 1) || 999) || b.items.length - a.items.length; });
    }
    out.forEach(function (g) {
      g.title = G === 'source' ? esc(g.src) : dayLabel(g.day);
      g.small = n(g.items.length) + (G === 'day' ? ' · Datum in ' + esc(g.tz || 'UTC') : '');
    });
    return head.concat(out);
  };
  Gallery.prototype.render = function () {
    if (!this.items.length) { this.view = []; this.$days.innerHTML = '<p class="hsp-empty">Noch keine Bilder veröffentlicht.</p>'; return; }
    var view = [], G = this.opts.group;
    this.$days.innerHTML = this.groups().map(function (g) {
      return '<section class="hsp-day">' + (g.title ? '<h3>' + g.title + ' <small>' + (g.small || '') + '</small></h3>' : '') + '<div class="hsp-grid">'
        + g.items.map(function (it) {
          var i = view.push(it) - 1, s = it.summary || {};
          // Beschriftung: was die Überschrift nicht schon sagt
          var head = G === 'source' ? esc(it.day.slice(8, 10) + '.' + it.day.slice(5, 7) + '.' + it.day.slice(0, 4)) : G === 'pass' ? '' : esc(it.source);
          return '<button type="button" class="hsp-card" data-i="' + i + '" aria-label="' + esc(it.source + ', ' + it.day + ' ' + it.time + ' ' + (it.tz || 'UTC')) + '">'
            + (it.thumb ? '<img loading="lazy" decoding="async" src="' + esc(it.thumb.url) + '" alt="">' : '')
            + '<div class="hsp-bar" title="' + pct(s.complete) + ' % der Zeilen"><i style="width:' + pct(s.complete) + '%"></i></div>'
            + '<div class="cap">' + (head ? '<b>' + head + '</b>' : '') + '<span>' + utc(it.time || it.time_utc.slice(11, 19), it.tz) + ' · ' + esc(s.mode || 'SSTV')
            + (s.max_el != null && G !== 'pass' ? ' · max. ' + esc(s.max_el) + '°' : '') + '</span></div></button>';
        }).join('') + '</div></section>';
    }).join('');
    this.view = view;
    var self = this;
    this.$days.querySelectorAll('.hsp-card').forEach(function (b) { b.addEventListener('click', function () { LB.open(self, Number(b.dataset.i)); }); });
  };
  Gallery.prototype.openFromHash = function () {
    var m = /^#sstv-(\d+)$/.exec(location.hash); if (!m) return;
    var i = this.view.findIndex(function (x) { return String(x.id) === m[1]; });
    if (i >= 0) LB.open(this, i);
  };

  /* ---------------------------------------------------------------- Lightbox */
  var LB = {
    el: null, g: null, i: 0, front: 0, last: null,
    build: function () {
      if (this.el) return;
      var d = document.createElement('div');
      d.className = 'hsp-lb'; d.hidden = true; d.setAttribute('role', 'dialog'); d.setAttribute('aria-modal', 'true'); d.setAttribute('aria-label', 'SSTV-Bild');
      d.innerHTML = '<div class="hsp-stage"><span class="hsp-count"></span><img alt=""><img alt="">'
        + '<button type="button" class="hsp-prev" aria-label="voriges Bild">‹</button><button type="button" class="hsp-next" aria-label="nächstes Bild">›</button>'
        + '<button type="button" class="hsp-infobtn" aria-label="Angaben ein- oder ausblenden">ⓘ Angaben</button>'
        + '<button type="button" class="hsp-close" aria-label="schließen">✕</button><div class="hsp-caption"></div></div><aside class="hsp-info"></aside>';
      // Farben/Variablen der Galerie übernehmen
      document.body.appendChild(d);
      this.el = d; this.imgs = d.querySelectorAll('.hsp-stage img');
      var self = this;
      d.querySelector('.hsp-prev').addEventListener('click', function () { self.go(-1); });
      d.querySelector('.hsp-next').addEventListener('click', function () { self.go(1); });
      d.querySelector('.hsp-close').addEventListener('click', function () { self.close(); });
      d.querySelector('.hsp-infobtn').addEventListener('click', function () {
        d.classList.toggle('noinfo'); try { localStorage.setItem('hspNoInfo', d.classList.contains('noinfo') ? '1' : '0'); } catch (e) { }
      });
      d.querySelector('.hsp-stage').addEventListener('click', function (e) { if (e.target === e.currentTarget) self.close(); });
      document.addEventListener('keydown', function (e) {
        if (d.hidden) return;
        if (e.key === 'Escape') self.close();
        else if (e.key === 'ArrowLeft') self.go(-1);
        else if (e.key === 'ArrowRight') self.go(1);
        else if (e.key === 'i') d.querySelector('.hsp-infobtn').click();
      });
      var x0 = null, y0 = null;                       // Wischen am Handy
      d.addEventListener('touchstart', function (e) { x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; }, { passive: true });
      d.addEventListener('touchend', function (e) {
        if (x0 == null) return;
        var dx = e.changedTouches[0].clientX - x0, dy = e.changedTouches[0].clientY - y0; x0 = null;
        if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.5) self.go(dx < 0 ? 1 : -1);
      }, { passive: true });
      try { if (localStorage.getItem('hspNoInfo') === '1') d.classList.add('noinfo'); } catch (e) { }
    },
    open: function (g, i) {
      this.build(); this.g = g; this.last = document.activeElement;
      var cs = getComputedStyle(g.root);
      ['--hsp-accent', '--hsp-radius', '--hsp-fade'].forEach(function (v) { var x = cs.getPropertyValue(v); if (x) LB.el.style.setProperty(v, x.trim()); });
      this.el.hidden = false; document.documentElement.style.overflow = 'hidden';
      requestAnimationFrame(function () { LB.el.classList.add('open'); });
      this.show(i, true);
      this.el.querySelector('.hsp-close').focus();
    },
    close: function () {
      var self = this; this.el.classList.remove('open'); document.documentElement.style.overflow = '';
      setTimeout(function () { self.el.hidden = true; }, 250);
      if (/^#sstv-/.test(location.hash)) history.replaceState(null, '', location.pathname + location.search);
      if (this.last && this.last.focus) this.last.focus();
    },
    go: function (d) {
      var n = this.g.view.length, i = this.i + d;
      if (i >= n && this.g.page < this.g.pages) {     // am Ende: nächste Seite nachladen, dann das Bild nach dem jetzigen
        var self = this, id = this.g.view[this.i].id, before = this.g.items.length;
        this.g.load(false);
        var wait = setInterval(function () {
          if (self.g.items.length > before) {
            clearInterval(wait);
            var k = self.g.view.findIndex(function (x) { return x.id === id; });
            self.show(Math.min(self.g.view.length - 1, k + 1));
          }
        }, 150);
        setTimeout(function () { clearInterval(wait); }, 8000);
        return;
      }
      if (n < 2) return;
      this.show((i + n) % n);
    },
    show: function (i, instant) {
      var it = this.g.view[i]; if (!it || !it.full) return;
      this.i = i;
      var next = this.imgs[1 - this.front], cur = this.imgs[this.front], self = this;
      next.onload = function () {
        next.classList.add('on'); cur.classList.remove('on'); self.front = 1 - self.front;
      };
      if (instant) { cur.classList.remove('on'); }
      next.alt = it.source + ', ' + it.time_utc + ' UTC';
      if (next.src === it.full.url && next.complete) next.onload();      // schon geladen: kein neues load-Ereignis
      else next.src = it.full.url;
      var s = it.summary || {};
      this.el.querySelector('.hsp-count').textContent = (i + 1) + ' / ' + this.g.view.length + (this.g.page < this.g.pages ? '+' : '');
      this.el.querySelector('.hsp-caption').innerHTML = '<b>' + esc(it.source) + '</b> · ' + esc(s.mode || 'SSTV') + ' · ' + pct(s.complete) + ' %'
        + '<small>' + esc(dayLabel(it.day)) + ', ' + utc(it.time || it.time_utc.slice(11, 19), it.tz) + (s.call ? ' · empfangen von ' + esc(s.call) : '') + (s.locator ? ' (' + esc(s.locator) + ')' : '') + '</small>';
      this.el.querySelector('.hsp-info').innerHTML = (it.sections || []).map(function (sec) {
        return '<h4>' + esc(sec.title) + '</h4><dl>' + sec.rows.map(function (r) {
          var v = esc(r[1]).replace(/ (UTC|C?E[SD]?T|M?ESZ|MEZ)\b/g, function (m, z) { return '<abbr class="hsp-utc">' + z + '</abbr>'; });
          return '<dt>' + esc(r[0]) + '</dt><dd>' + v + '</dd>';
        }).join('') + '</dl>';
      }).join('') + '<h4>Bild</h4><dl><dt>Originaldatei</dt><dd><a href="' + esc(it.full.url) + '" target="_blank" rel="noopener" style="color:inherit">' + it.full.w + ' × ' + it.full.h + ' Pixel öffnen</a></dd></dl>';
      history.replaceState(null, '', '#sstv-' + it.id);
      [i - 1, i + 1].forEach(function (k) { var x = self.g.view[k]; if (x && x.full) { var p = new Image(); p.src = x.full.url; } });  // Nachbarn vorladen
    }
  };

  function init() { document.querySelectorAll('.hsp-galerie[data-endpoint]').forEach(function (r) { if (!r._hsp) r._hsp = new Gallery(r); }); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
