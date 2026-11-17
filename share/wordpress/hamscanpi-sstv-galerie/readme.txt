=== Hesperos DX SSTV-Galerie ===
Requires at least: 5.6
Requires PHP: 7.4
Stable tag: 1.4.3
License: GPL-3.0-or-later

Empfängt SSTV-Bilder von Hesperos DX und zeigt sie als Galerie.

== Einrichten ==
1. Plugins → Installieren → Plugin hochladen → hamscanpi-sstv-galerie.zip → aktivieren.
2. Benutzer → Profil → Anwendungspasswörter: Name „Hesperos DX“ → Passwort erzeugen (nur einmal sichtbar).
3. In Hesperos DX: Einstellungen → SSTV-Export: Adresse, Benutzer, Anwendungspasswort → „Verbindung prüfen“, „Testbild senden“.
4. Eine Seite anlegen (z. B. unter Satelliten) mit dem Kurzcode [sstv_galerie].
5. Neue Bilder kommen als Entwurf (SSTV-Bilder im Menü) – prüfen und veröffentlichen. Später in Hesperos DX
   „sofort veröffentlichen“ wählen.

== Anordnung ==
* Einstellungen → SSTV-Galerie → Anordnung: Reihenfolge (neueste/älteste zuerst), Gruppieren (nach Tag, Quelle,
  Überflug oder gar nicht), Datum und Uhrzeit (wie in Hesperos DX, immer UTC, Zeitzone der Seite), Filterknöpfe
  (nach Anzahl, alphabetisch, ausblenden).
* Je Bild (SSTV-Bilder → Bild bearbeiten → „Einordnung in der Galerie“): Empfangszeit (UTC), Quelle, „hervorheben“
  (steht zusätzlich oben). Die Galerie sortiert sofort neu.
* Quellen umbenennen: SSTV-Bilder → Quellen.
* Von Hand in Hesperos DX nachgereichte Bilder („Auf Webseite“ in der Trefferkarte): wie automatische oder immer
  als Entwurf.
* Weitere Bilder lädt die Galerie beim Herunterscrollen von selbst nach.

== Anpassen ==
* Einstellungen → SSTV-Galerie: Farben, Ecken, Größe der Vorschaubilder, Überblenddauer, eigenes CSS, Plugin-CSS aus.
* CSS-Variablen im Theme: .hsp-galerie { --hsp-accent: …; --hsp-card: …; --hsp-gap: …; }
* Vorlage ins Theme kopieren: <theme>/hamscanpi-sstv/galerie.php
* Filter: hsp_sstv_item, hsp_sstv_insert_status

== Mehrere Pis ==
Je Pi einen Benutzer mit der Rolle „SSTV-Melder“ anlegen (Einstellungen → SSTV-Galerie → Zugang für die Pis) und in
Hesperos DX unter Einstellungen → SSTV-Export eintragen. Jedes Bild trägt den Stationsnamen des Pi (Hesperos DX →
Einstellungen → Station); die Liste „SSTV-Bilder“ lässt sich danach filtern.

== Datenschutz ==
Übertragen werden Bild, Satelliten- und Empfangsangaben, Rufzeichen, Locator, Stationsname und Werte des
Raspberry Pi (Temperatur, Last, Speicher). Keine IP- oder MAC-Adressen, kein WLAN, keine Zugangsdaten. Station
und Pi-Werte lassen sich in den Einstellungen ausblenden.

== Änderungen ==
= 1.4.3 =
* Links auf Hesperos DX bei GitHub (https://github.com/do1gmt/hesperos-dx): in der Pluginliste, auf der Einstellungsseite und in der Zeile „Empfangen mit …“ unter der Galerie.
= 1.4.2 =
* Hinweise nennen die Karte „SSTV-Export“ (vorher „Webseite“) im Dashboard.
= 1.4.1 =
* Neuer Name: Hesperos DX · Ham Radio Watch (früher HamScanPi). Ordner und Schnittstelle bleiben – das Update ersetzt das alte Plugin.
* Zeile „Empfangen mit Hesperos DX · Ham Radio Watch“ unter der Galerie (abschaltbar).
= 1.4.0 =
* Rolle „SSTV-Melder“ (darf nur Bilder einsenden); unter Einstellungen → SSTV-Galerie → Zugang für die Pis je Pi einen Benutzer mit Anwendungspasswort anlegen.
* Liste der SSTV-Bilder: Spalte „Eingesandt von“ (Stationsname des Pi und Benutzer), Filter nach Station; Protokoll mit Station.
* Kurzcode station="…": nur Bilder eines Pi.
* Freigabe-Link für Hesperos DX auch bei Einsendungen eines SSTV-Melders; Links zeigen auf die Galerie ohne Filter.
= 1.3.1 =
* Galerie nie breiter als der Inhaltsbereich des Themes (auch wenn der Kurzcode außerhalb eines Inhaltsrahmens steht), am Handy mit Rand; „Größte Breite“ einstellbar.
= 1.3.0 =
* Anordnung einstellbar: Reihenfolge, Gruppierung (Tag, Quelle, Überflug, keine), Zeitzone, Filterknöpfe.
* Je Bild: Empfangszeit, Quelle und „hervorheben“ bearbeiten.
* Von Hand nachgereichte Bilder erkennbar, auf Wunsch immer als Entwurf.
* „Weitere Bilder laden“ erscheint nur noch, wenn es weitere Bilder gibt; beim Herunterscrollen wird von selbst nachgeladen.
= 1.2.0 =
* Seitenkopf wie die übrigen Seiten, Farben vom Theme, Zeiten in der Zeit der Station mit Kennung, Protokoll.
