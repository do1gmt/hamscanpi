#!/bin/bash
# HamScanPi – neue Version auf GitHub veröffentlichen. Läuft auf einem PC mit git und GitHub-Zugang (SSH-Schlüssel),
# nicht auf dem Pi. Die Pis holen sich die Version dann selbst (Dashboard: „Jetzt aktualisieren“ oder bin/update.sh).
#
#   bash hamscanpi-publish.sh
#
# Arbeitskopie: Ordner „hamscanpi“ neben dieser Datei (beim ersten Mal per git clone angelegt, danach aktualisiert).
# Andere GitHub-Adresse: HSP_REPO=git@github.com:<konto>/hamscanpi.git bash hamscanpi-publish.sh
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="${HSP_WORK:-$HERE/hamscanpi}"
REPO="${HSP_REPO:-git@github.com:do1gmt/hamscanpi.git}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$*"; exit 1; }
command -v git >/dev/null || die "git fehlt: sudo apt install git"

sed -n '/^__PAYLOAD__$/,$p' "$0" | tail -n +2 | base64 -d > "$TMP/p.tgz"
mkdir -p "$TMP/new" && tar xzf "$TMP/p.tgz" -C "$TMP/new"
V="$(cat "$TMP/new/VERSION")"
bold "HamScanPi $V veröffentlichen"
echo "  $HSP_MSG"

bold "1/4 Arbeitskopie"
if [ -d "$WORK/.git" ]; then
    git -C "$WORK" fetch -q origin || die "GitHub nicht erreichbar (ssh -T git@github.com prüfen)"
    git -C "$WORK" checkout -q main
    git -C "$WORK" reset -q --hard origin/main
    git -C "$WORK" clean -qfd
    ok "aktualisiert: $WORK"
else
    git clone -q "$REPO" "$WORK" || die "git clone $REPO fehlgeschlagen (ssh -T git@github.com prüfen)"
    ok "angelegt: $WORK"
fi
OLDV="$(cat "$WORK/VERSION" 2>/dev/null || echo ?)"
[ -n "$(git -C "$WORK" config --get user.name)" ] || die "git kennt deinen Namen nicht: git config --global user.name \"…\" (und user.email)"
if git -C "$WORK" rev-parse -q --verify "refs/tags/v$V" >/dev/null; then
    die "v$V ist schon auf GitHub – nichts zu tun"
fi

bold "2/4 Neuer Stand"
# alles außer .git ersetzen: so verschwinden auch Dateien, die es im neuen Stand nicht mehr gibt
find "$WORK" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
cp -a "$TMP/new/." "$WORK/"
cd "$WORK"
git add -A
BAD="$(git diff --cached --name-only | grep -E '(^|/)auth\.json$|(^|/)web_settings\.json$|^config/scanner\.json$|^(data|recordings|reports|log|backup)/' || true)"
[ -z "$BAD" ] || { git reset -q --hard; die "persönliche Dateien wären im Repository gelandet: $BAD"; }
GONE="$(git diff --cached --name-only --diff-filter=D)"
if [ -n "$GONE" ]; then
    warn "Diese Dateien gibt es im neuen Stand nicht mehr und werden auf GitHub gelöscht:"
    echo "$GONE" | sed 's/^/      /'
    read -r -p "  Trotzdem veröffentlichen? [j/N] " a
    [[ "$a" =~ ^[jJyY]$ ]] || { git reset -q --hard; die "abgebrochen – nichts geändert"; }
fi
git diff --cached --stat | tail -1 | sed 's/^/  /'

bold "3/4 Festschreiben"
git commit -q -m "$HSP_MSG"
git tag -a "v$V" -m "HamScanPi $V"
ok "$OLDV → $V"

bold "4/4 Hochladen"
git push -q origin main || die "push fehlgeschlagen – nichts veröffentlicht (Arbeitskopie: $WORK)"
git push -q origin "v$V" || warn "Tag v$V nicht hochgeladen"
ok "auf GitHub: $(echo "$REPO" | sed -E 's#^git@github\.com:#https://github.com/#; s#\.git$##')"
echo
echo "  Selbsttest auf GitHub: $(echo "$REPO" | sed -E 's#^git@github\.com:#https://github.com/#; s#\.git$##')/actions"
echo "  Die Pis holen die Version selbst: Dashboard → System → „Jetzt aktualisieren“ (oder bin/update.sh)."
exit 0
__PAYLOAD__
