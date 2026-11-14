<?php
/**
 * Vorlage der SSTV-Galerie. Zum Anpassen ins Theme kopieren:  <theme>/hamscanpi-sstv/galerie.php
 * Verfügbar: $hsp['endpoint'], $hsp['per_page'], $hsp['quelle'], $hsp['station'], $hsp['statistik'], $hsp['credit'].
 * Das Skript (assets/galerie.js) füllt .hsp-stats, .hsp-filter, .hsp-days und .hsp-more – diese Klassen bleiben nötig.
 */
if (!defined('ABSPATH')) {
    exit;
}
?>
<div class="hsp-galerie"
     data-endpoint="<?php echo esc_url($hsp['endpoint']); ?>"
     data-per-page="<?php echo (int) $hsp['per_page']; ?>"
     data-quelle="<?php echo esc_attr($hsp['quelle']); ?>">
    <?php if ($hsp['statistik']) : ?><div class="hsp-stats" aria-live="polite"></div><?php endif; ?>
    <div class="hsp-filter" role="toolbar" aria-label="Nach Quelle filtern"></div>
    <div class="hsp-days"><p class="hsp-wait">Bilder werden geladen …</p></div>
    <button type="button" class="hsp-more" hidden>Weitere Bilder laden</button>
    <?php if (!empty($hsp['credit'])) : ?><p class="hsp-credit">Empfangen mit <b>Hesperos DX</b> · Ham Radio Watch</p><?php endif; ?>
    <noscript><p>Für die Galerie bitte JavaScript einschalten.</p></noscript>
</div>
