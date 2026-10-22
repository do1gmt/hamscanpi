#!/bin/bash

set -u

BASE="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$BASE/log"
DB="$BASE/data/scanner.db"
STATUSFILE="$BASE/data/runtime_status.json"

mkdir -p "$LOG"

START_ISO="$(date -u --iso-8601=seconds)"
LOGT="$BASE/bin/applog.py"
echo $$ > "$BASE/data/scan.pid"
CANCELLED=0
on_term() { CANCELLED=1; [ -n "${SCANNER_PID:-}" ] && kill "$SCANNER_PID" 2>/dev/null; pkill -P $$ 2>/dev/null; exit 143; }
trap on_term TERM INT
START_EPOCH="$(date +%s)"

mkdir -p "$BASE/data"
sqlite3 "$DB" "CREATE TABLE IF NOT EXISTS run (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, duration_s INTEGER, status TEXT, peaks INTEGER DEFAULT 0, rejected INTEGER DEFAULT 0, error TEXT);" 2>/dev/null

RUN_ID="$(
sqlite3 "$DB" "
INSERT INTO run(started_at,status)
VALUES('$START_ISO','running');
SELECT last_insert_rowid();
"
)"

write_status() {
    local STATE="$1"
    local PHASE="$2"
    local PROGRESS="$3"
    local MESSAGE="$4"
    MESSAGE="${MESSAGE//\\/ }"
    MESSAGE="${MESSAGE//\"/\'}"
    local UPDATED
    UPDATED="$(date -u --iso-8601=seconds)"

    cat > "$STATUSFILE" <<JSON
{
  "state": "$STATE",
  "phase": "$PHASE",
  "progress": $PROGRESS,
  "message": "$MESSAGE",
  "run_id": $RUN_ID,
  "started_at": "$START_ISO",
  "updated_at": "$UPDATED"
}
JSON
}

TMP="$(mktemp)"

finish_run() {
    RC=$?

    END_ISO="$(date -u --iso-8601=seconds)"
    END_EPOCH="$(date +%s)"
    DUR=$((END_EPOCH - START_EPOCH))

    PEAKS="$(
        grep -E '^Peaks[[:space:]]*:' "$TMP" \
        | tail -1 \
        | awk -F: '{gsub(/ /,"",$2); print $2}'
    )"

    REJECTED="$(
        grep -E '^Verworfen[[:space:]]*:' "$TMP" \
        | tail -1 \
        | awk -F: '{gsub(/ /,"",$2); print $2}'
    )"

    [ -z "${PEAKS:-}" ] && PEAKS=0
    [ -z "${REJECTED:-}" ] && REJECTED=0

    if [ "$RC" -eq 0 ]; then
        STATUS="success"
    else
        STATUS="failed"
    fi

    sqlite3 "$DB" "
UPDATE run
SET
    finished_at='$END_ISO',
    duration_s=$DUR,
    status='$STATUS',
    peaks=$PEAKS,
    rejected=$REJECTED
WHERE id=$RUN_ID;
"

    if [ "$RC" -eq 0 ]; then
        write_status "done" "done" 100 "Scan fertig"
        python3 "$LOGT" INFO cycle run_done run_id="$RUN_ID" peaks="$PEAKS" rejected="$REJECTED" duration_s="$DUR"
    elif [ "$CANCELLED" -eq 1 ]; then
        write_status "error" "error" 100 "Scan vom Benutzer abgebrochen"
        python3 "$LOGT" WARN cycle run_cancelled run_id="$RUN_ID"
    else
        ERRLINE="$(grep -E 'Error|FEHLER|Fehler|Traceback|error|keine Daten|nicht gefunden' "$TMP" 2>/dev/null | grep -v '^  File' | tail -1 | cut -c1-200)"
        # SIGKILL (137) ohne eigene Fehlermeldung: fast immer der OOM-Killer (Speicher voll)
        if [ "$RC" -eq 137 ] && [ -z "$ERRLINE" ]; then
            ERRLINE="Scanner vom System beendet (SIGKILL) – meist Speicher voll, siehe: dmesg | grep -i oom"
        fi
        [ -z "$ERRLINE" ] && ERRLINE="$(grep -v '^\s*$' "$TMP" 2>/dev/null | tail -1 | cut -c1-200)"
        write_status "error" "error" 100 "Scan abgebrochen: ${ERRLINE:-Rückgabewert $RC}"
        TAIL="$(grep -v '^\s*$' "$TMP" 2>/dev/null | tail -12 | cut -c1-160 | tr '\n' '|')"
        python3 "$LOGT" ERROR cycle run_failed run_id="$RUN_ID" rc="$RC" error="${ERRLINE:-?}" output_tail="$TAIL"
    fi
    python3 "$BASE/bin/notify.py" scan_done "$RUN_ID" >/dev/null 2>&1 || true
    rm -f "$BASE/data/scan.pid"

    rm -f "$TMP"
    rm -f "${BAND_PLAN_FILE:-}"
    exit "$RC"
}

