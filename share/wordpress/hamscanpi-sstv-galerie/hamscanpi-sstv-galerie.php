<?php
/**
 * Plugin Name:       Hesperos DX SSTV-Galerie
 * Description:       Empfängt SSTV-Bilder von Hesperos DX · Ham Radio Watch (früher HamScanPi) (Satellit, Kurzwelle, Live) mit allen Angaben zu Satellit, Überflug, Empfang, Station und Raspberry Pi und zeigt sie als Galerie – nach Tag gruppiert, Filter je Quelle, Lightbox mit Überblendung. Kurzcode: [sstv_galerie]
 * Version:           1.4.2
 * Requires at least: 5.6
 * Requires PHP:      7.4
 * Author:            Hesperos DX
 * License:           GPL-3.0-or-later
 * Text Domain:       hsp-sstv
 *
 * Anpassen ohne das Plugin zu ändern:
 *  - Einstellungen → SSTV-Galerie: Reihenfolge, Gruppierung (Tag, Quelle, Überflug), Zeitzone der Angaben, Filterknöpfe,
 *    Farben, Rundung, Bildgröße, Überblenddauer, eigenes CSS, Plugin-CSS ganz aus.
 *  - Je Bild (SSTV-Bilder → bearbeiten): Empfangszeit, Quelle, „hervorheben“ – die Galerie sortiert sofort neu.
 *  - Mehrere Pis: je Pi ein Benutzer mit der Rolle „SSTV-Melder“ (darf nur Bilder einsenden). Liste und Protokoll
 *    zeigen, welcher Pi (Stationsname) und welcher Benutzer ein Bild geschickt hat; Kurzcode-Filter station="…".
 *  - Vorlage: templates/galerie.php ins Theme kopieren nach  <theme>/hamscanpi-sstv/galerie.php  und dort ändern.
 *  - CSS-Variablen (--hsp-…) im Theme überschreiben, z. B.  .hsp-galerie { --hsp-accent: #c00; }
 *  - Filter: hsp_sstv_item (ein Bild für die Ausgabe), hsp_sstv_insert_status (Status beim Empfang).
 */

if (!defined('ABSPATH')) {
    exit;
}

define('HSP_SSTV_VERSION', '1.4.2');
define('HSP_SSTV_DIR', plugin_dir_path(__FILE__));
define('HSP_SSTV_URL', plugin_dir_url(__FILE__));
define('HSP_SSTV_NS', 'hsp-sstv/v1');

/* ------------------------------------------------------------------ Einstellungen */

function hsp_sstv_defaults()
{
    return array(
        'status_mode' => 'pi',        // pi = wie Hesperos DX es schickt | draft = immer Entwurf | publish = immer sofort
        'max_mb'      => 8,
        'per_page'    => 48,
        'accent'      => '',          // leer = vom Theme (--green / --accent), sonst Vorgabe des Plugins
        'card_bg'     => '',          // leer = vom Theme
        'text'        => '',
        'muted'       => '',
        'radius'      => '',          // leer = vom Theme (--radius)
        'thumb'       => 220,         // Mindestbreite eines Vorschaubilds in px
        'max_w'       => '',          // größte Breite der Galerie in px, leer = vom Theme
        'fade_ms'     => 450,
        'load_css'    => 1,
        'custom_css'  => '',
        'show_station'=> 1,
        'show_pi'     => 1,
        'credit'      => 1,           // Zeile „Empfangen mit Hesperos DX · Ham Radio Watch“ unter der Galerie
        'order'       => 'desc',      // desc = neueste zuerst | asc = älteste zuerst
        'group'       => 'day',       // day | source | pass (Überflug) | none
        'date_zone'   => 'station',   // station = wie in Hesperos DX eingestellt | utc | site = Zeitzone dieser WordPress-Seite
        'chips'       => 'count',     // Filterknöpfe je Quelle: count = nach Anzahl | alpha = alphabetisch | off = keine
        'manual_status' => 'same',    // von Hand nachgereichte Bilder: same = wie automatische | draft = immer Entwurf
    );
}

function hsp_sstv_opts()
{
    $o = get_option('hsp_sstv_opts', array());
    return wp_parse_args(is_array($o) ? $o : array(), hsp_sstv_defaults());
}

/* ------------------------------------------------------------------ Rolle „SSTV-Melder“ */

/** Rolle mit dem Nötigsten: lesen, Dateien hochladen, SSTV-Bilder einsenden (und, wenn erlaubt, gleich veröffentlichen)
 *  – kein Zugang zu Beiträgen, Seiten oder Einstellungen. Wird beim Laden angelegt (auch nach einem Plugin-Update). */
function hsp_sstv_roles()
{
    if (get_option('hsp_sstv_roles') === '1') {
        return;
    }
    remove_role('hsp_sstv_melder');
    add_role('hsp_sstv_melder', 'SSTV-Melder', array('read' => true, 'upload_files' => true, 'hsp_sstv_send' => true,
                                                     'hsp_sstv_publish' => true));
    foreach (array('administrator', 'editor', 'author') as $r) {
        $role = get_role($r);
        if ($role) {
            $role->add_cap('hsp_sstv_send');
            $role->add_cap('hsp_sstv_publish');
        }
    }
    update_option('hsp_sstv_roles', '1');
}
add_action('init', 'hsp_sstv_roles', 5);

/** Ältere Bilder (vor 1.4): Stationsname aus den mitgeschickten Angaben übernehmen – einmalig, für Spalte und Filter. */
add_action('admin_init', function () {
    if (get_option('hsp_sstv_station_meta') === '1') {
        return;
    }
    $ids = get_posts(array('post_type' => 'hsp_sstv', 'post_status' => 'any', 'fields' => 'ids', 'posts_per_page' => 2000,
                           'meta_query' => array(array('key' => '_hsp_station', 'compare' => 'NOT EXISTS'))));
    foreach ($ids as $id) {
        $m = json_decode((string) get_post_meta($id, '_hsp_meta', true), true);
        if (is_array($m) && !empty($m['summary']['station'])) {
            update_post_meta($id, '_hsp_station', mb_substr((string) $m['summary']['station'], 0, 80));
        }
    }
    update_option('hsp_sstv_station_meta', '1');
});

function hsp_sstv_can_send()
{
    return current_user_can('hsp_sstv_send') || current_user_can('upload_files');
}

function hsp_sstv_can_publish()
{
    return current_user_can('hsp_sstv_publish') || current_user_can('publish_posts');
}

/* ------------------------------------------------------------------ Inhaltstyp und Quellen */

add_action('init', function () {
    register_post_type('hsp_sstv', array(
        'labels'       => array(
            'name' => 'SSTV-Bilder', 'singular_name' => 'SSTV-Bild', 'menu_name' => 'SSTV-Bilder',
            'all_items' => 'Alle SSTV-Bilder', 'edit_item' => 'SSTV-Bild prüfen', 'search_items' => 'SSTV-Bilder durchsuchen',
            'not_found' => 'Noch keine SSTV-Bilder', 'add_new' => 'Neu', 'add_new_item' => 'Neues SSTV-Bild',
        ),
        'public'       => false,
        'show_ui'      => true,
        'show_in_menu' => true,
        'menu_icon'    => 'dashicons-format-image',
        'supports'     => array('title', 'thumbnail'),
        'map_meta_cap' => true,
        'capability_type' => 'post',
    ));
    register_taxonomy('hsp_sat', 'hsp_sstv', array(
        'labels'            => array('name' => 'Quellen', 'singular_name' => 'Quelle', 'menu_name' => 'Quellen (Satelliten)'),
        'public'            => false,
        'show_ui'           => true,
        'show_admin_column' => true,
        'hierarchical'      => false,
    ));
});

