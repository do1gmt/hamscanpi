# Hesperos DX – Dienste und Systemdateien: hamscanpi-* → hesperosdx-* (seit 1.8.15), Start und Neustart der Dienste.
# Wird von bin/setup_system.sh eingebunden (source) und nutzt dessen t/ok/warn/bold/asuser, PORT, DIR, RESTART.
#
#   migrate_files     vor der Einrichtung: Dateien, die nur den Namen wechseln (Systemeinstellungen, udev-Regeln),
#                     Zugangsdaten der Netzwerkfreigabe (/etc/hamscanpi → /etc/hesperosdx), alte Einhängepunkte lösen
#   start_services    am Ende: neue Dienste starten. Laufen noch die alten, sicherer Übergang:
#                     alte stoppen (Port frei) → neue starten → prüfen (alle aktiv, Dashboard antwortet) →
#                     erst dann die alten Dienste, die alte sudo-Regel und die alten Helfer entfernen.
#                     Starten die neuen nicht, laufen die alten weiter (Hinweis im Protokoll).
#                     Der alte Update-Dienst bleibt, solange das Update noch darin läuft – beim nächsten Mal weg.
#
# Für den Selbsttest: R (Wurzelordner statt /), SYSTEMCTL, PING, MOUNTPOINT, UMOUNT, WAIT_S.
R="${R:-}"
SYSTEMCTL="${SYSTEMCTL:-systemctl}"
SVC_NEW=hesperosdx
SVC_OLD=hamscanpi
SVC_PARTS="web scheduler rescue mdns"

_ms_ping() { if [ -n "${PING:-}" ]; then $PING; else curl -fs -m 3 "http://127.0.0.1:$PORT/api/ping" >/dev/null 2>&1; fi; }
_ms_log() { asuser python3 "$DIR/bin/applog.py" "$@" >/dev/null 2>&1 || true; }

migrate_files() {
    local pair a b m
    for pair in "systemd/journald.conf.d/$SVC_OLD.conf:systemd/journald.conf.d/$SVC_NEW.conf" \
                "systemd/logind.conf.d/$SVC_OLD.conf:systemd/logind.conf.d/$SVC_NEW.conf" \
                "NetworkManager/conf.d/$SVC_OLD-wifi.conf:NetworkManager/conf.d/$SVC_NEW-wifi.conf" \
                "udev/rules.d/20-$SVC_OLD-rtlsdr.rules:udev/rules.d/20-$SVC_NEW-rtlsdr.rules" \
                "udev/rules.d/90-$SVC_OLD-pmsdr.rules:udev/rules.d/90-$SVC_NEW-pmsdr.rules"; do
        a="$R/etc/${pair%%:*}"; b="$R/etc/${pair##*:}"
        if [ -f "$a" ]; then
            if [ -f "$b" ]; then rm -f "$a"; else mv "$a" "$b"; fi
        fi
    done
    # Zugangsdaten der Netzwerkfreigabe (nur root lesbar)
    if [ -d "$R/etc/$SVC_OLD" ]; then
        if [ ! -e "$R/etc/$SVC_NEW" ]; then mv "$R/etc/$SVC_OLD" "$R/etc/$SVC_NEW"
        else cp -an "$R/etc/$SVC_OLD/." "$R/etc/$SVC_NEW/" && rm -rf "$R/etc/$SVC_OLD"; fi
        chmod 700 "$R/etc/$SVC_NEW"
    fi
    # alte Einhängepunkte lösen: der Stick bzw. die Freigabe wird innerhalb einer Minute am neuen Ort eingehängt
    for m in usb nas; do
        if ${MOUNTPOINT:-mountpoint} -q "$R/mnt/$SVC_OLD-$m" 2>/dev/null; then
            sync
            ${UMOUNT:-umount} "$R/mnt/$SVC_OLD-$m" 2>/dev/null || ${UMOUNT:-umount} -l "$R/mnt/$SVC_OLD-$m" 2>/dev/null || true
        fi
        rmdir "$R/mnt/$SVC_OLD-$m" 2>/dev/null || true
    done
}

_ms_all_active() {
    local u
    for u in $SVC_PARTS; do $SYSTEMCTL is-active -q "$SVC_NEW-$u" || return 1; done
}

_ms_cleanup_old() {
    local h
    # Update-Dienst: beim Umstieg läuft das Update noch darin – dann bleibt er bis zum nächsten Mal
    if [ -f "$R/etc/systemd/system/$SVC_OLD-update.service" ] && ! $SYSTEMCTL is-active -q "$SVC_OLD-update"; then
        rm -f "$R/etc/systemd/system/$SVC_OLD-update.service"
    fi
    # sudo-Regel und Helfer mit altem Namen – nur, wenn die neuen da sind
    [ -f "$R/etc/sudoers.d/$SVC_NEW" ] && rm -f "$R/etc/sudoers.d/$SVC_OLD"
    for h in net store; do
        [ -f "$R/usr/local/sbin/$SVC_NEW-$h" ] && rm -f "$R/usr/local/sbin/$SVC_OLD-$h"
    done
    return 0
}

