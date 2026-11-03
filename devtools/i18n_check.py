#!/usr/bin/env python3
"""Übersetzung prüfen: öffnet das Dashboard in der englischen Oberfläche (ui_lang = en), geht durch alle Reiter,
klappt alles auf, öffnet einige Treffer und listet sichtbare Texte, die noch nach Deutsch aussehen
(Umlaute, häufige deutsche Wörter). Braucht Playwright mit Chromium (pip install playwright; playwright install chromium).
   python3 devtools/i18n_check.py [port]      (Webserver muss laufen, Sprache auf Englisch)
"""
import re, sys, json
from playwright.sync_api import sync_playwright
PORT = sys.argv[1] if len(sys.argv) > 1 else "8079"
GER = re.compile(r"[äöüÄÖÜß]|\b(und|oder|nicht|noch|wird|werden|mit|für|bei|der|die|das|den|dem|des|ein|eine|einen|keine|kein|ist|sind|aus|auf|nach|von|vor|zum|zur|über|unter|Nachrichten|Takt|Treffer|Stationen|Satelliten|Überflug|gehört|Aufnahme|antwortet|abgeschlossen|gibt|bestätigt|Einstellungen|Sekunden|Minuten|Stunden|heute|gestern|läuft|fertig|Fehler|Hinweis|zuletzt|Uhr|nur|auch|wenn|dann|beim|alle|bitte|ohne|jetzt|hier|Seite|Karte|Liste|Empfang|Frequenz|Zeitraum|Ton|Träger|Antenne|Gerät|neu|gespeichert|gelöscht|zeigen|wählen|speichern|Protokoll|Dienst|Sicherung|Ablage|Relais|Baken|Rundfunk|Wetter|Sprache|Daten|Bild|Bilder|Kanal|Kanäle|Zeilen|Sender|Ausgabe|Eingabe|schmal|breit|automatisch|Verstärkung|Abstimmung|Rauschsperre|Lautstärke|Aufnahmen|Bänder|Betriebsart|Schritt|Suchlauf|Weiter|weiter|Zurück|Anzeige|Einstellung|Hilfe)\b")
JS = """() => {
  const out = new Set();
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = w.nextNode())) {
    const t = n.nodeValue.replace(/\\s+/g, ' ').trim();
    if (t.length < 3) continue;
    const el = n.parentElement; if (!el) continue;
    if (el.closest('.no-i18n, script, style, code, pre, .mono, [hidden]')) continue;
    const cs = getComputedStyle(el); if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    out.add(t);
  }
  document.querySelectorAll('[title],[placeholder],[aria-label]').forEach(e => {
    if (e.closest('.no-i18n')) return;
    for (const a of ['title','placeholder','aria-label']) { const v = e.getAttribute(a); if (v && v.trim().length > 2) out.add('@' + a + ': ' + v.trim()); }
  });
  return [...out];
}"""
found = {}
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1400, "height": 1000}, locale="en-US")
    pg.route("**/*.tile.openstreetmap.org/**", lambda r: r.abort())
    pg.goto(f"http://localhost:{PORT}/", wait_until="networkidle"); pg.wait_for_timeout(1500)
    for view in ["scan", "hits", "stations", "live", "logs", "system", "settings"]:
        pg.locator(f".tabs-main [data-view={view}]").click(); pg.wait_for_timeout(2500)
        pg.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)"); pg.wait_for_timeout(800)
        if view == "hits":
            cards = pg.locator(".hit-head, .hit summary, .hit")
            for i in range(min(cards.count(), 8)):
                try: cards.nth(i).click(timeout=2000); pg.wait_for_timeout(700)
                except Exception: pass
            pg.wait_for_timeout(1500)
        for t in pg.evaluate(JS):
            if GER.search(t):
                found.setdefault(t, view)
    b.close()
for t, v in sorted(found.items(), key=lambda x: x[1]):
    print(f"[{v}] {t[:230]}")
print(len(found), "Funde")