/* ------------------------------------------------------------------ Schnittstelle für Hesperos DX */

add_action('rest_api_init', function () {
    register_rest_route(HSP_SSTV_NS, '/ping', array(
        'methods'             => 'GET',
        'permission_callback' => 'hsp_sstv_can_send',
        'callback'            => 'hsp_sstv_ping',
    ));
    register_rest_route(HSP_SSTV_NS, '/images', array(
        'methods'             => 'POST',
        'permission_callback' => 'hsp_sstv_can_send',
        'callback'            => 'hsp_sstv_receive',
    ));
    register_rest_route(HSP_SSTV_NS, '/gallery', array(
        'methods'             => 'GET',
        'permission_callback' => '__return_true',     // nur veröffentlichte Bilder
        'callback'            => 'hsp_sstv_gallery_rest',
        'args'                => array(
            'page'     => array('default' => 1, 'sanitize_callback' => function ($v) { return absint($v); }),
            'per_page' => array('default' => 48, 'sanitize_callback' => function ($v) { return absint($v); }),
            // eigene Hülle: sanitize_title() nähme sonst das Anfrage-Objekt als Ersatzwert (2. Argument)
            'quelle'   => array('default' => '', 'sanitize_callback' => function ($v) { return sanitize_title((string) $v); }),
            'station'  => array('default' => '', 'sanitize_callback' => function ($v) { return mb_substr(sanitize_text_field((string) $v), 0, 80); }),
        ),
    ));
});

function hsp_sstv_ping()
{
    $u = wp_get_current_user();
    return array(
        'ok'          => true,
        'version'     => HSP_SSTV_VERSION,
        'site'        => get_bloginfo('name'),
        'user'        => $u ? $u->user_login : '',
        'can_publish' => hsp_sstv_can_publish(),
        'max_mb'      => (int) hsp_sstv_opts()['max_mb'],
    );
}

/** Angaben säubern: nur Text, begrenzte Mengen, bekannte Felder in summary. */
function hsp_sstv_clean_meta($m)
{
    $out = array('v' => 1, 'summary' => array(), 'sections' => array());
    if (!is_array($m)) {
        return $out;
    }
    $keys = array('source', 'time_utc', 'mode', 'complete', 'call', 'locator', 'station', 'kind', 'norad', 'freq_hz',
                  'max_el', 'el', 'snr_db', 'band', 'tz', 'manual');
    $sum = isset($m['summary']) && is_array($m['summary']) ? $m['summary'] : array();
    foreach ($keys as $k) {
        if (isset($sum[$k]) && is_scalar($sum[$k])) {
            $out['summary'][$k] = is_numeric($sum[$k]) && !in_array($k, array('norad', 'locator', 'call'), true)
                ? 0 + $sum[$k] : mb_substr(sanitize_text_field((string) $sum[$k]), 0, 120);
        }
    }
    $secs = isset($m['sections']) && is_array($m['sections']) ? array_slice($m['sections'], 0, 12) : array();
    foreach ($secs as $s) {
        if (!is_array($s) || empty($s['title']) || empty($s['rows']) || !is_array($s['rows'])) {
            continue;
        }
        $rows = array();
        foreach (array_slice($s['rows'], 0, 40) as $r) {
            if (is_array($r) && count($r) >= 2 && is_scalar($r[0]) && is_scalar($r[1]) && (string) $r[1] !== '') {
                $rows[] = array(mb_substr(sanitize_text_field((string) $r[0]), 0, 80), mb_substr(sanitize_text_field((string) $r[1]), 0, 200));
            }
        }
        if ($rows) {
            $out['sections'][] = array('title' => mb_substr(sanitize_text_field((string) $s['title']), 0, 80), 'rows' => $rows);
        }
    }
    return $out;
}

/** Zeitzone, in der Hesperos DX die Zeiten anzeigt (UTC oder Ortszeit des Pi) – die Galerie folgt ihr. */
function hsp_sstv_zone($sum)
{
    $mode = hsp_sstv_opts()['date_zone'];
    if ($mode === 'utc') {
        return new DateTimeZone('UTC');
    }
    if ($mode === 'site') {
        return wp_timezone();
    }
    $tz = isset($sum['tz']) ? (string) $sum['tz'] : 'UTC';
    try {
        return new DateTimeZone(in_array($tz, timezone_identifiers_list(), true) ? $tz : 'UTC');
    } catch (Exception $e) {
        return new DateTimeZone('UTC');
    }
}

function hsp_sstv_local($t, $sum)
{
    $d = new DateTime('@' . (int) $t);
    $d->setTimezone(hsp_sstv_zone($sum));
    return $d;
}

/** Kennung der Zeitzone wie in Hesperos DX: deutsch MEZ/MESZ, sonst CET/CEST usw. */
function hsp_sstv_tzlabel($lbl)
{
    if ($lbl === 'Z' || $lbl === '+00:00' || $lbl === 'GMT') {
        return 'UTC';
    }
    if (strpos(get_locale(), 'de') === 0) {
        $de = array('CET' => 'MEZ', 'CEST' => 'MESZ');
        return isset($de[$lbl]) ? $de[$lbl] : $lbl;
    }
    return $lbl;
}

function hsp_sstv_find_uid($uid)
{
    $q = get_posts(array('post_type' => 'hsp_sstv', 'post_status' => 'any', 'meta_key' => '_hsp_uid', 'meta_value' => $uid,
                         'fields' => 'ids', 'posts_per_page' => 1, 'no_found_rows' => true));
    return $q ? (int) $q[0] : 0;
}

function hsp_sstv_answer($id, $dup)
{
    $p = get_post($id);
    $o = hsp_sstv_opts();
    $link = '';
    if ($p && $p->post_status === 'publish') {
        $page = get_option('hsp_sstv_page');
        $link = $page ? get_permalink((int) $page) . '#sstv-' . $id : wp_get_attachment_url(get_post_thumbnail_id($id));
    }
    // Link zum Freigeben für den Menschen am Dashboard – auch wenn der einsendende Benutzer (SSTV-Melder) selbst nicht bearbeiten darf
    return array('ok' => true, 'id' => $id, 'status' => $p ? $p->post_status : '', 'duplicate' => $dup, 'link' => $link,
                 'edit' => admin_url('post.php?post=' . (int) $id . '&action=edit'));
}

/** Protokoll der letzten 100 Empfänge (Einstellungen → SSTV-Galerie) – zur Fehlersuche. */
function hsp_sstv_log($result, $detail = array())
{
    $log = get_option('hsp_sstv_log', array());
    $log = is_array($log) ? $log : array();
    $u = wp_get_current_user();
    $log[] = array_merge(array('t' => time(), 'result' => $result, 'user' => $u ? $u->user_login : ''), $detail);
    update_option('hsp_sstv_log', array_slice($log, -100), false);
}

