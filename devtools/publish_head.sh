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
ALREADY=0
if git -C "$WORK" rev-parse -q --verify "refs/tags/v$V" >/dev/null; then
    ok "v$V ist schon auf GitHub – nur noch fehlende Releases nachtragen"
    ALREADY=1
fi
if [ "$ALREADY" = 0 ]; then

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
fi
cd "$WORK"
ok "auf GitHub: $(echo "$REPO" | sed -E 's#^git@github\.com:#https://github.com/#; s#\.git$##')"

# GitHub-Release (Kasten „Releases“ rechts auf der Projektseite) – braucht die GitHub-CLI „gh“, einmal angemeldet.
# Holt auch Releases für frühere Versionen nach, die nur als Tag vorhanden sind (höchstens die letzten 15; in
# aufsteigender Reihenfolge angelegt, so ist die neueste am Ende „Latest“).
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    for tg in $(git tag --sort=-v:refname | grep -E '^v[0-9]' | head -15 | tac); do
        gh release view "$tg" >/dev/null 2>&1 && continue
        vv="${tg#v}"
        git show "$tg:CHANGELOG.md" 2>/dev/null | awk -v v="$vv" '$0 ~ "^## "v" " {f=1; next} /^## / && f {exit} f' > "$TMP/notes.md"
        [ -s "$TMP/notes.md" ] || echo "HamScanPi $vv" > "$TMP/notes.md"
        if gh release create "$tg" --title "HamScanPi $vv" --notes-file "$TMP/notes.md" >/dev/null 2>&1; then
            ok "GitHub-Release HamScanPi $vv"
        else
            warn "Release $tg nicht angelegt"
        fi
    done
else
    warn "GitHub-Releases nicht angelegt: dafür einmalig  sudo apt install gh  und  gh auth login  (danach automatisch)"
fi
echo
echo "  Selbsttest auf GitHub: $(echo "$REPO" | sed -E 's#^git@github\.com:#https://github.com/#; s#\.git$##')/actions"
echo "  Die Pis holen die Version selbst: Dashboard → System → „Jetzt aktualisieren“ (oder bin/update.sh)."
exit 0
__PAYLOAD__
