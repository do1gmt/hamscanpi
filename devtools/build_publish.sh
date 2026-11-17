#!/usr/bin/env bash
# Baut die selbstentpackende Datei hesperos-publish.sh aus diesem Arbeitsstand (Veröffentlichen auf GitHub vom PC).
#   bash devtools/build_publish.sh "Kurzbeschreibung"           → ./hesperos-publish.sh
#
# Vorgabe: nur die Änderungen gegenüber dem Stand auf GitHub (meist wenige KB statt des ganzen Projekts). Dafür muss
# dieser Arbeitsstand ein git-Klon sein; verglichen wird mit origin/main (vorher geholt) oder HSP_BASE=<commit>.
# Die Datei prüft beim Veröffentlichen, ob GitHub noch genau auf diesem Stand ist, und das Ergebnis per Prüfsumme.
#   FULL=1 bash devtools/build_publish.sh "…"                    → das ganze Projekt (wie früher), passt auf jeden Stand
# Aufbau: devtools/publish_head.sh + base64(tar.gz). Delta: .hsp-delta/{BASE,BASE_VERSION,VERSION,TREE,deleted,text.patch,files/}.
set -euo pipefail
cd "$(dirname "$0")/.."
MSG="${1:-Hesperos DX $(cat VERSION)}"
OUT="${OUT:-$PWD/hesperos-publish.sh}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
# was nie mitgeht (außer dem, was .gitignore schon ausschließt)
EXCL=(':(exclude)hamscanpi-update.sh' ':(exclude)hamscanpi-publish.sh' ':(exclude)hesperos-update.sh'
      ':(exclude)hesperos-publish.sh' ':(exclude)hesperos-migrate.sh' ':(exclude,glob)**/*.bak*' ':(exclude)fake_rtl_hf.*')
KIND=full
if [ "${FULL:-0}" != 1 ] && git rev-parse --git-dir >/dev/null 2>&1; then
    [ -n "${HSP_BASE:-}" ] || git fetch -q origin main 2>/dev/null || echo "  (GitHub nicht erreichbar – vergleiche mit dem zuletzt geholten Stand)"
    BASE="$(git rev-parse --verify -q "${HSP_BASE:-origin/main}^{commit}" || true)"
    if [ -n "$BASE" ]; then
        KIND=delta
        mkdir -p "$TMP/pl/.hsp-delta"
        IDX="$TMP/index"
        GIT_INDEX_FILE="$IDX" git read-tree "$BASE"
        GIT_INDEX_FILE="$IDX" git add -A -- . "${EXCL[@]}"
        TREE="$(GIT_INDEX_FILE="$IDX" git write-tree)"
        # Textdateien als Patch (nur die geänderten Zeilen), Binärdateien ganz (ein Binär-Patch trüge auch den alten
        # Inhalt mit), gelöschte in einer Liste
        GIT_INDEX_FILE="$IDX" git diff --cached --no-renames --numstat -z --diff-filter=d "$BASE" \
            | python3 -c 'import sys; d=sys.stdin.buffer.read().split(b"\0"); [sys.stdout.buffer.write(x.split(b"\t",2)[2]+b"\0") for x in d if x.startswith(b"-\t-\t")]' > "$TMP/binary"
        GIT_INDEX_FILE="$IDX" git diff --cached --no-renames --name-only -z --diff-filter=d "$BASE" > "$TMP/changed"
        GIT_INDEX_FILE="$IDX" git diff --cached --no-renames --name-only -z --diff-filter=D "$BASE" > "$TMP/pl/.hsp-delta/deleted"
        [ -s "$TMP/changed" ] || [ -s "$TMP/pl/.hsp-delta/deleted" ] || { echo "keine Änderungen gegenüber GitHub – nichts zu bauen"; exit 1; }
        python3 - "$TMP/changed" "$TMP/binary" "$TMP/text" <<'PYX'
import sys
ch = [x for x in open(sys.argv[1], "rb").read().split(b"\0") if x]
bi = set(x for x in open(sys.argv[2], "rb").read().split(b"\0") if x)
open(sys.argv[3], "wb").write(b"".join(x + b"\0" for x in ch if x not in bi))
PYX
        mkdir -p "$TMP/pl/.hsp-delta/files"
        [ -s "$TMP/binary" ] && tar cf - --null -T "$TMP/binary" | tar xf - -C "$TMP/pl/.hsp-delta/files"
        if [ -s "$TMP/text" ]; then
            xargs -0 -a "$TMP/text" sh -c 'GIT_INDEX_FILE="$0" git diff --cached --no-renames --full-index "$1" -- "$@"' "$IDX" "$BASE" \
                > "$TMP/pl/.hsp-delta/text.patch"
        fi
        echo "$BASE" > "$TMP/pl/.hsp-delta/BASE"
        echo "$TREE" > "$TMP/pl/.hsp-delta/TREE"
        cat VERSION > "$TMP/pl/.hsp-delta/VERSION"
        git show "$BASE:VERSION" > "$TMP/pl/.hsp-delta/BASE_VERSION" 2>/dev/null || echo "?" > "$TMP/pl/.hsp-delta/BASE_VERSION"
        tar czf "$TMP/payload.tgz" -C "$TMP/pl" .hsp-delta
        CHANGED="$(GIT_INDEX_FILE="$IDX" git diff --cached --no-renames --name-only "$BASE" | wc -l)"
    fi
fi
if [ "$KIND" = full ]; then
    tar czf "$TMP/payload.tgz" --exclude='./.git' --exclude='./data' --exclude='./log' --exclude='./recordings' \
        --exclude='./reports' --exclude='./restore-previous' --exclude='./backup' --exclude='./hamscanpi-update.sh' \
        --exclude='./hamscanpi-publish.sh' --exclude='./hesperos-update.sh' --exclude='./hesperos-publish.sh' \
        --exclude='./hesperos-migrate.sh' --exclude='./config/auth.json' --exclude='./config/scanner.json' \
        --exclude='./config/web_settings.json' --exclude='*.bak*' --exclude='./fake_rtl_hf.*' .
fi
{ sed "0,/^set -e\$/s//set -e\nHSP_MSG=\"${MSG//\//\\/}\"/" devtools/publish_head.sh; base64 "$TMP/payload.tgz"; } > "$OUT"
bash -n "$OUT"
if [ "$KIND" = delta ]; then
    echo "gebaut: $OUT  (Version $(cat VERSION), nur Änderungen seit $(cat "$TMP/pl/.hsp-delta/BASE_VERSION"): $CHANGED Dateien, $(du -h "$OUT" | cut -f1))"
else
    echo "gebaut: $OUT  (Version $(cat VERSION), vollständig, $(du -h "$OUT" | cut -f1))"
fi