function hsp_sstv_receive(WP_REST_Request $req)
{
    $r = hsp_sstv_receive_inner($req);
    $src = json_decode((string) $req->get_param('meta'), true);
    $sta = is_array($src) && isset($src['summary']['station']) ? mb_substr(sanitize_text_field((string) $src['summary']['station']), 0, 80) : '';
    $src = is_array($src) && isset($src['summary']['source']) ? mb_substr(sanitize_text_field((string) $src['summary']['source']), 0, 80) : '';
    if (is_wp_error($r)) {
        hsp_sstv_log('abgelehnt', array('source' => $src, 'station' => $sta, 'reason' => $r->get_error_message()));
    } else {
        hsp_sstv_log($r['duplicate'] ? 'schon vorhanden' : ($r['status'] === 'publish' ? 'veröffentlicht' : 'Entwurf'),
                     array('source' => $src, 'station' => $sta, 'id' => $r['id']));
    }
    return $r;
}

function hsp_sstv_receive_inner(WP_REST_Request $req)
{
    $o = hsp_sstv_opts();
    $uid = substr(preg_replace('/[^A-Za-z0-9_-]/', '', (string) $req->get_param('uid')), 0, 64);
    if ($uid === '') {
        return new WP_Error('hsp_uid', 'Kennung fehlt', array('status' => 400));
    }
    $old = hsp_sstv_find_uid($uid);
    if ($old) {                                         // wiederholter Versuch: kein zweites Bild
        return hsp_sstv_answer($old, true);
    }
    $files = $req->get_file_params();
    $f = isset($files['image']) ? $files['image'] : null;
    if (!$f || !empty($f['error']) || empty($f['tmp_name']) || !is_uploaded_file($f['tmp_name'])) {
        return new WP_Error('hsp_file', 'Bilddatei fehlt', array('status' => 400));
    }
    if ($f['size'] > max(1, (int) $o['max_mb']) * 1048576) {
        return new WP_Error('hsp_size', 'Bild zu groß', array('status' => 413));
    }
    $chk = wp_check_filetype_and_ext($f['tmp_name'], $f['name'], array('png' => 'image/png', 'jpg|jpeg' => 'image/jpeg', 'webp' => 'image/webp'));
    if (empty($chk['type']) || !@getimagesize($f['tmp_name'])) {
        return new WP_Error('hsp_type', 'Nur PNG, JPEG oder WebP', array('status' => 415));
    }
    $meta = hsp_sstv_clean_meta(json_decode((string) $req->get_param('meta'), true));
    $sum = $meta['summary'];
    $t = !empty($sum['time_utc']) ? strtotime($sum['time_utc']) : time();
    if (!$t) {
        $t = time();
    }
    $want = $req->get_param('status') === 'publish' ? 'publish' : 'draft';
    if ($o['status_mode'] === 'draft' || $o['status_mode'] === 'publish') {
        $want = $o['status_mode'];
    }
    if ($want === 'publish' && !hsp_sstv_can_publish()) {
        $want = 'pending';
    }
    $manual = (bool) $req->get_param('manual');
    if ($manual && $o['manual_status'] === 'draft') {     // von Hand nachgereicht: auf Wunsch immer erst prüfen
        $want = 'draft';
    }
    $want = apply_filters('hsp_sstv_insert_status', $want, $meta);
    $source = !empty($sum['source']) ? $sum['source'] : 'SSTV';
    $lt = hsp_sstv_local($t, $sum);
    $title = sprintf('%s · %s · %s %s', $source, !empty($sum['mode']) ? $sum['mode'] : 'SSTV', $lt->format('Y-m-d H:i'), hsp_sstv_tzlabel($lt->format('T')));
    // Beitragsdatum nie in der Zukunft: geht die Uhr des Pi etwas vor, machte WordPress aus „veröffentlichen“
    // sonst einen geplanten Beitrag. Sortiert wird ohnehin nach der Empfangszeit (_hsp_time).
    $gmt = gmdate('Y-m-d H:i:s', min($t, time()));
    $id = wp_insert_post(array('post_type' => 'hsp_sstv', 'post_status' => $want, 'post_title' => $title,
                               'post_date_gmt' => $gmt, 'post_date' => get_date_from_gmt($gmt)), true);
    if (is_wp_error($id)) {
        return $id;
    }
    require_once ABSPATH . 'wp-admin/includes/file.php';
    require_once ABSPATH . 'wp-admin/includes/media.php';
    require_once ABSPATH . 'wp-admin/includes/image.php';
    $name = 'sstv-' . sanitize_title($source) . '-' . gmdate('Ymd-His', $t) . '.' . (isset($chk['ext']) && $chk['ext'] ? $chk['ext'] : 'png');
    $att = media_handle_sideload(array('name' => $name, 'tmp_name' => $f['tmp_name']), $id, $title);
    if (is_wp_error($att)) {
        wp_delete_post($id, true);
        return $att;
    }
    set_post_thumbnail($id, $att);
    update_post_meta($id, '_hsp_uid', $uid);
    update_post_meta($id, '_hsp_meta', wp_slash(wp_json_encode($meta)));
    update_post_meta($id, '_hsp_time', $t);
    if (isset($sum['max_el'])) {
        update_post_meta($id, '_hsp_max_el', (float) $sum['max_el']);
    }
    if (isset($sum['complete'])) {
        update_post_meta($id, '_hsp_complete', (float) $sum['complete']);
    }
    if ($manual) {
        update_post_meta($id, '_hsp_manual', 1);
    }
    // wer hat es geschickt: Stationsname des Pi (Einstellungen → Station) und WordPress-Benutzer
    if (!empty($sum['station'])) {
        update_post_meta($id, '_hsp_station', mb_substr((string) $sum['station'], 0, 80));
    }
    $u = wp_get_current_user();
    if ($u && $u->ID) {
        update_post_meta($id, '_hsp_user', $u->user_login);
    }
    wp_set_object_terms($id, $source, 'hsp_sat');
    return hsp_sstv_answer($id, false);
}

/* ------------------------------------------------------------------ Galerie-Daten (öffentlich) */

function hsp_sstv_item($id)
{
    $o = hsp_sstv_opts();
    $meta = json_decode((string) get_post_meta($id, '_hsp_meta', true), true);
    $meta = is_array($meta) ? $meta : array('summary' => array(), 'sections' => array());
    $att = get_post_thumbnail_id($id);
    $full = $att ? wp_get_attachment_image_src($att, 'full') : null;
    $thumb = $att ? wp_get_attachment_image_src($att, 'medium_large') : null;
    $secs = array();
    foreach ($meta['sections'] as $s) {
        if ((!$o['show_station'] && $s['title'] === 'Station') || (!$o['show_pi'] && strpos($s['title'], 'Raspberry Pi') === 0)) {
            continue;
        }
        $secs[] = $s;
    }
    $t = (int) get_post_meta($id, '_hsp_time', true);
    $lt = hsp_sstv_local($t, $meta['summary']);
    $lbl = $lt->format('T');
    $terms = wp_get_object_terms($id, 'hsp_sat', array('fields' => 'all'));
    $item = array(
        'id'       => $id,
        't'        => $t,
        'time_utc' => gmdate('Y-m-d H:i:s', $t),
        'day'      => $lt->format('Y-m-d'),          // Tag und Uhrzeit in der Zeit, die Hesperos DX eingestellt hat
        'time'     => $lt->format('H:i:s'),
        'tz'       => hsp_sstv_tzlabel($lbl),
        'featured' => (bool) get_post_meta($id, '_hsp_featured', true),
        'station'  => (string) get_post_meta($id, '_hsp_station', true),
        'source'   => $terms && !is_wp_error($terms) ? $terms[0]->name : '',
        'slug'     => $terms && !is_wp_error($terms) ? $terms[0]->slug : '',
        'summary'  => $meta['summary'],
        'sections' => $secs,
        'full'     => $full ? array('url' => $full[0], 'w' => $full[1], 'h' => $full[2]) : null,
        'thumb'    => $thumb ? array('url' => $thumb[0], 'w' => $thumb[1], 'h' => $thumb[2]) : null,
    );
    return apply_filters('hsp_sstv_item', $item, $id);
}

