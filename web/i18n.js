/* HamScanPi – Sprache der Oberfläche (Deutsch / English).
 *
 * Das Dashboard ist auf Deutsch geschrieben. Für Englisch wird jeder sichtbare Text beim Einfügen
 * übersetzt: ganze Texte über das Wörterbuch D, Textteile mit eingesetzten Werten (Zeiten, Zahlen,
 * Namen) über die Muster P. Ein MutationObserver übersetzt auch alles, was app.js später einfügt.
 * Elemente mit der Klasse .mono oder .no-i18n (Dekodierungen, Rohdaten) bleiben unverändert.
 *
 * Die Wahl gilt je Browser (localStorage „lang“); ohne Wahl entscheidet die Browsersprache.
 * Fehlt eine Übersetzung, bleibt der deutsche Text stehen – Ergänzungen gern als Pull Request.
 */
(function () {
  'use strict';
  const stored = localStorage.getItem('lang');
  const LANG = stored === 'en' || stored === 'de' ? stored : ((navigator.language || 'de').toLowerCase().startsWith('de') ? 'de' : 'en');
  document.documentElement.lang = LANG;

  const D = {
    // Kopf, Bereiche
    'Verbinde …': 'Connecting …', 'Lokal': 'local', 'Bereiche': 'Sections', 'Treffer': 'Hits', 'Protokoll': 'Log',
    'Einstellungen': 'Settings', 'Live': 'Live', 'Scan': 'Scan',
    // Treffer
    'gespeicherte Treffer': 'stored hits', 'neu im letzten Lauf': 'new in last run', 'Treffer letzter Scan': 'hits last scan',
    'verworfen': 'rejected', 'Betriebsart': 'Mode', 'Alle': 'All', 'Sortierung': 'Sort', 'Neu → alt': 'New → old',
    'Alt → neu': 'Old → new', 'Frequenz': 'Frequency', 'Stärkstes Signal': 'Strongest signal', 'Bester Ton zuerst': 'Best audio first',
    'Suche (MHz oder Notiz)': 'Search (MHz or note)', '7.14 oder DL1…': '7.14 or DL1…', 'nur mit Ton': 'audio only', 'nur ★': '★ only',
    'Keine Treffer für diese Auswahl.': 'No hits for this selection.', '🔊 Ton': '🔊 audio', 'nur Träger': 'carrier only', 'neu': 'new',
    '★ geschützt': '★ protected', 'Löschen': 'Delete', '▶ Jetzt live hören': '▶ Listen live now', 'Schützen': 'Protect',
    'Schutz aufheben': 'Unprotect', 'Treffer wird behalten': 'Hit will be kept', 'Gelöscht': 'Deleted', 'Notiz gespeichert': 'Note saved',
    'Notiz': 'Note', 'Notiz / Rufzeichen': 'Note / callsign', '★ Behalten': '★ Keep', '☆ Schutz aufheben': '☆ Unprotect', 'Bedeutung': 'Meaning', 'Nachricht': 'Message', 'Nachrichten': 'messages', 'Empfang': 'Reception',
    'Ausgabe': 'Output', 'ohne DNR': 'no DNR', 'DNR mittel': 'DNR medium', 'DNR stark': 'DNR strong', 'leer': 'empty',
    'Unbekannt': 'Unknown', 'Sprache': 'Voice', 'Bake': 'Beacon', 'Satellit': 'Satellite', 'Daten': 'Data', 'Dauerträger': 'Carrier',
    'Störung/Breitband': 'Noise/wideband', 'UKW-Rundfunk': 'FM broadcast', 'Relais': 'Repeater', 'Pager': 'Pager', 'Digitalfunk': 'Digital voice',
    '↓ Audio so speichern, wie es gerade klingt': '↓ Save audio as it sounds now', '↓ LSB': '↓ LSB', '↓ USB': '↓ USB',
    'Rohdaten, Stereo I/Q – für SDR-Software, nicht zum Anhören': 'Raw data, stereo I/Q – for SDR software, not for listening',
    'Digitalmodi werden wie am Funkgerät in USB abgehört. Liegt der Ton außerhalb von 300–3000 Hz, die Abstimmung verschieben.':
      'Digital modes are monitored in USB, as on a radio. If the tone is outside 300–3000 Hz, shift the tuning.',
    'Klingt die Stimme zu hoch/tief („Donald Duck“), die Abstimmung verschieben – grob mit ±500/±100, fein mit ±10 Hz. Bei doppeltem Signal das Seitenband wechseln.':
      'If the voice sounds too high or low ("Donald Duck"), shift the tuning – coarse with ±500/±100, fine with ±10 Hz. If you hear the signal twice, switch the sideband.',
    'AM (Flugfunk, CB). Rauscht es nur, ±5 kHz probieren – der Treffer sitzt nicht immer auf der Kanalmitte.':
      'AM (airband, CB). If you only hear noise, try ±5 kHz – the hit is not always on the channel centre.',
    'Schmales FM (Relais, Simplex, PMR, Pager, Digitalfunk als Rohton). Bei Digitalfunk hörst du nur das Rauschen des Signals – das ist normal.':
      'Narrow FM (repeaters, simplex, PMR, pager, digital voice as raw audio). With digital voice you only hear the noise of the signal – that is normal.',
    // Scan
    'Zu den Treffern': 'Go to hits', 'Treffer 24 h': 'Hits 24 h', 'Scans 24 h': 'Scans 24 h', 'Scanzeit 24 h': 'Scan time 24 h',
    'Zur Überflugliste': 'Go to pass list', 'Überflüge aufgenommen': 'Passes recorded', 'Überflüge 24 h': 'Passes 24 h',
    'SSTV-Bilder anzeigen': 'Show SSTV images', 'SSTV-Bilder 24 h': 'SSTV images 24 h', 'nächster Überflug': 'next pass',
    'kein Überflug geplant': 'no pass planned', 'läuft': 'running', 'Bereit': 'Ready', 'Letzter Lauf': 'Last run', 'Dauer': 'Duration',
    'Nächster Scan': 'Next scan', 'Angeschlossenes Gerät': 'Connected receiver', 'Automatisch (Start zu Start)': 'Automatic (start to start)',
    'Jetzt scannen': 'Scan now', 'Scan abbrechen': 'Stop scan', 'Scan läuft …': 'Scan running …', 'aus': 'off', 'jede Stunde': 'every hour',
    'nicht angeschlossen': 'not connected', 'Gesamtes Band': 'Whole band', 'weitere': 'more', 'Scan angefordert': 'Scan requested',
    'Scan startet': 'Scan starting', 'Bänder werden abgesucht': 'Scanning bands', 'Aufnahmen werden ausgewertet': 'Analysing recordings',
    'FM/VHF-Aufnahmen': 'FM/VHF recordings', 'Signale werden dekodiert': 'Decoding signals', 'FT8 wird dekodiert': 'Decoding FT8',
    'Abschluss': 'Finishing', 'Scan läuft': 'Scan running', 'Scan abgeschlossen': 'Scan finished', 'Fehler beim Scan': 'Scan error',
    'Server nicht erreichbar': 'Server not reachable', 'läuft gerade': 'running now', 'fällig – startet gleich': 'due – starting soon',
    'Scan fertig': 'Scan finished', 'Scan wird vorbereitet': 'Preparing scan', 'Treffer werden ausgewertet': 'Analysing hits',
    'FM-Aufnahmen werden erzeugt': 'Creating FM recordings', 'VHF/UHF-Aufnahmen werden ausgewertet': 'Analysing VHF/UHF recordings',
    'Bekannte Signale werden dekodiert': 'Decoding known signals', 'FT8-Nachrichten werden dekodiert': 'Decoding FT8 messages',
    'Ergebnisse werden bereitgestellt': 'Preparing results', 'Scan vom Benutzer abgebrochen': 'Scan cancelled by user',
    'Scanlauf wurde unterbrochen': 'Scan run was interrupted', 'Live-Empfang': 'Live reception',
    'Scan abgeschlossen – Treffer aktualisiert': 'Scan finished – hits updated', 'Laufenden Scan abbrechen?': 'Stop the running scan?',
    'Scan wird abgebrochen …': 'Stopping scan …', 'Mindestens ein Band auswählen': 'Select at least one band', 'Scan läuft bereits': 'Scan already running',
    '✓ Scan direkt gestartet (Scheduler-Dienst läuft nicht!)': '✓ Scan started directly (scheduler service not running!)',
    '✓ Scan angefordert': '✓ Scan requested', '✓ gespeichert': '✓ saved', '✓ Locator gespeichert': '✓ Locator saved',
    '✓ gespeichert – gilt ab dem nächsten Scan': '✓ saved – applies from the next scan',
    '✓ gespeichert – der Plan wird neu berechnet': '✓ saved – the plan is being recalculated',
    'Antippen zum Ein-/Ausschalten. Änderungen werden sofort gespeichert und beim nächsten Scan verwendet.':
      'Tap to switch on/off. Changes are saved immediately and used from the next scan.',
    'Bänder & Betriebsarten': 'Bands & modes', 'Gerätetelemetrie (ISM) – nur eigene Geräte auswerten': 'Device telemetry (ISM) – only evaluate your own devices',
    'Eingeschränkt: Mithören in vielen Ländern verboten (z. B. Deutschland, § 5 TDDDG) – nur einschalten, wo es erlaubt ist.':
      'Restricted: monitoring is forbidden in many countries (e.g. Germany, § 5 TDDDG) – only enable where it is allowed.',
    'Kein Band für dieses Gerät gewählt – der Scanner hätte nichts zu tun.': 'No band selected for this receiver – the scanner would have nothing to do.',
    'Satelliten-Überflüge': 'Satellite passes', 'Satellit · Kanäle': 'Satellite · channels', 'Zeit (Ortszeit)': 'Time (local)', 'Zeit (UTC)': 'Time (UTC)', 'Stufe': 'Level', 'Quelle': 'Source',
    'Höhe': 'Elev.', 'Aufnahme': 'Recording', 'vergangene zeigen': 'show past', 'alle 24 h': 'all 24 h', 'gemeinsam': 'shared',
    'übersprungen': 'skipped', 'voll': 'full', 'wenn Stick frei': 'if stick is free', 'keine Überflüge': 'no passes',
    'aufgenommen': 'recorded', 'nimmt auf': 'recording', 'wird ausgewertet': 'analysing', 'Auswertung nicht beendet': 'analysis did not finish', 'Aufnahme nicht beendet': 'recording did not finish', 'nicht aufgenommen': 'not recorded',
    'In den nächsten 24 h kein Überflug über 10° Höhe.': 'No pass above 10° elevation in the next 24 h.',
    'Aufnahme startet 15 s vor dem Aufgang. Überschneiden sich Überflüge, entscheidet die Punktzahl (Priorität, Höhe, Wartezeit, Aktivität – einstellbar unter Einstellungen). Passen die Frequenzen in ein Empfangsfenster, werden die Satelliten gemeinsam aufgenommen.':
      'Recording starts 15 s before AOS. If passes overlap, the score decides (priority, elevation, waiting time, activity – adjustable under Settings). If the frequencies fit into one receive window, the satellites are recorded together.',
    'Unter „Bänder & Betriebsarten“ das Band „Satelliten“ einschalten – dann nimmt der RTL-SDR jeden Überflug automatisch auf (Doppler-Korrektur, SSTV und APRS werden dekodiert). Die mit „SSTV ·“ markierten Bildsatelliten senden nur gelegentlich; sie werden nur aufgenommen, wenn der Stick frei ist.':
      'Switch on the band "Satelliten" under "Bands & modes" – the RTL-SDR then records every pass automatically (Doppler correction, SSTV and APRS are decoded). The image satellites marked "SSTV ·" transmit only occasionally; they are only recorded when the stick is free.',
    'Scan-Verlauf': 'Scan history', 'Start (UTC)': 'Start (UTC)', 'Bänder': 'Bands', 'Status': 'Status', 'Treffer / verworfen': 'Hits / rejected',
    'Abstimmungen': 'Tunings', 'Abst.': 'Tun.', 'Pro Seite': 'Per page', 'noch kein Lauf': 'no run yet', 'ok': 'ok', 'Fehler': 'Error',
    'abgebrochen': 'aborted', 'vom Benutzer oder für einen Überflug abgebrochen': 'cancelled by the user or for a satellite pass',
    // Live
    'Live hören': 'Listen live', 'Frequenz (MHz)': 'Frequency (MHz)', 'Demodulator': 'Demodulator', 'FM schmal': 'FM narrow', 'FM breit': 'FM wide',
    'Verstärkung': 'Gain', 'wie Scan': 'as scan', 'automatisch': 'automatic', 'Rauschsperre / Suchlauf-Schwelle': 'Squelch / seek threshold',
    'Hören': 'Listen', 'Stopp': 'Stop', 'Suchlauf abwärts ab der eingestellten Frequenz': 'Seek down from the set frequency',
    '◀◀ Suchlauf': '◀◀ Seek', 'Suchlauf aufwärts ab der eingestellten Frequenz': 'Seek up from the set frequency', 'Suchlauf ▶▶': 'Seek ▶▶',
    'einfacher Player (falls kein Ton kommt)': 'simple player (if there is no audio)', 'Zuletzt gehört': 'Recently heard', 'noch nichts': 'nothing yet',
    'Suchlauf: sucht ab der eingestellten Frequenz im gewählten Raster und Demodulator (für UKW-Radio „FM breit“, Raster wird dann 100 kHz), bleibt auf einem Signal stehen, solange es belegt ist, und sucht danach weiter. Anhalten: ◀/▶ oder Hören drücken. Der Regler ist im Suchlauf die Schwelle (0 = 10 dB).':
      'Seek: searches from the set frequency in the chosen step and demodulator (for FM radio use "FM wide", the step is then 100 kHz), stays on a signal while it is active and then continues. Stop: press ◀/▶ or Listen. During seek the slider is the threshold (0 = 10 dB).',
    'Tipp: Auf jeder Trefferkarte bringt dich „▶ Jetzt live hören“ direkt hierher.': 'Tip: on every hit card, "▶ Listen live now" brings you straight here.',
    'Browser lässt kein Audio zu – „einfacher Player“ anhaken und nochmal Hören drücken': 'The browser blocks audio – tick "simple player" and press Listen again',
    // Protokoll
    'Stufe ab': 'Level from', 'Warnung': 'Warning', 'Komponente': 'Component', 'alle': 'all', 'Suche': 'Search', 'Text': 'Text',
    'Wiederholungen zusammenfassen': 'Group repeats', 'Laden': 'Load', '◀ neuere': '◀ newer', 'ältere ▶': 'older ▶',
    'Scan-Ausgabe (roh, letzte Zeilen)': 'Scan output (raw, last lines)', 'keine Einträge': 'no entries', 'Ereignis': 'Event',
    'Scan gestartet': 'Scan started', 'Scan fehlgeschlagen': 'Scan failed', 'Scan abgebrochen': 'Scan cancelled', 'Scan unterbrochen': 'Scan interrupted',
    'Scan wird gestartet': 'Starting scan', 'Scan verschoben': 'Scan postponed', 'Scan beendet': 'Scan stopped', 'Scan-Anforderung ignoriert': 'Scan request ignored',
    'Band wird abgesucht': 'Scanning band', 'Band übersprungen': 'Band skipped', 'Empfänger geöffnet': 'Receiver opened',
    'Empfänger lässt sich nicht öffnen': 'Cannot open receiver', 'RTL-SDR liefert keine Daten': 'RTL-SDR delivers no data', 'RTL-SDR läuft wieder': 'RTL-SDR running again',
    'Überflug: Aufnahme startet': 'Pass: recording starts', 'Überflug übersprungen': 'Pass skipped', 'Satelliten: Problem': 'Satellites: problem',
    'Satelliten: Fehler': 'Satellites: error', 'Satelliten pausiert (PMSDR gewählt)': 'Satellites paused (PMSDR selected)', 'Satelliten: Hinweis': 'Satellites: note', 'Überflug: Aufnahme läuft': 'Pass: recording',
    'Überflug fertig': 'Pass finished', 'Überflug fehlgeschlagen': 'Pass failed', 'Überflug: Auswertung fehlgeschlagen': 'Pass: analysis failed',
    'Überflug: Aufnahme gestört': 'Pass: recording disturbed', 'Scheduler: Fehler in der Schleife': 'Scheduler: error in main loop',
    'Kanalbeobachtung fehlgeschlagen': 'Channel watch failed', 'Sprachbeobachtung fehlgeschlagen': 'Voice watch failed',
    'Live-Empfang gestartet': 'Live reception started', 'Live-Empfang beendet': 'Live reception stopped', 'Mail verschickt': 'Mail sent',
    'Mail fehlgeschlagen': 'Mail failed', 'Scan-Zusammenfassung': 'Scan summary', 'Auswertung': 'Analysis', 'Dekodierung fertig': 'Decoding finished',
    'Lauf': 'Run', 'Grund': 'Reason', 'Kanäle': 'Channels', 'max. Höhe°': 'max. elev.°', 'Gerät': 'Receiver', 'Problem': 'Problem',
    'Intervall min': 'Interval min', 'Sekunden': 'Seconds', 'Mitte': 'Centre', 'bis': 'until', 'je Kanal': 'per channel', 'Plan': 'Plan',
    'Dauer s': 'Duration s', 'Band abgehört': 'Band monitored', 'abgehört (MHz)': 'monitored (MHz)', 'Fenstermitten (MHz)': 'window centres (MHz)', 'Segmente': 'Segments', 'Digital (MHz)': 'Digital (MHz)', 'Frequenzen (MHz)': 'Frequencies (MHz)', 'Zeit (UTC)': 'Time (UTC)',
    // Einstellungen
    'Station': 'Station', 'Mein Locator (für Entfernung und Richtung bei FT8/FT4)': 'My locator (for distance and bearing with FT8/FT4)',
    'z. B. JO31AB': 'e.g. JO31AB', 'Sprache / Language': 'Sprache / Language', 'Frequenzraster': 'Scan raster',
    'Abstand der Empfangsfenster beim Absuchen ganzer Bänder. Feiner = die Fenster überlappen mehr, an den Fensterrändern und in der Mitte (Gleichanteil) entgehen weniger Signale – der Scan dauert dafür länger. Einzelkanäle (PMR, Freenet, Relais) sind davon nicht betroffen.':
      'Spacing of the receive windows when scanning whole bands. Finer = the windows overlap more, fewer signals are missed at the window edges and in the centre (DC) – the scan takes longer. Single channels (PMR, Freenet, repeaters) are not affected.',
    'PMSDR (Kurzwelle)': 'PMSDR (shortwave)', 'normal': 'normal', 'fein (¾ Schritt)': 'fine (¾ step)', 'sehr fein (½ Schritt)': 'very fine (½ step)',
    'Satelliten-Prioritäten': 'Satellite priorities', 'Kanal': 'Channel', 'Priorität': 'Priority', 'Zuletzt (14 Tage)': 'Last (14 days)',
    '0 – nie': '0 – never', 'immer zuerst': 'always first',
    'Überschneiden sich zwei Überflüge, gewinnt die höhere Punktzahl: Priorität × 10, dazu bis 15 Punkte für die Höhe des Überflugs und bis 15 Punkte für die Wartezeit seit der letzten Aufnahme (1 Punkt je 4 Stunden). Abgezogen werden bis 15 Punkte, wenn ein Satellit in den letzten 14 Tagen mehrmals ohne jedes Signal aufgenommen wurde – ein einziger Treffer hebt das wieder auf. Passen die Frequenzen in ein Empfangsfenster, werden beide gemeinsam aufgenommen. 0 = nie aufnehmen, „immer zuerst“ schlägt alles.':
      'If two passes overlap, the higher score wins: priority × 10, plus up to 15 points for the elevation of the pass and up to 15 points for the time since the last recording (1 point per 4 hours). Up to 15 points are deducted if a satellite was recorded several times in the last 14 days without any signal – a single hit cancels that. If the frequencies fit into one receive window, both are recorded together. 0 = never record, "always first" beats everything.',
    'Aufräumen': 'Clean-up', 'Älter als': 'Older than', '1 Tag': '1 day', '3 Tage': '3 days', '7 Tage': '7 days', '14 Tage': '14 days', '30 Tage': '30 days',
    'Prüfen': 'Check', 'Alte löschen': 'Delete old', 'Alle ungeschützten Treffer löschen': 'Delete all unprotected hits',
    'Geschützte Treffer (★) werden nie automatisch gelöscht.': 'Protected hits (★) are never deleted automatically.', 'Löschprotokoll': 'Deletion log',
    'Wirklich ALLE ungeschützten Treffer samt Aufnahmen löschen?': 'Really delete ALL unprotected hits including recordings?',
    'Benachrichtigungen (Mail)': 'Notifications (mail)', 'Mailversand aktiv': 'Mail enabled', 'SMTP-Server': 'SMTP server', 'Verschlüsselung': 'Encryption',
    'keine': 'none', 'Benutzer': 'User', 'Passwort': 'Password', 'Absender': 'From', 'Empfänger': 'To', 'pi@example.de': 'pi@example.org',
    'ich@example.de': 'me@example.org', 'smtp.example.de': 'smtp.example.org', 'Mail bei Fehlern': 'Mail on errors', 'höchstens eine je': 'at most one per',
    'Mail nach jedem Scan': 'Mail after every scan', 'ab': 'from', 'Treffern, fehlgeschlagene Scans immer': 'hits, failed scans always',
    'Speichern': 'Save', 'Testmail senden': 'Send test mail', 'sende …': 'sending …', '✓ Testmail verschickt': '✓ Test mail sent',
    'Das Passwort liegt im Klartext in config/web_settings.json auf dem Pi – ein eigenes Mailkonto oder App-Passwort verwenden.':
      'The password is stored in plain text in config/web_settings.json on the Pi – use a separate mail account or an app password.',
    // Bänder und Segmente
    'Satelliten': 'Satellites', 'Rundfunk': 'Broadcast', 'Wettersat': 'Weather sat', 'Flugfunk': 'Airband',
    'UKW-Rundfunk (breites FM, RDS)': 'FM broadcast (wide FM, RDS)', 'Sprache USB': 'Voice USB', 'Sprache LSB': 'Voice LSB',
    'Kanalraster 8,33/25 kHz': 'Channel raster 8.33/25 kHz', 'nur bei Sonne aktiv': 'only active in sunlight', 'Akku schwach – nur bei Sonne': 'weak battery – sunlight only',
    'Transponder nach Fahrplan': 'transponder on schedule', 'nach Fahrplan aktiv, schwächelt': 'active on schedule, weakening',
    'nur aktiv, wenn jemand den 10-min-Zeitschalter auslöst': 'only active when someone triggers the 10-min timer',
    'FM-Relais ohne festen Fahrplan, sonst Telemetrie': 'FM repeater without fixed schedule, otherwise telemetry',
    'Robot 36, häufigster SSTV-Cubesat': 'Robot 36, most frequent SSTV CubeSat', 'auch SSDV (wird nicht dekodiert)': 'also SSDV (not decoded)',
    'Martin 1, nach Fahrplan der Uni Würzburg; Bahn sinkt': 'Martin 1, on the schedule of Würzburg University; orbit decaying',
    'ARISS-SSTV seit 2026 meist hier, Robot 36': 'ARISS SSTV mostly here since 2026, Robot 36',
    'Handfunkgeräte ohne Lizenz, 10 mW, 25-kHz-Raster': 'licence-free handhelds, 10 mW, 25 kHz raster',
    'DMR/C4FM/D-STAR: Spektrum + Rohton': 'DMR/C4FM/D-STAR: spectrum + raw audio',
    'Nur hörbar bei Überflug; Bild braucht ~10 min Aufnahme': 'only audible during a pass; an image needs ~10 min of recording',
    'Sprache / Language:': 'Sprache / Language:',
    // Nachträge aus den Screenshots
    'AX.25 AFSK 1200 Bd – Dekodierung mit multimon-ng': 'AX.25 AFSK 1200 Bd – decoded with multimon-ng', 'starte …': 'starting …',
    'Gerade läuft ein Scan oder eine Satellitenaufnahme. Abbrechen und live hören?': 'A scan or satellite recording is running. Stop it and listen live?',
    'Gerade läuft ein Scan – bitte warten oder abbrechen lassen': 'A scan is running – please wait or stop it',
    'Live-Empfang abgestimmt': 'Live reception tuned', 'Fehler bei einer Anfrage': 'Request error', 'Für Live-Empfang abgebrochen': 'Stopped for live reception',
    // PSK Reporter
    'FT8/FT4-Spots an PSK Reporter melden': 'Report FT8/FT4 spots to PSK Reporter', 'Gehörte FT8/FT4-Stationen erscheinen dann alle 5 Minuten auf': 'Heard FT8/FT4 stations then appear every 5 minutes on',
    '– mit deinem Rufzeichen als Empfänger. Braucht Rufzeichen und Locator.': '– with your callsign as receiver. Needs callsign and locator.',
    '✓ PSK Reporter an – erste Spots nach etwa 5 min': '✓ PSK Reporter on – first spots after about 5 min', '✓ PSK Reporter aus': '✓ PSK Reporter off',
    'An PSK Reporter gemeldet': 'Reported to PSK Reporter', 'PSK Reporter: Fehler': 'PSK Reporter: error',
    'Für PSK Reporter zuerst Rufzeichen und Locator eintragen': 'For PSK Reporter enter callsign and locator first',
    // Karte, Aktivität
    'Aufnahme abbrechen': 'Stop recording', 'Aufnahme wird abgebrochen …': 'Stopping recording …',
    'Satellitenaufnahme abbrechen? Bisher Aufgenommenes wird noch ausgewertet.': 'Stop the satellite recording? What has been recorded so far is still analysed.',
    'Der Überflug wird gerade ausgewertet – der Empfänger ist schon frei.': 'The pass is being analysed – the receiver is already free.',
    'Überflug abgebrochen (Benutzer)': 'Pass stopped (user)',
    'Wasserfall – antippen zum Abstimmen': 'Waterfall – tap to tune',
    'Karte': 'Map', 'Kartenhintergrund: OpenStreetMap (braucht Internet im Browser). Nur Stationen mit Locator.': 'Map background: OpenStreetMap (needs internet in the browser). Only stations with a locator.',
    'Karte nicht verfügbar – der Browser erreicht cdnjs.cloudflare.com bzw. OpenStreetMap nicht.': 'Map not available – the browser cannot reach cdnjs.cloudflare.com or OpenStreetMap.',
    'Aktivität': 'Activity', '90 Tage': '90 days', 'Noch keine Daten.': 'No data yet.', 'Wochentag und Stunde (alle Bänder)': 'Weekday and hour (all bands)',
    'Wann auf welchem Band etwas zu hören war – Treffer je Abstimmung, damit die Zahl der Scans das Bild nicht verfälscht. Stunden in UTC.':
      'When there was something to hear on which band – hits per tuning, so the number of scans does not distort the picture. Hours in UTC.',
    // Zeitpläne
    'Zeitpläne': 'Schedules', 'Zeitplan hinzufügen': 'Add schedule', 'Name': 'Name', 'von': 'from', 'z. B. Nacht': 'e.g. Night',
    'Zeitplan speichern': 'Save schedule', 'Noch kein Zeitplan.': 'No schedule yet.', 'täglich': 'daily', 'aktiv': 'active', '✓ Zeitplan gespeichert': '✓ Schedule saved',
    'Mo': 'Mon', 'Di': 'Tue', 'Mi': 'Wed', 'Do': 'Thu', 'Fr': 'Fri', 'Sa': 'Sat', 'So': 'Sun',
    'Zu bestimmten Zeiten andere Bänder scannen, z. B. nachts 80 und 40 m. Solange ein Zeitplan gilt, ersetzt er die Auswahl oben (auch „Satelliten“). Er gilt nur, wenn er Bänder des gewählten Geräts enthält.':
      'Scan other bands at certain times, e.g. 80 and 40 m at night. While a schedule applies it replaces the selection above (including "Satellites"). It only applies if it contains bands of the selected receiver.',
    'Zeitplan: Uhrzeiten als HH:MM, Beginn und Ende verschieden': 'Schedule: times as HH:MM, start and end must differ', 'Zeitplan: mindestens ein Band wählen': 'Schedule: choose at least one band',
    // Push
    'Benachrichtigungen': 'Notifications', 'Push aufs Handy': 'Push to your phone', 'Dienst': 'Service', 'ntfy-Adresse': 'ntfy address',
    'ntfy-Zugangstoken (optional)': 'ntfy access token (optional)', 'Telegram-Bot-Token': 'Telegram bot token', 'Telegram-Chat-ID': 'Telegram chat ID',
    'Wann melden': 'When to notify', 'bei Fehlern': 'on errors', 'nach jedem Scan': 'after every scan', 'Test-Push senden': 'Send test push',
    'per Mail/Push melden': 'notify by mail/push', '✓ Test-Push verschickt – kommt er an?': '✓ Test push sent – did it arrive?',
    'ntfy: App „ntfy“ aufs Handy, dort ein Thema abonnieren (am besten ein langer, zufälliger Name – wer ihn kennt, liest mit) und dieselbe Adresse hier eintragen.':
      'ntfy: install the ntfy app, subscribe to a topic (best a long random name – anyone who knows it can read along) and enter the same address here.',
    'Telegram: bei @BotFather einen Bot anlegen (Token), dem Bot eine Nachricht schreiben, die Chat-ID z. B. über @userinfobot erfahren.':
      'Telegram: create a bot with @BotFather (token), send the bot a message, get your chat ID e.g. from @userinfobot.',
    'Push ist nicht eingerichtet': 'Push is not set up',
    // Stationen, Beobachtungsliste
    'Stationen': 'Stations', 'Gehörte Stationen': 'Heard stations', 'Rufzeichen': 'Callsign', 'Zeitraum': 'Period', '1 Jahr': '1 year', 'alles': 'all',
    '↓ ADIF-Export': '↓ ADIF export', 'Locator': 'Locator', 'Entfernung': 'Distance', 'Zuletzt (UTC)': 'Last (UTC)', 'Anzahl Aussendungen': 'Number of transmissions',
    'DL1ABC oder DL*': 'DL1ABC or DL*', 'z. B. DL1ABC': 'e.g. DL1ABC', 'Mein Rufzeichen (für den ADIF-Export)': 'My callsign (for the ADIF export)',
    '✓ Rufzeichen gespeichert': '✓ Callsign saved',
    'Rufzeichen aus FT8/FT4, APRS, CW und RTTY – nur sendende Stationen. Entfernung und Richtung aus dem Locator der Station (FT8, APRS-Position) und deinem eigenen Locator.':
      "Callsigns from FT8/FT4, APRS, CW and RTTY – transmitting stations only. Distance and bearing from the station's locator (FT8, APRS position) and your own locator.",
    'Noch keine Stationen gehört – Rufzeichen kommen aus FT8/FT4, APRS, CW und RTTY.': 'No stations heard yet – callsigns come from FT8/FT4, APRS, CW and RTTY.',
    'ADIF ist das Austauschformat der Logbuchprogramme. Der Export ist ein SWL-Log (jede gehörte Aussendung ein Eintrag, SWL=Y) – mit deinem Rufzeichen und Locator aus den Einstellungen.':
      'ADIF is the exchange format of logbook programs. The export is an SWL log (one entry per heard transmission, SWL=Y) – with your callsign and locator from the settings.',
    'Beobachtungsliste': 'Watchlist', 'Art': 'Type', 'Frequenz (MHz)': 'Frequency (MHz)', 'Entfernung ab (km)': 'Distance from (km)', 'Ereignis': 'Event',
    'Wert': 'Value', 'Bezeichnung': 'Label', 'z. B. Relais DB0XYZ': 'e.g. repeater DB0XYZ', 'Toleranz (kHz)': 'Tolerance (kHz)', 'Ruhezeit (min)': 'Quiet time (min)',
    'per Mail melden': 'notify by mail', 'Hinzufügen': 'Add', 'Noch keine Beobachtungen.': 'No watches yet.', '✓ Beobachtung hinzugefügt': '✓ Watch added',
    'Beobachtungsliste': 'Watchlist', 'Entfernung ab': 'Distance from',
    'Meldet sich, sobald der Scanner etwas Bestimmtes hört – im Protokoll, mit 🔔 auf der Trefferkarte und, wenn Mail eingerichtet ist, per Mail (höchstens eine je Regel in der Ruhezeit).':
      'Reports as soon as the scanner hears something specific – in the log, with 🔔 on the hit card and, if mail is set up, by mail (at most one per rule within the quiet time).',
    'SSTV-Bild von der ISS': 'SSTV image from the ISS', 'SSTV-Bild (alle)': 'SSTV image (any)', 'APRS über die ISS': 'APRS via the ISS',
    'Satellit gehört (alle Treffer im Band Satelliten)': 'Satellite heard (any hit in the band Satellites)',
    'Beobachtet': 'Watched', 'Abstimmung:': 'Tuning:', '↓ I/Q-Rohdaten (für SDR-Software)': '↓ I/Q raw data (for SDR software)', 'Beobachtung ausgelöst': 'Watch triggered', 'QSO verfolgt': 'QSO followed',
    // System, Aufräumen, Download
    'System': 'System', 'Automatisch löschen (ungeschützte Treffer)': 'Delete automatically (unprotected hits)',
    'CPU-Temperatur': 'CPU temperature', 'gedrosselt': 'throttled', 'war gedrosselt': 'was throttled', 'nein': 'no', 'ja': 'yes',
    'Drosselung / Unterspannung': 'Throttling / undervoltage', 'Uhr synchron (NTP)': 'Clock synchronised (NTP)', 'Last (1 min)': 'Load (1 min)',
    'läuft seit': 'up for', 'Version': 'Version', '↓ Bild herunterladen (PNG)': '↓ Download image (PNG)',
    'Speicherkarte fast voll – keine neuen Scans und Überflüge, bis wieder Platz ist.': 'SD card almost full – no new scans or passes until there is space again.',
    'Speicherkarte wird voll – alte Treffer aufräumen oder automatisch löschen lassen.': 'SD card is filling up – clean up old hits or enable automatic deletion.',
    'Abgebrochene Überflüge liegen noch als Rohdaten vor – auf dem Pi: python3 bin/sat_recover.py': 'Interrupted passes are still available as raw data – on the Pi: python3 bin/sat_recover.py',
    'Unterspannung erkannt – Netzteil prüfen (der RTL-Stick zieht zusätzlich Strom).': 'Undervoltage detected – check the power supply (the RTL stick draws extra current).',
    'Die Uhr des Pi ist nicht synchron – Satellitenzeiten und Doppler werden ungenau.': "The Pi's clock is not synchronised – satellite times and Doppler become inaccurate.",
    '✓ automatisches Löschen aus': '✓ automatic deletion off',
    '✓ gespeichert – wird alle 15 min geprüft, geschützte Treffer (★) bleiben': '✓ saved – checked every 15 min, protected hits (★) are kept',
    'Automatisch aufgeräumt': 'Cleaned up automatically', 'Rohdaten eines Überflugs gelöscht': 'Raw data of a pass deleted',
    'Speicherkarte fast voll': 'SD card almost full', 'Speicherkarte wird voll': 'SD card filling up', 'Speicherplatz wieder ok': 'Disk space ok again',
    'Überflug abgebrochen': 'Pass aborted', 'Scan ausgelassen (Speicher voll)': 'Scan skipped (disk full)', 'Haushalt fehlgeschlagen': 'Housekeeping failed',
    // Zugangsschutz
    'Zugangsschutz': 'Access protection', 'Aktuelles Passwort': 'Current password', 'Neues Passwort (mind. 8 Zeichen)': 'New password (min. 8 characters)',
    'Neues Passwort wiederholen': 'Repeat new password', 'Passwort setzen': 'Set password', 'Passwort ändern': 'Change password', 'Abmelden': 'Log out',
    'Schutz ausschalten': 'Switch off protection', 'Ohne Anmeldung aus diesen Netzen (z. B. 192.168.178.0/24)': 'No login needed from these networks (e.g. 192.168.178.0/24)',
    'Netze speichern': 'Save networks', '✓ Netze gespeichert': '✓ Networks saved', 'Zugangsschutz aktiv': 'Access protection on',
    'Kein Passwort gesetzt – das Dashboard ist für jeden im Netz offen.': 'No password set – the dashboard is open to everyone on the network.',
    'Die neuen Passwörter stimmen nicht überein': 'The new passwords do not match',
    '✓ Passwort gespeichert – andere Geräte müssen sich neu anmelden': '✓ Password saved – other devices have to log in again',
    'Zugangsschutz wirklich ausschalten?': 'Really switch off access protection?', '✓ Zugangsschutz ausgeschaltet': '✓ Access protection switched off',
    'Ohne Passwort kann jeder, der das Dashboard erreicht, scannen, Treffer löschen und die Einstellungen ändern. Spätestens wenn der Pi aus dem Internet erreichbar ist, ein Passwort setzen. Passwort vergessen: auf dem Pi':
      'Without a password anyone who can reach the dashboard can scan, delete hits and change the settings. Set a password at the latest when the Pi is reachable from the internet. Forgot the password: on the Pi run',
    'Passwort': 'Password', 'Anmelden': 'Log in', 'Passwort vergessen? Auf dem Pi:': 'Forgot the password? On the Pi:',
    'Passwort falsch': 'Wrong password', 'Aktuelles Passwort falsch': 'Current password is wrong', 'Anmeldung nötig': 'Login required',
    'Zu viele Fehlversuche – bitte 5 Minuten warten': 'Too many failed attempts – please wait 5 minutes',
    'Passwort zu kurz (mindestens 8 Zeichen)': 'Password too short (at least 8 characters)',
    'Anmeldung': 'Login', 'Abgemeldet': 'Logged out', 'Anmeldung fehlgeschlagen': 'Login failed', 'Anmeldung gesperrt': 'Login blocked',
    'Passwort gesetzt': 'Password set', 'Zugangsschutz ausgeschaltet': 'Access protection switched off',
  };

  // Textteile mit eingesetzten Werten (Reihenfolge zählt)
  const P = [
    [/^Scan abgebrochen: /, 'Scan aborted: '],
    [/^Scan fehlgeschlagen: /, 'Scan failed: '],
    [/^Fehler: /, 'Error: '],
    [/^Einstellungen nicht ladbar: /, 'Cannot load settings: '],
    [/^Geräte geändert: (.+)$/, (m, x) => 'Receivers changed: ' + x.replace(/ da\b/g, ' present').replace(/ weg\b/g, ' gone')],
    [/✓ Gerät umgestellt – Antenne am (.+) anschließen/, '✓ Receiver switched – connect the antenna to $1'],
    [/^(.+) nicht angeschlossen – automatisch (.+)\. /, '$1 not connected – using $2 automatically. '],
    [/^Kein Empfänger angeschlossen – (.+) oder (.+) anstecken\./, 'No receiver connected – plug in $1 or $2.'],
    [/^(.+) am (.+)\. Gescannt werden nur die VHF\/UHF-Bänder\./, '$1 on the $2. Only the VHF/UHF bands are scanned.'],
    [/^(.+) am (.+)\. Gescannt werden nur die Kurzwellenbänder\./, '$1 on the $2. Only the shortwave bands are scanned.'],
    [/^Gescannt werden nur die VHF\/UHF-Bänder\./, 'Only the VHF/UHF bands are scanned.'],
    [/^Gescannt werden nur die Kurzwellenbänder\./, 'Only the shortwave bands are scanned.'],
    [/steckt – Steuerung läuft nicht \((.+)\)/, 'plugged in – control program not running ($1)'],
    [/Soundkarte \(Aureon\) fehlt/, 'sound card missing'], [/PMSDR-Platine fehlt/, 'PMSDR board missing'],
    [/Kurzwelle 160 m – 10 m/, 'shortwave 160 m – 10 m'],
    [/^alle (\d+) min$/, 'every $1 min'], [/^alle (\d+) Stunden$/, 'every $1 hours'], [/^alle (\d+) min – Scan dauert länger$/, 'every $1 min – scan takes longer'],
    [/^alle (\d+) Stunden – Scan dauert länger$/, 'every $1 hours – scan takes longer'], [/^jede Stunde – Scan dauert länger$/, 'every hour – scan takes longer'],
    [/Ein Scan mit dieser Auswahl dauert etwa (.+?) \((.*), Vor-\/Nachlauf ≈ (.+?)\)\./, (m, a, b, c) => `A scan with this selection takes about ${a} (${b}, setup ≈ ${c}).`],
    [/ \(geschätzt\)/g, ' (estimated)'],
    [/ ⚠ Länger als das Intervall – die Scans laufen dann mit 30 s Pause hintereinander, der Stick ist fast nie frei\./, ' ⚠ Longer than the interval – scans then run back to back with a 30 s pause, the stick is almost never free.'],
    [/ Kürzestes sinnvolles Intervall: (\d+) min\./, ' Shortest sensible interval: $1 min.'],
    [/ Kürzestes sinnvolles Intervall: jede Stunde\./, ' Shortest sensible interval: every hour.'],
    [/^Scans 24 h · (\d+) Fehler$/, 'Scans 24 h · $1 errors'], [/^(\d+) Fehler$/, '$1 errors'],
    [/^Überflüge 24 h · (\d+) Treffer$/, 'Passes 24 h · $1 hits'],
    [/^Überflug (.+) · (\d+)°$/, 'Pass $1 · $2°'],
    [/^in (\d+):(\d\d) h$/, 'in $1:$2 h'],
    [/^(\d+)–(\d+) von (\d+)$/, '$1–$2 of $3'], [/^Einträge (\d+)–(\d+)$/, 'Entries $1–$2'],
    [/^24 h · (\d+) Einträge$/, '24 h · $1 entries'], [/^(\d+)× seit (.+)$/, '$1× since $2'],
    [/^(\d+) Treffer \(erste 400\)$/, '$1 hits (first 400)'], [/^(\d+) Treffer$/, '$1 hits'], [/ · (\d+) Treffer$/, ' · $1 hits'],
    [/^(\d+) P · /, '$1 pts · '], [/^(\d+) P$/, '$1 pts'],
    [/noch nie aufgenommen/g, 'never recorded'], [/(\d+) Tage nicht aufgenommen/g, 'not recorded for $1 days'],
    [/(\d+) h nicht aufgenommen/g, 'not recorded for $1 h'], [/herabgestuft: (\d+) Aufnahmen ohne Signal/g, 'downgraded: $1 recordings without signal'],
    [/aktiv: (\d+) Treffer in 14 Tagen/g, 'active: $1 hits in 14 days'],
    [/^gemeinsame Aufnahme mit (.+)$/, 'shared recording with $1'], [/^gekürzt wegen (.+)$/, 'shortened because of $1'],
    [/gemeinsame Aufnahme mit (.+) ist schon voll \((\d+) Kanäle\); /, 'the shared recording with $1 is already full ($2 channels); '],
    [/gleichzeitig mit (.+?), Frequenz passt nicht ins gemeinsame Empfangsfenster; /, 'simultaneous with $1, frequency does not fit the shared receive window; '],
    [/(.+) hat Vorrang \((\d+) gegen (\d+) Punkte\)/, '$1 has priority ($2 vs. $3 points)'], [/(.+) hat Vorrang$/, '$1 has priority'],
    [/^nicht gleichzeitig: /, 'not simultaneously: '],
    [/^Bahndaten vor weniger als 1 h geholt/, 'Orbital elements fetched less than 1 h ago'],
    [/^Bahndaten vor (\d+) h geholt/, 'Orbital elements fetched $1 h ago'], [/ · letzter Abruf: /, ' · last fetch: '],
    [/^⚠ keine Bahndaten für /, '⚠ no orbital elements for '], [/ \(nicht mehr im Katalog – verglüht\?\)/g, ' (no longer in the catalogue – decayed?)'],
    [/⚠ Bahndaten (.+) (\d+) Tage alt – Zeiten ungenau/g, '⚠ orbital elements for $1 are $2 days old – times inaccurate'],
    [/ nicht angeschlossen – Überflüge werden nur angezeigt\./, ' not connected – passes are only displayed.'],
    [/^Locator fehlt oder ungültig – unter Einstellungen → Station eintragen$/, 'Locator missing or invalid – enter it under Settings → Station'],
    [/^⚠ Locator fehlt oder ungültig – unter Einstellungen → Station eintragen$/, '⚠ Locator missing or invalid – enter it under Settings → Station'],
    [/^Satellit (.+): Aufnahme (.+) MHz, Elevation (.+)$/, 'Satellite $1: recording $2 MHz, elevation $3'],
    [/^vom Benutzer abgebrochen nach (\d+) s$/, 'stopped by the user after $1 s'],
    [/^Satellit (.+): Auswertung$/, 'Satellite $1: analysing'], [/^Satellit (.+): (\d+) Treffer$/, 'Satellite $1: $2 hits'],
    [/^Standard \((\d+)\)$/, 'default ($1)'],
    [/^(\d+)× aufgenommen, (\d+) Treffer$/, '$1× recorded, $2 hits'], [/ · (\d+)× aufgenommen, (\d+) Treffer$/, ' · $1× recorded, $2 hits'],
    [/^(\d+) würden gelöscht, (\d+) geschützte bleiben$/, '$1 would be deleted, $2 protected remain'],
    [/^Alle ungeschützten Treffer älter als (\d+) Tage löschen\?$/, 'Delete all unprotected hits older than $1 days?'],
    [/^(\d+) gelöscht$/, '$1 deleted'], [/^Treffer (.+) MHz löschen\?$/, 'Delete hit $1 MHz?'],
    [/^Noch nichts dekodiert/, 'Nothing decoded yet'], [/ – für WSPR gibt es keinen Dekoder auf dem Pi/, ' – there is no WSPR decoder on the Pi'],
    [/ \(läuft nach dem Scan, oder das Signal war zu kurz\/zu schwach\)/, ' (runs after the scan, or the signal was too short/weak)'],
    [/^Locator unter „Bänder &amp; Betriebsarten“ eintragen/, 'Enter your locator under Settings'],
    [/Locator unter „Bänder & Betriebsarten“ eintragen, dann stehen hier Entfernung und Richtung\./, 'Enter your locator under Settings to see distance and bearing here.'],
    [/^Scan-Ausgabe/, 'Scan output'],
    [/^Segment (.+)$/, 'segment $1'], [/^kein Segment$/, 'no segment'],
    [/^CQ (.*)von (.+?)( aus (.+))?$/, (m, tag, call, x, grid) => `CQ ${tag}from ${call}${grid ? ' in ' + grid : ''}`],
    [/^(.+) gibt (.+) Rapport (.+?)( \(bestätigt\))?$/, (m, a, b, r, c) => `${a} gives ${b} report ${r}${c ? ' (confirmed)' : ''}`],
    [/^Audio-Strom nicht verfügbar \(/, 'Audio stream not available ('],
    [/^älter als (\d+) Tage$/, 'older than $1 days'], [/^frei von ([\d.]+) GB \(([\d.]+) % belegt\)$/, 'free of $1 GB ($2 % used)'],
    [/^Aufnahmen \((\d+) Dateien\)$/, 'Recordings ($1 files)'], [/^abgebrochene Überflüge \((\d+) MB\)$/, 'interrupted passes ($1 MB)'],
    [/^Arbeitsspeicher frei von (\d+) MB$/, 'memory free of $1 MB'], [/^(\d+) Tage$/, '$1 days'], [/^Version – neu: (.+)$/, 'Version – new: $1'],
    [/Neue Version (.+) verfügbar – auf dem Pi: bin\/update\.sh/, 'New version $1 available – on the Pi: bin/update.sh'],
    [/^Frequenz: (.+)$/, 'Frequency: $1'], [/^Rufzeichen: (.+)$/, 'Callsign: $1'], [/^Ereignis: (.+)$/, 'Event: $1'], [/^Entfernung ab: (.+)$/, 'Distance from: $1'],
    [/ · Mail\/Push/, ' · mail/push'], [/^(\d+) Treffer \/ (\d+) Abstimmungen$/, '$1 hits / $2 tunings'], [/^Zeitplan „(.+)“ aktiv bis (\d\d:\d\d) – gescannt wird gerade: (.+)$/, 'Schedule "$1" active until $2 – now scanning: $3'],
    [/^Uhrzeiten in der Ortszeit des Pi \(jetzt (.+)\)\.$/, "Times in the Pi's local time (now $1)."], [/^täglich · /, 'daily · '], [/ \(vorher speichern\)$/, ' (save first)'],
    [/ · Ruhe (\d+) min/, ' · quiet $1 min'], [/SSTV-Bild von der ISS/g, 'SSTV image from the ISS'], [/SSTV-Bild \(alle\)/g, 'SSTV image (any)'],
    [/APRS über die ISS/g, 'APRS via the ISS'], [/Satellit gehört \(alle Treffer im Band Satelliten\)/g, 'Satellite heard (any hit in the band Satellites)'], [/(\d+)° (NO|SO)$/, (m, a, d) => a + '° ' + d.replace('O', 'E')], [/(\d+)° O$/, '$1° E'],
    [/^Empfang (.+?) · (\d+) dB über Rauschen/, 'Receiving $1 · $2 dB above noise'], [/ · ÜBERSTEUERT \(([\d.,]+) %\) – Verstärkung senken/, ' · OVERLOAD ($1 %) – reduce gain'],
    [/^Empfang (.+)$/, 'Receiving $1'], [/^(.+) scannt (\d+)\/(\d+)$/, '$1 scanning $2/$3'], [/^Grund: /, 'Reason: '], [/(\d+) Pakete\b/g, '$1 packets'],
    [/^Fehler: Gerade läuft ein Scan – bitte warten oder abbrechen lassen$/, 'Error: a scan is running – please wait or stop it'],
    [/^Treffer · /, 'Hits · '],
    [/^Zugangsschutz aktiv/, 'Access protection on'], [/ · dieses Gerät ist in einem freigegebenen Netz$/, ' · this device is in a trusted network'], [/ · angemeldet$/, ' · logged in'],
    [/Ungültiges Netz: (.+) \(Beispiel (.+)\)/, 'Invalid network: $1 (example $2)'],
    [/ · Treffer: (\d+)/, ' · Hits: $1'], [/^Satellit: /, 'Satellite: '],
    [/^abgehört \(MHz\): /, 'monitored (MHz): '], [/ · abgehört \(MHz\): /, ' · monitored (MHz): '], [/^Frequenzen \(MHz\): /, 'Frequencies (MHz): '], [/ · Frequenzen \(MHz\): /, ' · Frequencies (MHz): '],
    [/^Pegel (-?\d+) dB · Rauschen (-?\d+) dB/, 'Level $1 dB · noise $2 dB'],
    [/^Lauf (\d+)$/, 'Run $1'], [/^Zeit \((.+)\)$/, 'Time ($1)'],
    [/\bGrund ([+-][\d.]+)/, 'base $1'], [/\bHöhe ([+-][\d.]+)/, 'elevation $1'], [/\bWartezeit ([+-][\d.]+)/, 'waiting $1'], [/\bRuhe ([+-][\d.]+)/, 'silence $1'],
    [/^Satellit – /, 'Satellite – '], [/^([^–]+?) – (.+)$/, (m, a, b) => `${t(a)} – ${t(b)}`],
    [/^Treffer: (\d+)$/, 'Hits: $1'],
    // Zeitangaben (Teile)
    [/\bheute\b/g, 'today'], [/\bgestern\b/g, 'yesterday'], [/\bvor (\d+) Tagen\b/g, '$1 days ago'],
    [/\bvor ([\d.,]+ ?(?:s|min|h|Tage|Tag))\b/g, (m, x) => `${x.replace('Tage', 'days').replace('Tag', 'day')} ago`],
    [/\bvor (\d+ h \d+ min)\b/g, '$1 ago'], [/^bis /, 'until '],
    [/(\d+) Stunden?\b/g, (m, n) => n === '1' ? '1 hour' : `${n} hours`],
    // Bandplan-Bezeichnungen (Teile)
    [/\bRelais-Ausgaben\b/g, 'Repeater outputs'], [/\bEingabe\b/g, 'input'], [/\bDatenkanäle\b/g, 'data channels'],
    [/\bKanal\b/g, 'channel'], [/\bSprache\b/g, 'voice'], [/\bBake\b/g, 'beacon'], [/\b\/ Daten\b/g, '/ data'],
    [/ISS-Relais/g, 'ISS repeater'], [/SSTV-Aktionen/g, 'SSTV events'], [/APRS-Digipeater/g, 'APRS digipeater'], [/\(Mitte\)/g, '(centre)'],
    [/^Spektrum um (.+) MHz$/, 'Spectrum around $1 MHz'], [/^SSTV-Bild$/, 'SSTV image'], [/^Satelliten\/ISS /, 'Satellites/ISS '],
  ];

  function t(s) {
    if (LANG !== 'en' || !s || typeof s !== 'string') return s;
    const k = s.trim();
    if (!k || !/[A-Za-zÄÖÜäöüß]/.test(k)) return s;
    const pct = k.match(/^(.+?) (\d+)%$/);              // Statusanzeige „Bänder werden abgesucht 22%“
    if (pct && D[pct[1]] !== undefined) return s.replace(k, `${D[pct[1]]} ${pct[2]}%`);
    let r = D[k];
    if (r === undefined) {
      r = k;
      for (const [re, rep] of P) r = r.replace(re, rep);
      if (r === k) return s;
    }
    return s.replace(k, r);
  }

  const SKIP = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'CODE', 'PRE']);
  function skipped(el) {
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      if (SKIP.has(e.tagName) || e.classList.contains('mono') || e.classList.contains('no-i18n')) return true;
    }
    return false;
  }
  const ATTRS = ['title', 'placeholder', 'aria-label', 'alt'];
  function translateNode(n) {
    if (n.nodeType === 3) {
      if (n.parentElement && skipped(n.parentElement)) return;
      const v = t(n.data);
      if (v !== n.data) n.data = v;
      return;
    }
    if (n.nodeType !== 1 || skipped(n)) return;
    for (const a of ATTRS) {
      const v = n.getAttribute(a);
      if (v) { const w = t(v); if (w !== v) n.setAttribute(a, w); }
    }
    const walker = document.createTreeWalker(n, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    let x = walker.nextNode();
    while (x) {
      if (x.nodeType === 1) {
        if (skipped(x)) { x = walker.nextSibling() || nextOutside(walker, n); continue; }
        for (const a of ATTRS) { const v = x.getAttribute(a); if (v) { const w = t(v); if (w !== v) x.setAttribute(a, w); } }
      } else {
        const v = t(x.data); if (v !== x.data) x.data = v;
      }
      x = walker.nextNode();
    }
  }
  function nextOutside(walker, root) {
    let p = walker.currentNode;
    while (p && p !== root) { const s = walker.nextSibling(); if (s) return s; p = walker.parentNode(); }
    return null;
  }

  function start() {
    if (LANG !== 'en') return;
    document.title = 'HamScanPi';
    translateNode(document.body);
    new MutationObserver(muts => {
      for (const m of muts) {
        if (m.type === 'characterData') translateNode(m.target);
        else if (m.type === 'attributes') {
          const v = m.target.getAttribute(m.attributeName);
          if (v && !skipped(m.target)) { const w = t(v); if (w !== v) m.target.setAttribute(m.attributeName, w); }
        } else m.addedNodes.forEach(translateNode);
      }
    }).observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS });
    const c = window.confirm.bind(window), a = window.alert.bind(window);
    window.confirm = m => c(t(m));
    window.alert = m => a(t(m));
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();

  window.I18N = {
    lang: LANG, t,
    locale: LANG === 'en' ? 'en-GB' : 'de-DE',
    set(l) { localStorage.setItem('lang', l === 'en' ? 'en' : 'de'); location.reload(); },
  };
})();
