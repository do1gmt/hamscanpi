/* HamScanPi SSTV-Galerie: lädt die Bilder über die WordPress-Schnittstelle, gruppiert nach Tag (UTC), Filter je
 * Quelle, Lightbox mit Überblenden, Pfeiltasten, Wischen und Infotafel. Direktlink: …/seite/#sstv-123 */
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
    this.items = []; this.page = 0; this.pages = 1; this.filter = '';
    this.$stats = root.querySelector('.hsp-stats'); this.$filter = root.querySelector('.hsp-filter');
    this.$days = root.querySelector('.hsp-days'); this.$more = root.querySelector('.hsp-more');
    var self = this;
    if (this.$more) this.$more.addEventListener('click', function () { self.load(); });
    this.load(true);
  }
  Gallery.prototype.load = function (first) {
    var self = this, url = this.endpoint + (this.endpoint.indexOf('?') < 0 ? '?' : '&') + 'page=' + (this.page + 1) + '&per_page=' + this.per
      + (this.quelle ? '&quelle=' + encodeURIComponent(this.quelle) : '');
    if (this.$more) this.$more.disabled = true;
    fetch(url, { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (d) {
      self.page += 1; self.pages = d.pages || 1;
      self.items = self.items.concat(d.items || []);
      if (first) { self.stats(d); self.chips(d.sources || []); }
      self.render();
      if (first) self.openFromHash();
    }).catch(function () {
      self.$days.innerHTML = '<p class="hsp-empty">Die Bilder ließen sich gerade nicht laden.</p>';
    }).then(function () {
      if (self.$more) { self.$more.disabled = false; self.$more.hidden = self.page >= self.pages; }
    });
  };
  Gallery.prototype.stats = function (d) {
    if (!this.$stats) return;
    var src = d.sources || [], b = d.best, first = this.items[0];
    var box = function (v, t) { return '<div class="hsp-stat"><b>' + v + '</b><span>' + t + '</span></div>'; };
    var h = box(d.total || 0, 'SSTV-Bilder') + box(src.length, src.length === 1 ? 'Quelle' : 'Quellen');
    if (first) h += box(esc(first.day.slice(8, 10) + '.' + first.day.slice(5, 7) + '.' + first.day.slice(0, 4)), 'zuletzt empfangen');
    if (b && b.summary && b.summary.max_el != null) h += box(esc(b.summary.max_el) + '°', 'höchster Überflug (' + esc(b.source) + ')');
    this.$stats.innerHTML = h;
  };
  Gallery.prototype.chips = function (src) {
    if (!this.$filter || this.quelle || src.length < 2) { if (this.$filter) this.$filter.hidden = true; return; }
    var self = this, total = src.reduce(function (a, s) { return a + s.count; }, 0);
    this.$filter.innerHTML = '<button type="button" class="hsp-chip on" data-s="">Alle<span class="n">' + total + '</span></button>'
      + src.map(function (s) { return '<button type="button" class="hsp-chip" data-s="' + esc(s.slug) + '">' + esc(s.name) + '<span class="n">' + s.count + '</span></button>'; }).join('');
    this.$filter.addEventListener('click', function (e) {
      var c = e.target.closest('.hsp-chip'); if (!c) return;
      self.$filter.querySelectorAll('.hsp-chip').forEach(function (x) { x.classList.toggle('on', x === c); });
      // gefiltert neu vom Server (dort sind auch die noch nicht geladenen Bilder dieser Quelle)
      self.quelle = c.dataset.s; self.items = []; self.page = 0; self.load(false);
    });
  };
  Gallery.prototype.visible = function () { return this.items; };
  Gallery.prototype.render = function () {
    var list = this.visible(), groups = {}, order = [];
    list.forEach(function (it, i) { if (!groups[it.day]) { groups[it.day] = []; order.push(it.day); } groups[it.day].push(i); });
    if (!list.length) { this.$days.innerHTML = '<p class="hsp-empty">Noch keine Bilder veröffentlicht.</p>'; return; }
    this.$days.innerHTML = order.map(function (d) {
      var ids = groups[d];
      return '<section class="hsp-day"><h3>' + dayLabel(d) + ' <small>' + ids.length + (ids.length === 1 ? ' Bild' : ' Bilder') + ' · Datum in ' + esc(list[ids[0]].tz || 'UTC') + '</small></h3><div class="hsp-grid">'
        + ids.map(function (i) {
          var it = list[i], s = it.summary || {};
          return '<button type="button" class="hsp-card" data-i="' + i + '" aria-label="' + esc(it.source + ', ' + it.time_utc + ' UTC') + '">'
            + (it.thumb ? '<img loading="lazy" decoding="async" src="' + esc(it.thumb.url) + '" alt="">' : '')
            + '<div class="hsp-bar" title="' + pct(s.complete) + ' % der Zeilen"><i style="width:' + pct(s.complete) + '%"></i></div>'
            + '<div class="cap"><b>' + esc(it.source) + '</b><span>' + utc(it.time || it.time_utc.slice(11, 19), it.tz) + ' · ' + esc(s.mode || 'SSTV')
            + (s.max_el != null ? ' · max. ' + esc(s.max_el) + '°' : '') + '</span></div></button>';
        }).join('') + '</div></section>';
    }).join('');
    var self = this;
    this.$days.querySelectorAll('.hsp-card').forEach(function (b) { b.addEventListener('click', function () { LB.open(self, Number(b.dataset.i)); }); });
  };
  Gallery.prototype.openFromHash = function () {
    var m = /^#sstv-(\d+)$/.exec(location.hash); if (!m) return;
    var i = this.items.findIndex(function (x) { return String(x.id) === m[1]; });
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
      var n = this.g.items.length; if (n < 2) return;
      var i = this.i + d;
      if (i >= n && this.g.page < this.g.pages) {     // am Ende: nächste Seite nachladen
        var self = this, before = n;
        this.g.load(false);
        var wait = setInterval(function () { if (self.g.items.length > before) { clearInterval(wait); self.show(before); } }, 150);
        setTimeout(function () { clearInterval(wait); }, 8000);
        return;
      }
      this.show((i + n) % n);
    },
    show: function (i, instant) {
      var it = this.g.items[i]; if (!it || !it.full) return;
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
      this.el.querySelector('.hsp-count').textContent = (i + 1) + ' / ' + this.g.items.length + (this.g.page < this.g.pages ? '+' : '');
      this.el.querySelector('.hsp-caption').innerHTML = '<b>' + esc(it.source) + '</b> · ' + esc(s.mode || 'SSTV') + ' · ' + pct(s.complete) + ' %'
        + '<small>' + esc(dayLabel(it.day)) + ', ' + utc(it.time || it.time_utc.slice(11, 19), it.tz) + (s.call ? ' · empfangen von ' + esc(s.call) : '') + (s.locator ? ' (' + esc(s.locator) + ')' : '') + '</small>';
      this.el.querySelector('.hsp-info').innerHTML = (it.sections || []).map(function (sec) {
        return '<h4>' + esc(sec.title) + '</h4><dl>' + sec.rows.map(function (r) {
          var v = esc(r[1]).replace(/ (UTC|C?E[SD]?T|M?ESZ|MEZ)\b/g, function (m, z) { return '<abbr class="hsp-utc">' + z + '</abbr>'; });
          return '<dt>' + esc(r[0]) + '</dt><dd>' + v + '</dd>';
        }).join('') + '</dl>';
      }).join('') + '<h4>Bild</h4><dl><dt>Originaldatei</dt><dd><a href="' + esc(it.full.url) + '" target="_blank" rel="noopener" style="color:inherit">' + it.full.w + ' × ' + it.full.h + ' Pixel öffnen</a></dd></dl>';
      history.replaceState(null, '', '#sstv-' + it.id);
      [i - 1, i + 1].forEach(function (k) { var x = self.g.items[k]; if (x && x.full) { var p = new Image(); p.src = x.full.url; } });  // Nachbarn vorladen
    }
  };

  function init() { document.querySelectorAll('.hsp-galerie[data-endpoint]').forEach(function (r) { if (!r._hsp) r._hsp = new Gallery(r); }); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