function hsp_sstv_gallery_rest(WP_REST_Request $req)
{
    $o = hsp_sstv_opts();
    $per = max(1, min(100, (int) $req['per_page']));
    $args = array('post_type' => 'hsp_sstv', 'post_status' => 'publish', 'posts_per_page' => $per,
                  'paged' => max(1, (int) $req['page']), 'meta_key' => '_hsp_time', 'orderby' => 'meta_value_num',
                  'order' => $o['order'] === 'asc' ? 'ASC' : 'DESC');
    $tax = $req['quelle'] ? array(array('taxonomy' => 'hsp_sat', 'field' => 'slug', 'terms' => $req['quelle'])) : null;
    if ($tax) {
        $args['tax_query'] = $tax;
    }
    if ($req['station'] !== '' && $req['station'] !== null) {
        $args['meta_query'] = array(array('key' => '_hsp_station', 'value' => $req['station']));
    }
    $q = new WP_Query($args);
    $items = array_map('hsp_sstv_item', wp_list_pluck($q->posts, 'ID'));
    $out = array('items' => $items, 'total' => (int) $q->found_posts, 'pages' => (int) $q->max_num_pages);
    if ((int) $req['page'] === 1) {
        // Anordnung für das Skript (gilt auch für eine ins Theme kopierte Vorlage)
        $out['opts'] = array('group' => $o['group'], 'chips' => $o['chips'], 'order' => $o['order']);
        $fa = array('post_type' => 'hsp_sstv', 'post_status' => 'publish', 'posts_per_page' => 12, 'fields' => 'ids',
                    'no_found_rows' => true, 'meta_query' => array(array('key' => '_hsp_featured', 'value' => '1')),
                    'orderby' => 'date', 'order' => 'DESC');
        if ($tax) {
            $fa['tax_query'] = $tax;
        }
        $out['featured'] = array_map('hsp_sstv_item', get_posts($fa));
        $la = array('post_type' => 'hsp_sstv', 'post_status' => 'publish', 'posts_per_page' => 1, 'fields' => 'ids', 'no_found_rows' => true,
                    'meta_key' => '_hsp_time', 'orderby' => 'meta_value_num', 'order' => 'DESC');
        $latest = get_posts($la);                       // „zuletzt empfangen“ – auch bei „älteste zuerst“
        $out['latest'] = $latest ? hsp_sstv_item((int) $latest[0]) : null;
        $terms = get_terms(array('taxonomy' => 'hsp_sat', 'hide_empty' => true));
        $out['sources'] = array();
        foreach (is_wp_error($terms) ? array() : $terms as $tm) {
            $out['sources'][] = array('slug' => $tm->slug, 'name' => $tm->name, 'count' => (int) $tm->count);
        }
        $best = get_posts(array('post_type' => 'hsp_sstv', 'post_status' => 'publish', 'posts_per_page' => 1, 'fields' => 'ids',
                                'meta_key' => '_hsp_max_el', 'orderby' => 'meta_value_num', 'order' => 'DESC', 'no_found_rows' => true));
        $out['best'] = $best ? hsp_sstv_item((int) $best[0]) : null;
    }
    return $out;
}

/* ------------------------------------------------------------------ Kurzcode [sstv_galerie] */

function hsp_sstv_assets()
{
    $o = hsp_sstv_opts();
    if ($o['load_css']) {
        wp_enqueue_style('hsp-sstv', HSP_SSTV_URL . 'assets/galerie.css', array(), HSP_SSTV_VERSION);
        $vars = array();
        foreach (array('accent' => '--hsp-accent', 'card_bg' => '--hsp-card', 'text' => '--hsp-text', 'muted' => '--hsp-muted') as $k => $v) {
            if ($o[$k] !== '' && preg_match('/^#[0-9a-fA-F]{3,8}$/', $o[$k])) {
                $vars[] = $v . ':' . $o[$k];
            }
        }
        if ($o['radius'] !== '' && $o['radius'] !== null) {
            $vars[] = '--hsp-radius:' . (int) $o['radius'] . 'px';
        }
        if ($o['max_w'] !== '' && $o['max_w'] !== null) {
            $vars[] = '--hsp-max:' . (int) $o['max_w'] . 'px';
        }
        $vars[] = '--hsp-thumb:' . (int) $o['thumb'] . 'px';
        $vars[] = '--hsp-fade:' . (int) $o['fade_ms'] . 'ms';
        wp_add_inline_style('hsp-sstv', '.hsp-galerie,.hsp-lb{' . implode(';', $vars) . '}' . "\n" . wp_strip_all_tags($o['custom_css']));
    }
    wp_enqueue_script('hsp-sstv', HSP_SSTV_URL . 'assets/galerie.js', array(), HSP_SSTV_VERSION, true);
}

add_shortcode('sstv_galerie', function ($atts) {
    $o = hsp_sstv_opts();
    $a = shortcode_atts(array('quelle' => '', 'station' => '', 'pro_seite' => $o['per_page'], 'statistik' => 'ja'), $atts, 'sstv_galerie');
    // Seite für Links aus Hesperos DX: die Galerie ohne Filter (eine Seite nur mit ISS oder nur einem Pi zeigte nicht jedes Bild);
    // eine gefilterte Seite nur, solange es noch keine gültige gibt
    $cur = (int) get_option('hsp_sstv_page');
    $unfiltered = $a['quelle'] === '' && $a['station'] === '';
    if (get_the_ID() && $cur !== (int) get_the_ID() && ($unfiltered || !$cur || get_post_status($cur) !== 'publish')) {
        update_option('hsp_sstv_page', (int) get_the_ID());
    }
    hsp_sstv_assets();
    $hsp = array(
        'endpoint'  => esc_url_raw(add_query_arg($a['station'] !== '' ? array('station' => rawurlencode(sanitize_text_field($a['station']))) : array(),
                                                 rest_url(HSP_SSTV_NS . '/gallery'))),
        'per_page'  => max(1, min(100, (int) $a['pro_seite'])),
        'quelle'    => sanitize_title($a['quelle']),
        'station'   => sanitize_text_field($a['station']),
        'statistik' => $a['statistik'] !== 'nein',
        'credit'    => (bool) $o['credit'],
    );
    $tpl = locate_template('hamscanpi-sstv/galerie.php');
    ob_start();
    include $tpl ? $tpl : HSP_SSTV_DIR . 'templates/galerie.php';
    return ob_get_clean();
});

/* ------------------------------------------------------------------ Verwaltung */