start_services() {
    local old="" u i up=0 NEW_UNITS
    NEW_UNITS="$SVC_NEW-web $SVC_NEW-scheduler $SVC_NEW-rescue $SVC_NEW-mdns"
    for u in $SVC_PARTS; do [ -f "$R/etc/systemd/system/$SVC_OLD-$u.service" ] && old="$old $u"; done
    $SYSTEMCTL daemon-reload
    $SYSTEMCTL enable $NEW_UNITS >/dev/null 2>&1
    if [ -z "$old" ]; then
        if [ "$RESTART" = 1 ]; then $SYSTEMCTL restart $NEW_UNITS; else $SYSTEMCTL start $NEW_UNITS; fi
        _ms_cleanup_old
        $SYSTEMCTL daemon-reload
        return 0
    fi
    bold "$(t "Dienste heißen jetzt hesperosdx-*" "Services are now called hesperosdx-*")"
    for u in $old; do $SYSTEMCTL stop "$SVC_OLD-$u" 2>/dev/null || true; done          # Ports 8080/8081 frei machen
    $SYSTEMCTL restart $NEW_UNITS
    for i in $(seq 1 "${WAIT_S:-30}"); do
        if _ms_all_active && _ms_ping; then up=1; break; fi
        sleep 1
    done
    if [ "$up" = 1 ]; then
        for u in $old; do
            $SYSTEMCTL disable "$SVC_OLD-$u" >/dev/null 2>&1 || true
            rm -f "$R/etc/systemd/system/$SVC_OLD-$u.service"
            rm -rf "$R/etc/systemd/system/$SVC_OLD-$u.service.d"
        done
        _ms_cleanup_old
        $SYSTEMCTL daemon-reload
        ok "$(t "neue Dienste laufen, die alten (hamscanpi-*) sind entfernt" "new services running, the old ones (hamscanpi-*) are removed")"
        _ms_log INFO setup services_renamed old="$SVC_OLD-*" new="$SVC_NEW-*"
        return 0
    fi
    # neue Dienste kommen nicht hoch: zurück zu den alten, nichts Altes gelöscht
    warn "$(t "neue Dienste starten nicht – die bisherigen (hamscanpi-*) laufen weiter. Bitte ein Diagnosepaket schicken." \
              "new services do not start – the previous ones (hamscanpi-*) keep running. Please send a diagnostics package.")"
    for u in $SVC_PARTS; do echo "    $SVC_NEW-$u: $($SYSTEMCTL is-active "$SVC_NEW-$u" 2>/dev/null)"; done
    $SYSTEMCTL stop $NEW_UNITS 2>/dev/null || true
    $SYSTEMCTL disable $NEW_UNITS >/dev/null 2>&1 || true
    for u in $old; do $SYSTEMCTL start "$SVC_OLD-$u" 2>/dev/null || true; done
    _ms_log ERROR setup services_rename_failed detail="neue Dienste nicht aktiv oder Dashboard antwortet nicht; alte laufen weiter"
    return 1
}

# Name im Netz (seit 1.8.16): ohne eigenen Stationsnamen heißt der Pi hesperosdx (hesperosdx.local), vorher hamscanpi.
# Mit eigenem Stationsnamen bleibt der daraus abgeleitete Name. Kein Übergang mit dem alten Namen – die neue Adresse
# steht im Update-Protokoll und als Hinweis im Dashboard; der Sicherungsordner zieht mit (storage.follow_rename).
# Für den Selbsttest: HOSTNAME_BIN, NETHELPER, PYRUN.
migrate_hostname() {
    local cur
    cur="$(${HOSTNAME_BIN:-hostname} 2>/dev/null)"
    [ "$cur" = "$SVC_OLD" ] || return 0
    if ${PYRUN:-asuser python3} -c "import sys; sys.path.insert(0, '$DIR/bin'); import station; sys.exit(0 if station.custom() else 1)" 2>/dev/null; then
        return 0                                       # eigener Stationsname: dessen Name gilt (wird nicht angefasst)
    fi
    if ${NETHELPER:-bash $DIR/share/root/hesperosdx-net} hostname "$SVC_NEW" >/dev/null 2>&1; then
        ok "$(t "Neue Adresse im Netz: http://$SVC_NEW.local:$PORT (vorher $SVC_OLD.local) – Lesezeichen bitte anpassen" \
                "New address in the network: http://$SVC_NEW.local:$PORT (was $SVC_OLD.local) – please update bookmarks")"
        ${PYRUN:-asuser python3} - "$DIR" "$PORT" <<'PY' >/dev/null 2>&1 || true
import sys
sys.path.insert(0, sys.argv[1] + "/bin")
import applog
applog.log("INFO", "setup", "hostname_changed", old="hamscanpi", new="hesperosdx")
try:
    import notices as NT
    NT.event("hostname_changed", "info", "system", "Neue Adresse im Netz: hesperosdx.local",
             f"Das Dashboard ist jetzt unter http://hesperosdx.local:{sys.argv[2]} erreichbar (vorher hamscanpi.local). "
             "Lesezeichen bitte anpassen; die IP-Adresse gilt weiter.")
except Exception as e:
    applog.log("WARN", "setup", "hostname_notice_failed", error=str(e))
try:
    import storage as STO
    STO.follow_rename("hamscanpi", "hesperosdx", applog.log)
except Exception as e:
    applog.log("WARN", "storage", "backup_dir_rename_failed", error=str(e))
PY
    else
        warn "$(t "Name im Netz ließ sich nicht auf $SVC_NEW umstellen – bleibt $SVC_OLD.local" "could not change the network name to $SVC_NEW – stays $SVC_OLD.local")"
        _ms_log WARN setup hostname_change_failed
    fi
}