trap finish_run EXIT

echo "========================================"
echo "PMSDR Scan-Zyklus"
echo "$START_ISO"
echo "Run-ID: $RUN_ID"
echo "========================================"

write_status "running" "prepare" 2 "Scan wird vorbereitet"

BAND_PLAN_FILE="$(mktemp)"

"$BASE/bin/scan_plan.py" --tsv \
    > "$BAND_PLAN_FILE"

TOTAL_TUNES="$(
    awk '
        {
            total += $2
        }

        END {
            print total + 0
        }
    ' "$BAND_PLAN_FILE"
)"

if [ "${TOTAL_TUNES:-0}" -lt 1 ]; then
    TOTAL_TUNES=1
fi

echo
echo "Aktiver Scanplan:"
cat "$BAND_PLAN_FILE"
echo "Abstimmungen gesamt: $TOTAL_TUNES"
echo

START_SCAN_ID="$(
sqlite3 "$DB" "SELECT COALESCE(MAX(id),0) FROM scan;"
)"

write_status "running" "scan" 5 "Bänder werden abgesucht"
python3 "$LOGT" INFO cycle run_start run_id="$RUN_ID" tunes="$TOTAL_TUNES"

PYTHONUNBUFFERED=1 "$BASE/bin/scanner.py" --once >"$TMP" 2>&1 &
SCANNER_PID=$!

(
    while kill -0 "$SCANNER_PID" 2>/dev/null; do

        CURRENT_SCAN_ID="$(
            sqlite3 "$DB" "SELECT COALESCE(MAX(id),0) FROM scan;"
        )"

        DONE=$((CURRENT_SCAN_ID - START_SCAN_ID))

        if [ "$DONE" -lt 0 ]; then
            DONE=0
        fi

        if [ "$DONE" -gt "$TOTAL_TUNES" ]; then
            DONE="$TOTAL_TUNES"
        fi

        PROGRESS=$((5 + DONE * 70 / TOTAL_TUNES))


        # Tatsächlich aktuell gescanntes Band direkt
        # aus der Scanner-Ausgabe lesen.
        BAND="$(
            grep -E '^Band[[:space:]]*:' "$TMP" \
            | tail -1 \
            | sed -E 's/^Band[[:space:]]*:[[:space:]]*([^[:space:]]+).*/\1/'
        )"

        if [ -z "$BAND" ]; then
            BAND="Scan"
        fi

        write_status \
            "running" \
            "scan" \
            "$PROGRESS" \
            "$BAND scannt $DONE/$TOTAL_TUNES"

        sleep 2
    done
) &
PROGRESS_PID=$!

wait "$SCANNER_PID"
SCANNER_RC=$?

kill "$PROGRESS_PID" 2>/dev/null || true
wait "$PROGRESS_PID" 2>/dev/null || true

cat "$TMP"

if [ "$SCANNER_RC" -ne 0 ]; then
    exit "$SCANNER_RC"
fi

echo
echo "Aufzeichnungen auswerten ..."
write_status "running" "analyze" 84 "Treffer werden ausgewertet"
"$BASE/bin/analyze_recordings.py" || python3 "$LOGT" WARN cycle step_failed step=analyze_recordings

echo
echo "FM auf 10 m / 11 m erzeugen ..."
write_status "running" "fm" 87 "FM-Aufnahmen werden erzeugt"
"$BASE/bin/analyze_fm.py" || python3 "$LOGT" WARN cycle step_failed step=analyze_fm

echo
echo "VHF/UHF-Audio und APRS/POCSAG ..."
write_status "running" "fm" 88 "VHF/UHF-Aufnahmen werden ausgewertet"
"$BASE/bin/analyze_vhf.py" || python3 "$LOGT" WARN cycle step_failed step=analyze_vhf

echo
echo "Signale dekodieren ..."
write_status "running" "decode" 90 "Bekannte Signale werden dekodiert"
"$BASE/bin/decode_recordings.py" || python3 "$LOGT" WARN cycle step_failed step=decode_recordings

echo
echo "FT8-Nachrichten dekodieren ..."
write_status "running" "ft8decode" 93 "FT8-Nachrichten werden dekodiert"
"$BASE/bin/decode_ft8_real.py" || python3 "$LOGT" WARN cycle step_failed step=decode_ft8

echo
echo "Abschluss ..."
write_status "running" "report" 98 "Ergebnisse werden bereitgestellt"
# Das Dashboard liest alles live aus der Datenbank (webapp.py),
# ein statischer Report wird nicht mehr erzeugt.

echo
echo "Zyklus beendet:"
date -u --iso-8601=seconds