add_filter('manage_hsp_sstv_posts_columns', function ($c) {
    $n = array();
    foreach ($c as $k => $v) {
        if ($k === 'title') {
            $n['hsp_thumb'] = 'Bild';
        }
        $n[$k] = $v;
    }
    $n['hsp_info'] = 'Empfang';
    $n['hsp_from'] = 'Eingesandt von';
    return $n;
});
add_action('manage_hsp_sstv_posts_custom_column', function ($col, $id) {
    if ($col === 'hsp_thumb') {
        echo get_the_post_thumbnail($id, array(80, 64));
    } elseif ($col === 'hsp_from') {
        $st = (string) get_post_meta($id, '_hsp_station', true);
        $us = (string) get_post_meta($id, '_hsp_user', true);
        if ($st !== '') {
            echo '<a href="' . esc_url(add_query_arg(array('post_type' => 'hsp_sstv', 'hsp_station' => $st), admin_url('edit.php'))) . '"><b>'
                 . esc_html($st) . '</b></a>';
        }
        echo $us !== '' ? '<br><small>' . esc_html($us) . '</small>' : '';
    } elseif ($col === 'hsp_info') {
        $m = json_decode((string) get_post_meta($id, '_hsp_meta', true), true);
        $s = is_array($m) && isset($m['summary']) ? $m['summary'] : array();
        echo esc_html(trim(sprintf('%s · %s %%%s', isset($s['mode']) ? $s['mode'] : '', isset($s['complete']) ? round($s['complete'] * 100) : '?',
                                   isset($s['max_el']) ? ' · max. ' . $s['max_el'] . '°' : '')));
        $t = (int) get_post_meta($id, '_hsp_time', true);
        if ($t) {
            $lt = hsp_sstv_local($t, $s);
            echo '<br>' . esc_html($lt->format('d.m.Y H:i') . ' ' . hsp_sstv_tzlabel($lt->format('T')));
        }
        if (get_post_meta($id, '_hsp_featured', true)) {
            echo '<br><b>★ hervorgehoben</b>';
        }
        if (get_post_meta($id, '_hsp_manual', true)) {
            echo '<br><i>von Hand nachgereicht</i>';
        }
    }
}, 10, 2);

add_action('add_meta_boxes_hsp_sstv', function () {
    remove_meta_box('tagsdiv-hsp_sat', 'hsp_sstv', 'side');      // Quelle: eigenes Feld (genau eine je Bild)
    add_meta_box('hsp_sstv_sort', 'Einordnung in der Galerie', 'hsp_sstv_sort_box', 'hsp_sstv', 'side', 'high');
    add_meta_box('hsp_sstv_info', 'Angaben von Hesperos DX', function ($post) {
        $m = json_decode((string) get_post_meta($post->ID, '_hsp_meta', true), true);
        if (!is_array($m) || empty($m['sections'])) {
            echo '<p>Keine Angaben.</p>';
            return;
        }
        echo get_the_post_thumbnail($post->ID, 'medium', array('style' => 'max-width:100%;height:auto;image-rendering:pixelated'));
        foreach ($m['sections'] as $s) {
            echo '<h4 style="margin:1em 0 .3em">' . esc_html($s['title']) . '</h4><table class="widefat striped"><tbody>';
            foreach ($s['rows'] as $r) {
                echo '<tr><th style="width:40%">' . esc_html($r[0]) . '</th><td>' . esc_html($r[1]) . '</td></tr>';
            }
            echo '</tbody></table>';
        }
    }, 'hsp_sstv', 'normal', 'high');
});

/** Liste der SSTV-Bilder nach Station (Pi) filtern. */
add_action('restrict_manage_posts', function ($type) {
    if ($type !== 'hsp_sstv') {
        return;
    }
    global $wpdb;
    $all = $wpdb->get_col($wpdb->prepare("SELECT DISTINCT meta_value FROM {$wpdb->postmeta} WHERE meta_key = %s ORDER BY meta_value", '_hsp_station'));
    if (count($all) < 2) {
        return;
    }
    $cur = isset($_GET['hsp_station']) ? sanitize_text_field(wp_unslash($_GET['hsp_station'])) : '';
    echo '<select name="hsp_station"><option value="">Alle Stationen</option>';
    foreach ($all as $st) {
        echo '<option value="' . esc_attr($st) . '"' . selected($cur, $st, false) . '>' . esc_html($st) . '</option>';
    }
    echo '</select>';
});
add_action('pre_get_posts', function ($q) {
    if (!is_admin() || !$q->is_main_query() || $q->get('post_type') !== 'hsp_sstv' || empty($_GET['hsp_station'])) {
        return;
    }
    $q->set('meta_query', array(array('key' => '_hsp_station', 'value' => sanitize_text_field(wp_unslash($_GET['hsp_station'])))));
});

/** Zugang für einen Pi: Benutzer mit der Rolle „SSTV-Melder“ und Anwendungspasswort anlegen (einmal angezeigt). */
add_action('admin_post_hsp_sstv_pi_user', function () {
    if (!current_user_can('create_users') || !check_admin_referer('hsp_sstv_pi_user')) {
        wp_die('Keine Berechtigung');
    }
    $login = sanitize_user(wp_unslash(isset($_POST['login']) ? $_POST['login'] : ''), true);
    $back = admin_url('options-general.php?page=hsp-sstv#pis');
    if ($login === '' || username_exists($login)) {
        set_transient('hsp_sstv_pi_msg_' . get_current_user_id(), array('err' => $login === '' ? 'Bitte einen Namen eingeben.' : 'Diesen Benutzer gibt es schon.'), 300);
        wp_safe_redirect($back);
        exit;
    }
    $host = wp_parse_url(home_url(), PHP_URL_HOST);
    $uid = wp_insert_user(array('user_login' => $login, 'user_pass' => wp_generate_password(32, true, true),
                                'user_email' => $login . '@hamscanpi.invalid', 'display_name' => $login,
                                'role' => 'hsp_sstv_melder', 'description' => 'Hesperos DX – sendet SSTV-Bilder (' . $host . ')'));
    if (is_wp_error($uid)) {
        set_transient('hsp_sstv_pi_msg_' . get_current_user_id(), array('err' => $uid->get_error_message()), 300);
        wp_safe_redirect($back);
        exit;
    }
    $pw = class_exists('WP_Application_Passwords') ? WP_Application_Passwords::create_new_application_password($uid, array('name' => 'Hesperos DX')) : null;
    if (!$pw || is_wp_error($pw)) {
        set_transient('hsp_sstv_pi_msg_' . get_current_user_id(), array('err' => 'Benutzer angelegt, aber kein Anwendungspasswort möglich (WordPress-Seite ohne https?) – unter Benutzer → ' . $login . ' von Hand erzeugen.'), 300);
    } else {
        set_transient('hsp_sstv_pi_msg_' . get_current_user_id(), array('login' => $login, 'pw' => $pw[0]), 300);
    }
    wp_safe_redirect($back);
    exit;
});

