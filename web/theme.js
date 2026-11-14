/* Hesperos DX – Design (hell/dunkel/…) je Browser. Wird im <head> geladen, damit die Seite gleich richtig erscheint.
 * auto = wie das Gerät (hell oder dunkel), light, dark, contrast (hoher Kontrast), night (rot, für die Nacht). */
(function () {
  var LIST = [['auto', 'Automatisch'], ['dark', 'Dunkel'], ['light', 'Hell'], ['ocean', 'Ozean'], ['forest', 'Wald'], ['tube', 'Röhre'],
              ['aubergine', 'Aubergine'], ['contrast', 'Hoher Kontrast'], ['night', 'Nacht (rot)']];
  var BAR = { dark: '#0f1620', light: '#ffffff', contrast: '#000000', night: '#080000', ocean: '#071a2b', forest: '#0b1a12', tube: '#1a1208',
              aubergine: '#170f22' };
  function valid(t) { for (var i = 0; i < LIST.length; i++) if (LIST[i][0] === t) return true; return false; }
  function apply(t) {
    var root = document.documentElement;
    root.setAttribute('data-theme', t);
    var light = t === 'light' || (t === 'auto' && window.matchMedia && matchMedia('(prefers-color-scheme: light)').matches);
    var cs = document.querySelector('meta[name="color-scheme"]');
    if (cs) cs.setAttribute('content', t === 'auto' ? 'dark light' : (light ? 'light' : 'dark'));
    var tc = document.querySelector('meta[name="theme-color"]');
    if (tc) tc.setAttribute('content', light ? BAR.light : (BAR[t] || BAR.dark));
  }
  var cur = 'auto';
  try { cur = localStorage.getItem('theme') || 'auto'; } catch (e) { /* Speicher gesperrt */ }
  if (!valid(cur)) cur = 'auto';
  apply(cur);
  if (window.matchMedia) { try { matchMedia('(prefers-color-scheme: light)').addEventListener('change', function () { apply(cur); }); } catch (e) { } }
  window.HSPTheme = {
    list: LIST,
    get: function () { return cur; },
    set: function (t) { if (!valid(t)) t = 'auto'; cur = t; try { localStorage.setItem('theme', t); } catch (e) { } apply(t); },
    next: function () { for (var i = 0; i < LIST.length; i++) if (LIST[i][0] === cur) return LIST[(i + 1) % LIST.length][0]; return 'auto'; },
    label: function (t) { for (var i = 0; i < LIST.length; i++) if (LIST[i][0] === t) return LIST[i][1]; return t; }
  };
})();
