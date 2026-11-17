<?php
/**
 * Plugin Name:       Hesperos DX SSTV-Galerie
 * Description:       Empfängt SSTV-Bilder von Hesperos DX · Ham Radio Watch (früher HamScanPi) (Satellit, Kurzwelle, Live) mit allen Angaben zu Satellit, Überflug, Empfang, Station und Raspberry Pi und zeigt sie als Galerie – nach Tag gruppiert, Filter je Quelle, Lightbox mit Überblendung. Kurzcode: [sstv_galerie]
 * Version:           1.5.0
 * Plugin URI:        https://github.com/do1gmt/hesperos-dx
 * Requires at least: 5.6
 * Requires PHP:      7.4
 * Author:            Hesperos DX
 * Author URI:        https://github.com/do1gmt/hesperos-dx
 * License:           GPL-3.0-or-later
 * Text Domain:       hsp-sstv
 *
 * Anpassen ohne das Plugin zu ändern:
 *  - Einstellungen → SSTV-Galerie: Reihenfolge, Gruppierung (Tag, Quelle, Überflug), Zeitzone der Angaben, Filterknöpfe,
 *    Farben, Rundung, Bildgröße, Überblenddauer, eigenes CSS, Plugin-CSS ganz aus.
 *  - Je Bild (SSTV-Bilder → bearbeiten): Empfangszeit, Quelle, „hervorheben“ – die Galerie sortiert sofort neu.
 *  - Mehrere Pis: je Pi ein Benutzer mit der Rolle „SSTV-Melder“ (darf nur Bilder einsenden). Liste und Protokoll
 *    zeigen, welcher Pi (Stationsname) und welcher Benutzer ein Bild geschickt hat; Kurzcode-Filter station="…".
 *  - Vorlage: templates/galerie.php ins Theme kopieren nach  <theme>/hesperosdx-sstv/galerie.php  und dort ändern.
 *  - CSS-Variablen (--hsp-…) im Theme überschreiben, z. B.  .hsp-galerie { --hsp-accent: #c00; }
 *  - Filter: hsp_sstv_item (ein Bild für die Ausgabe), hsp_sstv_insert_status (Status beim Empfang).
 */

if (!defined('ABSPATH')) {
    exit;
}

// Ordner bis 1.4.x: hamscanpi-sstv-galerie. Ist das alte Plugin noch aktiv, hat es alles schon angelegt – dieses hier
// bleibt dann still und bittet darum, das alte zu deaktivieren (zwei gleichzeitig würden WordPress anhalten).
if (defined('HSP_SSTV_VERSION')) {
    add_action('admin_notices', function () {
        echo '<div class="notice notice-warning"><p><b>Hesperos DX SSTV-Galerie:</b> Bitte das alte Plugin '
           . '(Ordner <code>hamscanpi-sstv-galerie</code>) deaktivieren und löschen – Bilder, Einstellungen und '
           . 'SSTV-Melder bleiben erhalten. Danach arbeitet die neue Version.</p></div>';
    });
    return;
}
define('HSP_SSTV_VERSION', '1.5.0');
define('HSP_SSTV_DIR', plugin_dir_path(__FILE__));
define('HSP_SSTV_URL', plugin_dir_url(__FILE__));
define('HSP_SSTV_NS', 'hsp-sstv/v1');
define('HSP_SSTV_HOME', 'https://github.com/do1gmt/hesperos-dx');   // Programm, Anleitung, Updates

// Alles Weitere erst jetzt laden: PHP legt die Funktionen einer Datei schon beim Einlesen an – stünden sie hier,
// hielte WordPress bei noch aktivem altem Plugin mit „Funktion doppelt“ an, bevor die Prüfung oben greift.
require_once HSP_SSTV_DIR . 'includes/galerie-plugin.php';