/** Empfangszeit, Quelle und „hervorheben“ je Bild – danach sortiert die Galerie das Bild neu ein. */
function hsp_sstv_sort_box($post)
{
    wp_nonce_field('hsp_sstv_sort', 'hsp_sstv_sort_nonce');
    $t = (int) get_post_meta($post->ID, '_hsp_time', true);
    $t = $t ? $t : (int) get_post_time('U', true, $post);
    $terms = wp_get_object_terms($post->ID, 'hsp_sat', array('fields' => 'names'));
    $src = $terms && !is_wp_error($terms) ? $terms[0] : '';
    $all = get_terms(array('taxonomy' => 'hsp_sat', 'hide_empty' => false, 'fields' => 'names'));
    ?>
    <p><label for="hsp_time"><b>Empfangszeit (UTC)</b></label><br>
       <input type="datetime-local" id="hsp_time" name="hsp_time" step="1" value="<?php echo esc_attr(gmdate('Y-m-d\TH:i:s', $t)); ?>" style="width:100%"></p>
    <p><label for="hsp_src"><b>Quelle</b> (Satellit, Band …)</label><br>
       <input type="text" id="hsp_src" name="hsp_src" list="hsp_src_list" value="<?php echo esc_attr($src); ?>" style="width:100%">
       <datalist id="hsp_src_list"><?php foreach (is_wp_error($all) ? array() : $all as $n) : ?><option value="<?php echo esc_attr($n); ?>"><?php endforeach; ?></datalist></p>
    <p><label><input type="checkbox" name="hsp_featured" value="1" <?php checked((bool) get_post_meta($post->ID, '_hsp_featured', true)); ?>>
       <b>hervorheben</b> – steht zusätzlich oben in der Galerie</label></p>
    <p class="description">Die Zeit wird in UTC eingegeben; angezeigt wird sie in der Galerie wie unter Einstellungen → SSTV-Galerie gewählt.</p>
    <?php
}

add_action('save_post_hsp_sstv', function ($id) {
    if (!isset($_POST['hsp_sstv_sort_nonce']) || !wp_verify_nonce(sanitize_text_field(wp_unslash($_POST['hsp_sstv_sort_nonce'])), 'hsp_sstv_sort')
        || (defined('DOING_AUTOSAVE') && DOING_AUTOSAVE) || !current_user_can('edit_post', $id)) {
        return;
    }
    $raw = isset($_POST['hsp_time']) ? sanitize_text_field(wp_unslash($_POST['hsp_time'])) : '';
    $d = $raw !== '' ? DateTime::createFromFormat('!Y-m-d\TH:i:s', strlen($raw) === 16 ? $raw . ':00' : $raw, new DateTimeZone('UTC')) : false;
    $t = $d ? $d->getTimestamp() : (int) get_post_meta($id, '_hsp_time', true);
    if (!$t) {                                          // ohne Zeit fehlte das Bild sonst in der Galerie
        $t = (int) get_post_time('U', true, $id);
    }
    update_post_meta($id, '_hsp_time', $t);
    $src = isset($_POST['hsp_src']) ? mb_substr(sanitize_text_field(wp_unslash($_POST['hsp_src'])), 0, 80) : '';
    wp_set_object_terms($id, $src !== '' ? $src : 'SSTV', 'hsp_sat');
    if (!empty($_POST['hsp_featured'])) {
        update_post_meta($id, '_hsp_featured', 1);
    } else {
        delete_post_meta($id, '_hsp_featured');
    }
});

/** Galerie-Seite anlegen: Seitenkopf wie die übrigen Seiten des Themes (page-hero mit eyebrow), darunter die Galerie. */
add_action('admin_post_hsp_sstv_page', function () {
    if (!current_user_can('publish_pages') || !check_admin_referer('hsp_sstv_page')) {
        wp_die('Keine Berechtigung');
    }
    $eyebrow = sanitize_text_field(wp_unslash(isset($_POST['eyebrow']) ? $_POST['eyebrow'] : ''));
    $title = sanitize_text_field(wp_unslash(isset($_POST['title']) ? $_POST['title'] : 'SSTV-Bilder'));
    $intro = sanitize_textarea_field(wp_unslash(isset($_POST['intro']) ? $_POST['intro'] : ''));
    $tpl = sanitize_text_field(wp_unslash(isset($_POST['template']) ? $_POST['template'] : ''));
    $hero = '<!-- wp:group {"className":"page-hero","layout":{"type":"constrained"}} -->' . "\n"
          . '<div class="wp-block-group page-hero"><div class="wp-block-group container">'
          . ($eyebrow !== '' ? '<p class="eyebrow">' . esc_html($eyebrow) . '</p>' : '')
          . '<h1>' . esc_html($title) . '</h1>' . ($intro !== '' ? '<p>' . esc_html($intro) . '</p>' : '') . "</div></div>\n<!-- /wp:group -->\n\n";
    $body = '<!-- wp:group {"className":"section","layout":{"type":"constrained"}} -->' . "\n"
          . '<div class="wp-block-group section"><div class="wp-block-group container"><!-- wp:shortcode -->' . "\n[sstv_galerie]\n"
          . '<!-- /wp:shortcode --></div></div>' . "\n<!-- /wp:group -->";
    $id = wp_insert_post(array('post_type' => 'page', 'post_status' => 'publish', 'post_title' => $title,
                               'post_content' => $hero . $body, 'page_template' => $tpl), true);
    if (!is_wp_error($id)) {
        update_option('hsp_sstv_page', (int) $id);
        wp_safe_redirect(get_edit_post_link($id, 'raw'));
        exit;
    }
    wp_die(esc_html($id->get_error_message()));
});

add_action('admin_menu', function () {
    add_options_page('SSTV-Galerie', 'SSTV-Galerie', 'manage_options', 'hsp-sstv', 'hsp_sstv_settings_page');
});

add_action('admin_init', function () {
    register_setting('hsp_sstv', 'hsp_sstv_opts', array('sanitize_callback' => function ($in) {
        $d = hsp_sstv_defaults();
        $o = array();
        $o['status_mode'] = in_array(isset($in['status_mode']) ? $in['status_mode'] : '', array('pi', 'draft', 'publish'), true) ? $in['status_mode'] : 'pi';
        $o['radius'] = (isset($in['radius']) && $in['radius'] !== '') ? max(0, min(40, (int) $in['radius'])) : '';
        $o['max_w'] = (isset($in['max_w']) && $in['max_w'] !== '') ? max(320, min(4000, (int) $in['max_w'])) : '';
        foreach (array('max_mb' => array(1, 50), 'per_page' => array(6, 100), 'thumb' => array(100, 600), 'fade_ms' => array(0, 3000)) as $k => $lim) {
            $o[$k] = max($lim[0], min($lim[1], isset($in[$k]) ? (int) $in[$k] : $d[$k]));
        }
        foreach (array('accent', 'card_bg', 'text', 'muted') as $k) {
            $v = isset($in[$k]) ? trim((string) $in[$k]) : '';
            $o[$k] = preg_match('/^#[0-9a-fA-F]{3,8}$/', $v) ? $v : '';
        }
        foreach (array('load_css', 'show_station', 'show_pi', 'credit') as $k) {
            $o[$k] = empty($in[$k]) ? 0 : 1;
        }
        $o['custom_css'] = isset($in['custom_css']) ? wp_strip_all_tags((string) $in['custom_css']) : '';
        $pick = function ($k, $allowed) use ($in, $d) {
            return isset($in[$k]) && in_array($in[$k], $allowed, true) ? $in[$k] : $d[$k];
        };
        $o['order'] = $pick('order', array('desc', 'asc'));
        $o['group'] = $pick('group', array('day', 'source', 'pass', 'none'));
        $o['date_zone'] = $pick('date_zone', array('station', 'utc', 'site'));
        $o['chips'] = $pick('chips', array('count', 'alpha', 'off'));
        $o['manual_status'] = $pick('manual_status', array('same', 'draft'));
        return $o;
    }));
});

