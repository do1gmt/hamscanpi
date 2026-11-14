# SSTV-Bilder auf die eigene Webseite (WordPress)

HamScanPi lädt jedes SSTV-Bild – vom Satelliten, von Kurzwelle oder aus Live – mit allen Angaben auf eine
WordPress-Seite. Dort zeigt das Plugin **HamScanPi SSTV-Galerie** die Bilder nach Tag gruppiert, mit Filter je
Satellit und einer Lightbox (Durchblättern mit Pfeiltasten oder Wischen, Überblenden, Infotafel).

## Einrichten

1. **Plugin holen:** Dashboard → Einstellungen → Webseite → „herunterladen“ (`hamscanpi-sstv-galerie.zip`).
2. **In WordPress:** Plugins → Installieren → Plugin hochladen → ZIP wählen → aktivieren.
3. **Anwendungspasswort:** WordPress → Benutzer → Profil → *Anwendungspasswörter* → Name „HamScanPi“ →
   „Neues Anwendungspasswort hinzufügen“. Das Passwort wird nur einmal angezeigt – gleich kopieren.
   Es lässt sich dort jederzeit widerrufen; das eigentliche WordPress-Passwort braucht der Pi nicht.
4. **In HamScanPi:** Einstellungen → Webseite: Adresse (`https://…`), WordPress-Benutzer, Anwendungspasswort,
   „SSTV-Bilder hochladen“ an → „Verbindung prüfen“ → „Testbild senden“.
5. **Galerie-Seite:** WordPress → Einstellungen → SSTV-Galerie → *Galerie-Seite anlegen*. Die Seite bekommt
   denselben Seitenkopf wie die übrigen Seiten des Themes (Bereich `page-hero`: kleine Überschrift, großer Titel,
   Einleitung) und darunter die Galerie; als Seitenvorlage die der übrigen Seiten wählen (beim DO1GMT-Theme
   „DO1GMT Editierbare Theme-Seite“). Danach ins Menü aufnehmen (Design → Menüs), z. B. unter „Satelliten“.
   Von Hand geht es auch: irgendeine Seite mit dem Kurzcode `[sstv_galerie]`; möglich ist
   `[sstv_galerie quelle="iss-zarya" pro_seite="48" statistik="nein"]`.
6. **Freigeben:** Neue Bilder kommen zunächst als Entwurf (Menü *SSTV-Bilder*). Das Bild prüfen,
   „Veröffentlichen“ – oder in der Liste mehrere auf einmal. Läuft alles, in HamScanPi „sofort veröffentlichen“
   wählen.

## Was übertragen wird

Alle Zeiten in der Zeit, die in HamScanPi unter Einstellungen → Station → *Zeitangaben* eingestellt ist (UTC oder
Ortszeit des Pi), jeweils mit Kennung (UTC, MEZ, MESZ …). Die Galerie gruppiert die Tage in derselben Zeit.

| Abschnitt | Inhalt |
|---|---|
| Bild | SSTV-Modus, Anteil der Zeilen, Empfangszeit |
| Satellit und Überflug | Name, NORAD-Nummer, Soll- und Empfangsfrequenz, Doppler, Aufgang/höchster Punkt/Untergang (Zeit, Richtung, Elevation), beim Bild Elevation, Richtung, Entfernung, Bewegung, Alter der Bahndaten |
| Empfang | Signal beim Bild, Spitze und 90-%-Wert im Überflug, Zeit über 6 dB, Bilddauer, Aufnahmezeit, Empfänger, Verstärkung, ppm, Abstimmung und Abtastrate, Antenne |
| Station | Rufzeichen, Locator, Stationsname (welcher Pi gemeldet hat), HamScanPi-Version |
| Raspberry Pi beim Empfang | Modell, System, CPU-Temperatur, Last, freier Speicher, Unterspannung, Laufzeit |

Kurzwelle und Live bringen statt der Satellitenangaben Band, Frequenz, Seitenband und Pegel mit.
**Nie übertragen:** IP- und MAC-Adressen, WLAN, Zugangsdaten. Station und Pi-Werte lassen sich in WordPress
unter Einstellungen → SSTV-Galerie ausblenden.

Bilder unter der eingestellten Vollständigkeit (Vorgabe 60 % der Zeilen) bleiben auf dem Pi. Ohne Internet
merkt sich der Pi die Bilder und schickt sie später (Wartezeit bis 1 h, nach 14 Tagen aufgegeben). Ein
Live-Bild geht erst, wenn es 90 s nicht mehr gewachsen ist. Jedes Bild hat eine feste Kennung – ein
wiederholter Versuch legt in WordPress kein zweites an.

## Von Hand nachreichen

