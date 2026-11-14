=== HamScanPi SSTV-Galerie ===
Requires at least: 5.6
Requires PHP: 7.4
Stable tag: 1.3.0
License: GPL-3.0-or-later

Empfängt SSTV-Bilder von HamScanPi und zeigt sie als Galerie.

== Einrichten ==
1. Plugins → Installieren → Plugin hochladen → hamscanpi-sstv-galerie.zip → aktivieren.
2. Benutzer → Profil → Anwendungspasswörter: Name „HamScanPi“ → Passwort erzeugen (nur einmal sichtbar).
3. In HamScanPi: Einstellungen → Webseite: Adresse, Benutzer, Anwendungspasswort → „Verbindung prüfen“, „Testbild senden“.
4. Eine Seite anlegen (z. B. unter Satelliten) mit dem Kurzcode [sstv_galerie].
5. Neue Bilder kommen als Entwurf (SSTV-Bilder im Menü) – prüfen und veröffentlichen. Später in HamScanPi
   „sofort veröffentlichen“ wählen.

== Anordnung ==
* Einstellungen → SSTV-Galerie → Anordnung: Reihenfolge (neueste/älteste zuerst), Gruppieren (nach Tag, Quelle,
  Überflug oder gar nicht), Datum und Uhrzeit (wie in HamScanPi, immer UTC, Zeitzone der Seite), Filterknöpfe
  (nach Anzahl, alphabetisch, ausblenden).
* Je Bild (SSTV-Bilder → Bild bearbeiten → „Einordnung in der Galerie“): Empfangszeit (UTC), Quelle, „hervorheben“
  (steht zusätzlich oben). Die Galerie sortiert sofort neu.
* Quellen umbenennen: SSTV-Bilder → Quellen.
* Von Hand in HamScanPi nachgereichte Bilder („Auf Webseite“ in der Trefferkarte): wie automatische oder immer
  als Entwurf.
* Weitere Bilder lädt die Galerie beim Herunterscrollen von selbst nach.

== Anpassen ==
* Einstellungen → SSTV-Galerie: Farben, Ecken, Größe der Vorschaubilder, Überblenddauer, eigenes CSS, Plugin-CSS aus.
* CSS-Variablen im Theme: .hsp-galerie { --hsp-accent: …; --hsp-card: …; --hsp-gap: …; }
* Vorlage ins Theme kopieren: <theme>/hamscanpi-sstv/galerie.php
* Filter: hsp_sstv_item, hsp_sstv_insert_status

== Datenschutz ==
Übertragen werden Bild, Satelliten- und Empfangsangaben, Rufzeichen, Locator, Stationsname und Werte des
Raspberry Pi (Temperatur, Last, Speicher). Keine IP- oder MAC-Adressen, kein WLAN, keine Zugangsdaten. Station
und Pi-Werte lassen sich in den Einstellungen ausblenden.

== Änderungen ==
= 1.3.0 =
* Anordnung einstellbar: Reihenfolge, Gruppierung (Tag, Quelle, Überflug, keine), Zeitzone, Filterknöpfe.
* Je Bild: Empfangszeit, Quelle und „hervorheben“ bearbeiten.
* Von Hand nachgereichte Bilder erkennbar, auf Wunsch immer als Entwurf.
* „Weitere Bilder laden“ erscheint nur noch, wenn es weitere Bilder gibt; beim Herunterscrollen wird von selbst nachgeladen.
= 1.2.0 =
* Seitenkopf wie die übrigen Seiten, Farben vom Theme, Zeiten in der Zeit der Station mit Kennung, Protokoll.
