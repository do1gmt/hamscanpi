<?php
/**
 * Plugin Name:       HamScanPi SSTV-Galerie
 * Description:       Empfängt SSTV-Bilder von HamScanPi (Satellit, Kurzwelle, Live) mit allen Angaben zu Satellit, Überflug, Empfang, Station und Raspberry Pi und zeigt sie als Galerie – nach Tag gruppiert, Filter je Quelle, Lightbox mit Überblendung. Kurzcode: [sstv_galerie]
 * Version:           1.0.0
 * Requires at least: 5.6
 * Requires PHP:      7.4
 * Author:            HamScanPi
 * License:           GPL-3.0-or-later
 * Text Domain:       hsp-sstv
 *
 * Anpassen ohne das Plugin zu ändern:
 *  - Einstellungen → SSTV-Galerie: Farben, Rundung, Bildgröße, Überblenddauer, eigenes CSS, Plugin-CSS ganz aus.
 *  - Vorlage: templates/galerie.php ins Theme kopieren nach  <theme>/hamscanpi-sstv/galerie.php  und dort ändern.
 *  - CSS-Variablen (--hsp-…) im Theme überschreiben, z. B.  .hsp-galerie { --hsp-accent: #c00; }
 *  - Filter: hsp_sstv_item (ein Bild für die Ausgabe), hsp_sstv_insert_status (Status beim Empfang).
 */

if (!defined('ABSPATH')) {
    exit;
}

define('HSP_SSTV_VERSION', '1.0.0');
define('HSP_SSTV_DIR', plugin_dir_path(__FILE__));
define('HSP_SSTV_URL', plugin_dir_url(__FILE__));
define('HSP_SSTV_NS', 'hsp-sstv/v1');

/* ------------------------------------------------------------------ Einstellungen */

function hsp_sstv_defaults()
{
    return array(
        'status_mode' => 'pi',        // pi = wie HamScanPi es schickt | draft = immer Entwurf | publish = immer sofort
        'max_mb'      => 8,
        'per_page'    => 48,
        'accent'      => '#e8d28e',
        'card_bg'     => '',          // leer = vom Theme
        'text'        => '',
        'muted'       => '',
        'radius'      => 10,
        'thumb'       => 220,         // Mindestbreite eines Vorschaubilds in px
        'fade_ms'     => 450,
        'load_css'    => 1,
        'custom_css'  => '',
        'show_station'=> 1,
        'show_pi'     => 1,
    );
}