function hsp_sstv_settings_page()
{
    $o = hsp_sstv_opts();
    $f = function ($k) { return 'hsp_sstv_opts[' . $k . ']'; };
    ?>
    <div class="wrap">
        <h1>SSTV-Galerie</h1>
        <p>Auf einer Seite den Kurzcode <code>[sstv_galerie]</code> einfügen. Möglich: <code>quelle="iss"</code> (nur eine Quelle),
           <code>station="Hesperos-AFGN"</code> (nur Bilder dieses Pi),
           <code>pro_seite="48"</code>, <code>statistik="nein"</code>. Neue Bilder von Hesperos DX erscheinen unter <a href="<?php echo esc_url(admin_url('edit.php?post_type=hsp_sstv')); ?>">SSTV-Bilder</a>.</p>
        <form method="post" action="options.php">
            <?php settings_fields('hsp_sstv'); ?>
            <table class="form-table" role="presentation">
                <tr><th>Neue Bilder</th><td><select name="<?php echo esc_attr($f('status_mode')); ?>">
                    <option value="pi" <?php selected($o['status_mode'], 'pi'); ?>>wie in Hesperos DX eingestellt</option>
                    <option value="draft" <?php selected($o['status_mode'], 'draft'); ?>>immer als Entwurf (ich gebe frei)</option>
                    <option value="publish" <?php selected($o['status_mode'], 'publish'); ?>>immer sofort veröffentlichen</option>
                </select></td></tr>
                <tr><th>Von Hand nachgereicht</th><td><select name="<?php echo esc_attr($f('manual_status')); ?>">
                    <option value="same" <?php selected($o['manual_status'], 'same'); ?>>wie automatisch empfangene</option>
                    <option value="draft" <?php selected($o['manual_status'], 'draft'); ?>>immer als Entwurf (ich gebe frei)</option>
                </select><p class="description">Bilder, die in Hesperos DX über „Auf Webseite“ in der Trefferkarte geschickt werden.</p></td></tr>
            </table>
            <h2>Anordnung</h2>
            <table class="form-table" role="presentation">
                <tr><th>Reihenfolge</th><td><select name="<?php echo esc_attr($f('order')); ?>">
                    <option value="desc" <?php selected($o['order'], 'desc'); ?>>neueste zuerst</option>
                    <option value="asc" <?php selected($o['order'], 'asc'); ?>>älteste zuerst</option></select></td></tr>
                <tr><th>Gruppieren</th><td><select name="<?php echo esc_attr($f('group')); ?>">
                    <option value="day" <?php selected($o['group'], 'day'); ?>>nach Tag</option>
                    <option value="source" <?php selected($o['group'], 'source'); ?>>nach Quelle (Satellit, Band)</option>
                    <option value="pass" <?php selected($o['group'], 'pass'); ?>>nach Überflug (Bilder einer Quelle mit weniger als 20 min Abstand)</option>
                    <option value="none" <?php selected($o['group'], 'none'); ?>>nicht gruppieren</option></select></td></tr>
                <tr><th>Datum und Uhrzeit in</th><td><select name="<?php echo esc_attr($f('date_zone')); ?>">
                    <option value="station" <?php selected($o['date_zone'], 'station'); ?>>wie in Hesperos DX eingestellt (UTC oder Ortszeit der Station)</option>
                    <option value="utc" <?php selected($o['date_zone'], 'utc'); ?>>immer UTC</option>
                    <option value="site" <?php selected($o['date_zone'], 'site'); ?>>Zeitzone dieser Seite (<?php echo esc_html(wp_timezone_string()); ?>)</option></select>
                    <p class="description">Jede Uhrzeit trägt ihre Kennung (UTC, MEZ, MESZ …).</p></td></tr>
                <tr><th>Filterknöpfe je Quelle</th><td><select name="<?php echo esc_attr($f('chips')); ?>">
                    <option value="count" <?php selected($o['chips'], 'count'); ?>>nach Anzahl der Bilder</option>
                    <option value="alpha" <?php selected($o['chips'], 'alpha'); ?>>alphabetisch</option>
                    <option value="off" <?php selected($o['chips'], 'off'); ?>>ausblenden</option></select>
                    <p class="description">Quellen umbenennen: <a href="<?php echo esc_url(admin_url('edit-tags.php?taxonomy=hsp_sat&post_type=hsp_sstv')); ?>">SSTV-Bilder → Quellen</a>. Empfangszeit, Quelle und „hervorheben“ je Bild beim Bearbeiten des Bildes.</p></td></tr>
            </table>
            <h2>Aussehen</h2>
            <table class="form-table" role="presentation">
                <tr><th>Akzentfarbe</th><td><input type="text" name="<?php echo esc_attr($f('accent')); ?>" value="<?php echo esc_attr($o['accent']); ?>" placeholder="leer = vom Theme"></td></tr>
                <tr><th>Kartenhintergrund</th><td><input type="text" name="<?php echo esc_attr($f('card_bg')); ?>" value="<?php echo esc_attr($o['card_bg']); ?>" placeholder="leer = vom Theme"></td></tr>
                <tr><th>Schrift</th><td><input type="text" name="<?php echo esc_attr($f('text')); ?>" value="<?php echo esc_attr($o['text']); ?>" placeholder="leer = vom Theme"></td></tr>
                <tr><th>Schrift gedämpft</th><td><input type="text" name="<?php echo esc_attr($f('muted')); ?>" value="<?php echo esc_attr($o['muted']); ?>" placeholder="leer = vom Theme"></td></tr>
                <tr><th>Ecken (px)</th><td><input type="number" name="<?php echo esc_attr($f('radius')); ?>" value="<?php echo esc_attr($o['radius']); ?>" min="0" max="40" placeholder="leer = vom Theme"></td></tr>
                <tr><th>Größte Breite (px)</th><td><input type="number" name="<?php echo esc_attr($f('max_w')); ?>" value="<?php echo esc_attr($o['max_w']); ?>" min="320" max="4000" placeholder="leer = vom Theme">
                    <p class="description">Leer: so breit wie der Inhaltsbereich des Themes – auch wenn der Kurzcode außerhalb eines Inhaltsrahmens steht.</p></td></tr>
                <tr><th>Vorschaubild mindestens (px)</th><td><input type="number" name="<?php echo esc_attr($f('thumb')); ?>" value="<?php echo (int) $o['thumb']; ?>" min="100" max="600"></td></tr>
                <tr><th>Überblenden (ms)</th><td><input type="number" name="<?php echo esc_attr($f('fade_ms')); ?>" value="<?php echo (int) $o['fade_ms']; ?>" min="0" max="3000"></td></tr>
                <tr><th>Bilder je Seite</th><td><input type="number" name="<?php echo esc_attr($f('per_page')); ?>" value="<?php echo (int) $o['per_page']; ?>" min="6" max="100"></td></tr>
                <tr><th>Größte Bilddatei (MB)</th><td><input type="number" name="<?php echo esc_attr($f('max_mb')); ?>" value="<?php echo (int) $o['max_mb']; ?>" min="1" max="50"></td></tr>
                <tr><th>Angaben zeigen</th><td>
                    <label><input type="checkbox" name="<?php echo esc_attr($f('show_station')); ?>" value="1" <?php checked($o['show_station']); ?>> Station (Rufzeichen, Locator)</label><br>
                    <label><input type="checkbox" name="<?php echo esc_attr($f('show_pi')); ?>" value="1" <?php checked($o['show_pi']); ?>> Raspberry Pi beim Empfang</label><br>
                    <label><input type="checkbox" name="<?php echo esc_attr($f('credit')); ?>" value="1" <?php checked($o['credit']); ?>> Zeile „Empfangen mit Hesperos DX · Ham Radio Watch“ unter der Galerie</label></td></tr>
                <tr><th>Plugin-CSS</th><td><label><input type="checkbox" name="<?php echo esc_attr($f('load_css')); ?>" value="1" <?php checked($o['load_css']); ?>> laden (aus = das Theme gestaltet alles selbst)</label></td></tr>
                <tr><th>Eigenes CSS</th><td><textarea name="<?php echo esc_attr($f('custom_css')); ?>" rows="6" cols="70" class="code"><?php echo esc_textarea($o['custom_css']); ?></textarea>
                    <p class="description">Z. B. <code>.hsp-galerie { --hsp-accent: #b22; --hsp-gap: 1.2rem; }</code> – alle Variablen stehen oben in assets/galerie.css. Die Vorlage lässt sich ins Theme kopieren: <code>&lt;theme&gt;/hamscanpi-sstv/galerie.php</code>.</p></td></tr>
            </table>
            <?php submit_button(); ?>
        </form>

        <h2 id="pis">Zugang für die Pis</h2>
        <p>Am besten bekommt jeder Pi einen eigenen Benutzer mit der Rolle <b>SSTV-Melder</b>: der darf nur Bilder einsenden –
           keine Beiträge, Seiten oder Einstellungen. In der Liste der SSTV-Bilder und im Protokoll steht dann, von welchem Pi
           (Stationsname aus Hesperos DX → Einstellungen → Station) und welchem Benutzer ein Bild kam. Einzelnen Zugang sperren:
           unter Benutzer das Anwendungspasswort widerrufen.</p>
        <?php $m = get_transient('hsp_sstv_pi_msg_' . get_current_user_id()); delete_transient('hsp_sstv_pi_msg_' . get_current_user_id()); ?>
        <?php if (!empty($m['pw'])) : ?>
            <div class="notice notice-success"><p><b>Benutzer <?php echo esc_html($m['login']); ?> angelegt.</b> In Hesperos DX unter
            Einstellungen → SSTV-Export eintragen – das Passwort erscheint nur jetzt:</p>
            <p>Benutzer: <code><?php echo esc_html($m['login']); ?></code> · Anwendungspasswort: <code style="font-size:1.1em"><?php echo esc_html($m['pw']); ?></code></p></div>
        <?php elseif (!empty($m['err'])) : ?>
            <div class="notice notice-error"><p><?php echo esc_html($m['err']); ?></p></div>
        <?php endif; ?>
        <?php $mel = get_users(array('role' => 'hsp_sstv_melder', 'fields' => array('ID', 'user_login'))); if ($mel) : ?>
            <p>Vorhandene SSTV-Melder: <?php echo implode(', ', array_map(function ($u) { return '<a href="' . esc_url(get_edit_user_link($u->ID)) . '">' . esc_html($u->user_login) . '</a>'; }, $mel)); ?></p>
        <?php endif; ?>
        <?php if (current_user_can('create_users')) : ?>
        <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
            <input type="hidden" name="action" value="hsp_sstv_pi_user"><?php wp_nonce_field('hsp_sstv_pi_user'); ?>
            <p><label>Name für den Pi <input type="text" name="login" placeholder="z. B. pi-afgn" class="regular-text" required></label>
            <?php submit_button('Benutzer mit Anwendungspasswort anlegen', 'secondary', 'submit', false); ?></p>
        </form>
        <?php endif; ?>

        <h2>Galerie-Seite anlegen</h2>
        <?php $page = (int) get_option('hsp_sstv_page'); if ($page && get_post_status($page)) : ?>
            <p>Die Galerie steht auf <a href="<?php echo esc_url(get_permalink($page)); ?>"><?php echo esc_html(get_the_title($page)); ?></a>
               (<a href="<?php echo esc_url(get_edit_post_link($page)); ?>">bearbeiten</a>).</p>
        <?php endif; ?>
        <p>Legt eine Seite mit Seitenkopf wie die übrigen Seiten an (Bereich <code>page-hero</code> mit kleiner Überschrift, Titel und
           Einleitung) und darunter die Galerie. Die Seite lässt sich danach wie jede andere im Blockeditor ändern und ins Menü aufnehmen.</p>
        <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
            <input type="hidden" name="action" value="hsp_sstv_page"><?php wp_nonce_field('hsp_sstv_page'); ?>
            <table class="form-table" role="presentation">
                <tr><th>Kleine Überschrift</th><td><input type="text" name="eyebrow" value="Satelliten · SSTV · Empfang" class="regular-text"></td></tr>
                <tr><th>Titel</th><td><input type="text" name="title" value="SSTV-Bilder" class="regular-text"></td></tr>
                <tr><th>Einleitung</th><td><textarea name="intro" rows="3" cols="70">Bilder, die mein Hesperos DX von Satelliten, auf Kurzwelle und im Live-Empfang aufgenommen hat – mit allen Angaben zu Überflug und Empfang. Antippen zum Vergrößern.</textarea></td></tr>
                <tr><th>Seitenvorlage</th><td><select name="template"><option value="">Standard</option>
                    <?php foreach (wp_get_theme()->get_page_templates() as $file => $name) : ?>
                        <option value="<?php echo esc_attr($file); ?>" <?php selected(stripos($name, 'editier') !== false || stripos($file, 'editable') !== false); ?>><?php echo esc_html($name); ?></option>
                    <?php endforeach; ?></select>
                    <p class="description">Am besten dieselbe Vorlage wie die übrigen Seiten (bei Themes mit Blockseiten meist die „editierbare“ Vorlage in voller Breite).</p></td></tr>
            </table>
            <?php submit_button('Seite anlegen', 'secondary'); ?>
        </form>

        <h2>Protokoll</h2>
        <?php $log = array_reverse((array) get_option('hsp_sstv_log', array())); ?>
        <?php if (!$log) : ?><p>Noch nichts empfangen.</p><?php else : ?>
        <table class="widefat striped"><thead><tr><th>Zeit (UTC)</th><th>Ergebnis</th><th>Quelle</th><th>Station (Pi)</th><th>Benutzer</th><th>Hinweis</th></tr></thead><tbody>
        <?php foreach (array_slice($log, 0, 50) as $e) : ?>
            <tr><td><?php echo esc_html(gmdate('Y-m-d H:i:s', (int) $e['t'])); ?></td><td><?php echo esc_html($e['result']); ?></td>
                <td><?php echo esc_html(isset($e['source']) ? $e['source'] : ''); ?><?php if (!empty($e['id'])) : ?> · <a href="<?php echo esc_url(get_edit_post_link((int) $e['id'])); ?>">#<?php echo (int) $e['id']; ?></a><?php endif; ?></td>
                <td><?php echo esc_html(isset($e['station']) ? $e['station'] : ''); ?></td>
                <td><?php echo esc_html($e['user']); ?></td><td><?php echo esc_html(isset($e['reason']) ? $e['reason'] : ''); ?></td></tr>
        <?php endforeach; ?></tbody></table>
        <?php endif; ?>
    </div>
    <?php
}