In jeder SSTV-Trefferkarte (Treffer → SSTV, Karte öffnen) steht neben „Bild herunterladen“ der Knopf
**↑ Auf Webseite**. Er schickt das Bild mit den Angaben aus der Datenbank: Satellit, Frequenz, Signal, Antenne,
Station; beim Satelliten mit Überflug (Aufgang, höchster Punkt, Untergang, Doppler), solange die Bahndaten zum
Tag des Bildes passen (bis 30 Tage). Pi-Werte fehlen hier bewusst – die von jetzt sagen über den Empfang nichts.

- Geht auch, wenn das automatische Hochladen aus ist und unter der Mindest-Vollständigkeit – du entscheidest.
- Gleiche Kennung wie beim automatischen Hochladen: ein Bild steht nie doppelt auf der Seite.
- Danach zeigt die Karte den Zustand: *wird in der nächsten Minute hochgeladen*, *auf der Webseite · ansehen*,
  *als Entwurf · in WordPress freigeben*, oder bei einem Fehler den Grund und *erneut versuchen*.
- In WordPress: Einstellungen → SSTV-Galerie → *Von Hand nachgereicht*: wie automatische oder immer als Entwurf.
  In der Liste der SSTV-Bilder sind sie mit „von Hand nachgereicht“ markiert.

## Anordnung in der Galerie

Einstellungen → SSTV-Galerie → **Anordnung**:

- **Reihenfolge:** neueste oder älteste zuerst.
- **Gruppieren:** nach Tag, nach Quelle (Satellit, Band), nach Überflug (Bilder einer Quelle mit weniger als
  20 min Abstand) oder gar nicht.
- **Datum und Uhrzeit in:** wie in HamScanPi eingestellt (UTC oder Ortszeit der Station), immer UTC oder die
  Zeitzone der WordPress-Seite – immer mit Kennung.
- **Filterknöpfe je Quelle:** nach Anzahl, alphabetisch oder ausblenden.

Je Bild (SSTV-Bilder → Bild bearbeiten → *Einordnung in der Galerie*): **Empfangszeit** (UTC), **Quelle** und
**hervorheben** (steht zusätzlich oben unter „★ Hervorgehoben“). Die Galerie sortiert sofort neu. Quellen
umbenennen: SSTV-Bilder → Quellen.

Weitere Bilder lädt die Galerie beim Herunterscrollen von selbst; der Knopf *Weitere Bilder laden (noch …)*
erscheint nur, wenn es wirklich noch Bilder gibt.

## Protokoll

- **WordPress:** Einstellungen → SSTV-Galerie → *Protokoll*: die letzten 100 Empfänge mit Zeit (UTC), Ergebnis
  (Entwurf, veröffentlicht, schon vorhanden, abgelehnt mit Grund), Quelle und Benutzer.
- **HamScanPi:** Einstellungen → Webseite → *Zuletzt* (mit Links zum Ansehen und Freigeben), im Protokoll die
  Ereignisse `sstv_uploaded`, `sstv_upload_failed`, `sstv_manual_queued`, `publish_set`.

## Aussehen anpassen

Ohne eigene Angaben übernimmt die Galerie die Farben des Themes, wenn es die üblichen CSS-Variablen setzt
(`--green` bzw. `--accent`, `--panel`, `--line`, `--radius`, `--ink`, `--muted`, `--shadow` – das DO1GMT-Theme tut
das), sonst Schrift und Hintergrund der Seite. Die Lightbox bleibt dunkel (Bilder wirken so am besten), Akzente
in der Farbe des Themes.

- **Einstellungen → SSTV-Galerie:** Akzentfarbe, Kartenhintergrund, Schriftfarben, Ecken, Größe der
  Vorschaubilder, Überblenddauer, Bilder je Seite, eigenes CSS; Plugin-CSS ganz aus (dann gestaltet das Theme).
- **CSS-Variablen** im Theme: `.hsp-galerie { --hsp-accent: …; --hsp-card: …; --hsp-text: …; --hsp-muted: …;
  --hsp-radius: …; --hsp-thumb: …; --hsp-gap: …; --hsp-fade: …; --hsp-font: …; }`, Lightbox: `.hsp-lb { --hsp-lb-bg: …; }`.
  Leere Farben übernehmen Schrift und Hintergrund des Themes.
- **Vorlage:** `templates/galerie.php` ins Theme kopieren nach `<theme>/hamscanpi-sstv/galerie.php`.
- **Filter:** `hsp_sstv_item` (Daten eines Bildes vor der Ausgabe), `hsp_sstv_insert_status` (Status beim Empfang).

## Schnittstelle

`/wp-json/hsp-sstv/v1/ping` (GET, angemeldet), `/images` (POST multipart: `image`, `meta` JSON, `uid`, `status`, `manual`;
angemeldet, Recht „Dateien hochladen“), `/gallery?page=&per_page=&quelle=` (GET, öffentlich, nur veröffentlichte).