function hsp_sstv_opts()
{
    $o = get_option('hsp_sstv_opts', array());
    return wp_parse_args(is_array($o) ? $o : array(), hsp_sstv_defaults());
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

/* ------------------------------------------------------------------ Schnittstelle für HamScanPi */

add_action('rest_api_init', function () {
    register_rest_route(HSP_SSTV_NS, '/ping', array(
        'methods'             => 'GET',
        'permission_callback' => function () { return current_user_can('upload_files'); },
        'callback'            => 'hsp_sstv_ping',
    ));
    register_rest_route(HSP_SSTV_NS, '/images', array(
        'methods'             => 'POST',
        'permission_callback' => function () { return current_user_can('upload_files'); },
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
        'can_publish' => current_user_can('publish_posts'),
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
                  'max_el', 'el', 'snr_db', 'band');
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
    return array('ok' => true, 'id' => $id, 'status' => $p ? $p->post_status : '', 'duplicate' => $dup, 'link' => $link,
                 'edit' => get_edit_post_link($id, 'raw'));
}

function hsp_sstv_receive(WP_REST_Request $req)
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
    if ($want === 'publish' && !current_user_can('publish_posts')) {
        $want = 'pending';
    }
    $want = apply_filters('hsp_sstv_insert_status', $want, $meta);
    $source = !empty($sum['source']) ? $sum['source'] : 'SSTV';
    $title = sprintf('%s · %s · %s UTC', $source, !empty($sum['mode']) ? $sum['mode'] : 'SSTV', gmdate('Y-m-d H:i', $t));
    $gmt = gmdate('Y-m-d H:i:s', $t);
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
    $terms = wp_get_object_terms($id, 'hsp_sat', array('fields' => 'all'));
    $item = array(
        'id'       => $id,
        't'        => $t,
        'time_utc' => gmdate('Y-m-d H:i:s', $t),
        'day'      => gmdate('Y-m-d', $t),
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
    $per = max(1, min(100, (int) $req['per_page']));
    $args = array('post_type' => 'hsp_sstv', 'post_status' => 'publish', 'posts_per_page' => $per,
                  'paged' => max(1, (int) $req['page']), 'meta_key' => '_hsp_time', 'orderby' => 'meta_value_num', 'order' => 'DESC');
    if ($req['quelle']) {
        $args['tax_query'] = array(array('taxonomy' => 'hsp_sat', 'field' => 'slug', 'terms' => $req['quelle']));
    }
    $q = new WP_Query($args);
    $items = array_map('hsp_sstv_item', wp_list_pluck($q->posts, 'ID'));
    $out = array('items' => $items, 'total' => (int) $q->found_posts, 'pages' => (int) $q->max_num_pages);
    if ((int) $req['page'] === 1) {
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
        $vars[] = '--hsp-radius:' . (int) $o['radius'] . 'px';
        $vars[] = '--hsp-thumb:' . (int) $o['thumb'] . 'px';
        $vars[] = '--hsp-fade:' . (int) $o['fade_ms'] . 'ms';
        wp_add_inline_style('hsp-sstv', '.hsp-galerie,.hsp-lb{' . implode(';', $vars) . '}' . "\n" . wp_strip_all_tags($o['custom_css']));
    }
    wp_enqueue_script('hsp-sstv', HSP_SSTV_URL . 'assets/galerie.js', array(), HSP_SSTV_VERSION, true);
}

add_shortcode('sstv_galerie', function ($atts) {
    $o = hsp_sstv_opts();
    $a = shortcode_atts(array('quelle' => '', 'pro_seite' => $o['per_page'], 'statistik' => 'ja'), $atts, 'sstv_galerie');
    if (get_the_ID() && (int) get_option('hsp_sstv_page') !== (int) get_the_ID()) {
        update_option('hsp_sstv_page', (int) get_the_ID());     // für Links aus HamScanPi
    }
    hsp_sstv_assets();
    $hsp = array(
        'endpoint'  => esc_url_raw(rest_url(HSP_SSTV_NS . '/gallery')),
        'per_page'  => max(1, min(100, (int) $a['pro_seite'])),
        'quelle'    => sanitize_title($a['quelle']),
        'statistik' => $a['statistik'] !== 'nein',
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
    return $n;
});
add_action('manage_hsp_sstv_posts_custom_column', function ($col, $id) {
    if ($col === 'hsp_thumb') {
        echo get_the_post_thumbnail($id, array(80, 64));
    } elseif ($col === 'hsp_info') {
        $m = json_decode((string) get_post_meta($id, '_hsp_meta', true), true);
        $s = is_array($m) && isset($m['summary']) ? $m['summary'] : array();
        echo esc_html(trim(sprintf('%s · %s %%%s', isset($s['mode']) ? $s['mode'] : '', isset($s['complete']) ? round($s['complete'] * 100) : '?',
                                   isset($s['max_el']) ? ' · max. ' . $s['max_el'] . '°' : '')));
    }
}, 10, 2);

add_action('add_meta_boxes_hsp_sstv', function () {
    add_meta_box('hsp_sstv_info', 'Angaben von HamScanPi', function ($post) {
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

add_action('admin_menu', function () {
    add_options_page('SSTV-Galerie', 'SSTV-Galerie', 'manage_options', 'hsp-sstv', 'hsp_sstv_settings_page');
});

add_action('admin_init', function () {
    register_setting('hsp_sstv', 'hsp_sstv_opts', array('sanitize_callback' => function ($in) {
        $d = hsp_sstv_defaults();
        $o = array();
        $o['status_mode'] = in_array(isset($in['status_mode']) ? $in['status_mode'] : '', array('pi', 'draft', 'publish'), true) ? $in['status_mode'] : 'pi';
        foreach (array('max_mb' => array(1, 50), 'per_page' => array(6, 100), 'radius' => array(0, 40), 'thumb' => array(100, 600), 'fade_ms' => array(0, 3000)) as $k => $lim) {
            $o[$k] = max($lim[0], min($lim[1], isset($in[$k]) ? (int) $in[$k] : $d[$k]));
        }
        foreach (array('accent', 'card_bg', 'text', 'muted') as $k) {
            $v = isset($in[$k]) ? trim((string) $in[$k]) : '';
            $o[$k] = preg_match('/^#[0-9a-fA-F]{3,8}$/', $v) ? $v : '';
        }
        foreach (array('load_css', 'show_station', 'show_pi') as $k) {
            $o[$k] = empty($in[$k]) ? 0 : 1;
        }
        $o['custom_css'] = isset($in['custom_css']) ? wp_strip_all_tags((string) $in['custom_css']) : '';
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
           <code>pro_seite="48"</code>, <code>statistik="nein"</code>. Neue Bilder von HamScanPi erscheinen unter <a href="<?php echo esc_url(admin_url('edit.php?post_type=hsp_sstv')); ?>">SSTV-Bilder</a>.</p>
        <form method="post" action="options.php">
            <?php settings_fields('hsp_sstv'); ?>
            <table class="form-table" role="presentation">
                <tr><th>Neue Bilder</th><td><select name="<?php echo esc_attr($f('status_mode')); ?>">
                    <option value="pi" <?php selected($o['status_mode'], 'pi'); ?>>wie in HamScanPi eingestellt</option>
                    <option value="draft" <?php selected($o['status_mode'], 'draft'); ?>>immer als Entwurf (ich gebe frei)</option>
                    <option value="publish" <?php selected($o['status_mode'], 'publish'); ?>>immer sofort veröffentlichen</option>
                </select></td></tr>
                <tr><th>Akzentfarbe</th><td><input type="text" name="<?php echo esc_attr($f('accent')); ?>" value="<?php echo esc_attr($o['accent']); ?>" placeholder="#e8d28e"></td></tr>
                <tr><th>Kartenhintergrund</th><td><input type="text" name="<?php echo esc_attr($f('card_bg')); ?>" value="<?php echo esc_attr($o['card_bg']); ?>" placeholder="leer = vom Theme"></td></tr>
                <tr><th>Schrift</th><td><input type="text" name="<?php echo esc_attr($f('text')); ?>" value="<?php echo esc_attr($o['text']); ?>" placeholder="leer = vom Theme"></td></tr>
                <tr><th>Schrift gedämpft</th><td><input type="text" name="<?php echo esc_attr($f('muted')); ?>" value="<?php echo esc_attr($o['muted']); ?>" placeholder="leer = vom Theme"></td></tr>
                <tr><th>Ecken (px)</th><td><input type="number" name="<?php echo esc_attr($f('radius')); ?>" value="<?php echo (int) $o['radius']; ?>" min="0" max="40"></td></tr>
                <tr><th>Vorschaubild mindestens (px)</th><td><input type="number" name="<?php echo esc_attr($f('thumb')); ?>" value="<?php echo (int) $o['thumb']; ?>" min="100" max="600"></td></tr>
                <tr><th>Überblenden (ms)</th><td><input type="number" name="<?php echo esc_attr($f('fade_ms')); ?>" value="<?php echo (int) $o['fade_ms']; ?>" min="0" max="3000"></td></tr>
                <tr><th>Bilder je Seite</th><td><input type="number" name="<?php echo esc_attr($f('per_page')); ?>" value="<?php echo (int) $o['per_page']; ?>" min="6" max="100"></td></tr>
                <tr><th>Größte Bilddatei (MB)</th><td><input type="number" name="<?php echo esc_attr($f('max_mb')); ?>" value="<?php echo (int) $o['max_mb']; ?>" min="1" max="50"></td></tr>
                <tr><th>Angaben zeigen</th><td>
                    <label><input type="checkbox" name="<?php echo esc_attr($f('show_station')); ?>" value="1" <?php checked($o['show_station']); ?>> Station (Rufzeichen, Locator)</label><br>
                    <label><input type="checkbox" name="<?php echo esc_attr($f('show_pi')); ?>" value="1" <?php checked($o['show_pi']); ?>> Raspberry Pi beim Empfang</label></td></tr>
                <tr><th>Plugin-CSS</th><td><label><input type="checkbox" name="<?php echo esc_attr($f('load_css')); ?>" value="1" <?php checked($o['load_css']); ?>> laden (aus = das Theme gestaltet alles selbst)</label></td></tr>
                <tr><th>Eigenes CSS</th><td><textarea name="<?php echo esc_attr($f('custom_css')); ?>" rows="6" cols="70" class="code"><?php echo esc_textarea($o['custom_css']); ?></textarea>
                    <p class="description">Z. B. <code>.hsp-galerie { --hsp-accent: #b22; --hsp-gap: 1.2rem; }</code> – alle Variablen stehen oben in assets/galerie.css. Die Vorlage lässt sich ins Theme kopieren: <code>&lt;theme&gt;/hamscanpi-sstv/galerie.php</code>.</p></td></tr>
            </table>
            <?php submit_button(); ?>
        </form>
    </div>
    <?php
}
