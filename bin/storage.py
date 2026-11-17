#!/usr/bin/env python3
"""Sicherungsziele: USB-Stick/-Festplatte und Netzwerkfreigabe (NAS, Windows-Freigabe, FRITZ!Box) – im Dashboard
unter System → Sicherung.

USB: Ein angesteckter Datenträger erscheint in der Liste; „Für Sicherungen verwenden“ hängt ihn nach
/mnt/hamscanpi-usb ein und merkt sich seine UUID. Wird er abgezogen und wieder angesteckt (auch nach einem
Neustart), hängt ihn der Scheduler binnen einer Minute selbst wieder ein. „Auswerfen“ hängt ihn sauber aus;
bis er abgezogen wurde, wird er dann nicht wieder eingehängt. Formatiert wird nie etwas.

NAS: Adresse //rechner/freigabe, Benutzer, Passwort. Das Passwort liegt nur in /etc/hamscanpi/nas.cred (nur root
kann es lesen) – nicht in den Einstellungen und nicht in Sicherungen. Eingehängt nach /mnt/hamscanpi-nas; fällt
die Verbindung weg, wird alle 10 min neu versucht.

Die Sicherungen landen in <ziel>/HamScanPi-Sicherungen/<rechnername>/ – so können mehrere HamScanPis denselben
Stick oder dieselbe Freigabe benutzen, ohne sich gegenseitig alte Sicherungen wegzuräumen.
Root-Handgriffe über /usr/local/sbin/hamscanpi-store (sudo-Regel nur für diese Datei).
"""
import json
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
HELPER = "/usr/local/sbin/hamscanpi-store"
USB_MP = Path("/mnt/hamscanpi-usb")
NAS_MP = Path("/mnt/hamscanpi-nas")
SUB = "HamScanPi-Sicherungen"
CRED = Path("/etc/hamscanpi/nas.cred")
STATE = BASE / "data" / "storage_state.json"
FS_OK = ("vfat", "exfat", "ntfs", "ext2", "ext3", "ext4", "btrfs", "xfs")
FS_NAME = {"vfat": "FAT32", "exfat": "exFAT", "ntfs": "NTFS"}
NAS_RETRY_S = 600
BACKUP_RE = re.compile(r"^hamscanpi-backup-\d{8}-\d{4}-(settings|data|all)\.tar\.gz$")


def _run(cmd, timeout=30, input=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=input)
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except Exception as e:
        return 99, "", f"{type(e).__name__}: {e}"


def helper(*args, input=None, timeout=60):
    if not Path(HELPER).exists():
        return False, "Erst einmal „Jetzt aktualisieren“ (System → Update) – dabei wird der Helfer für USB-Stick und Netzwerkfreigabe eingerichtet"
    rc, out, err = _run(["sudo", "-n", HELPER, *[str(a) for a in args]], timeout=timeout, input=input)
    msg = (err or out).strip()
    if rc != 0 and "password is required" in msg:
        msg = "sudo-Regel fehlt – einmal „Jetzt aktualisieren“ (System → Update)"
    return rc == 0, msg


def _fail(msg):
    return (msg.splitlines() or ["?"])[-1]


# ------------------------------------------------------------------ Zustand
def read_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(st):
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(st), encoding="utf-8")
    except OSError:
        pass


def _settings():
    try:
        return json.loads((BASE / "config" / "web_settings.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def target(settings=None):
    """Gewähltes Ziel aus den Einstellungen: {"kind": "usb"|"nas"|"", "uuid", "label", "share", "user", "domain"}."""
    s = settings if settings is not None else _settings()
    t = dict(((s.get("auto_backup") or {}).get("target")) or {})
    t.setdefault("kind", "")
    return t


def host_dir(mp):
    """Ordner für die Sicherungen dieses Pi auf einem eingehängten Ziel."""
    return Path(mp) / SUB / (socket.gethostname() or "hamscanpi")


def mounted(mp):
    try:
        mp = str(mp)
        with open("/proc/mounts", encoding="utf-8") as f:
            return any(line.split()[1].replace("\\040", " ") == mp for line in f if len(line.split()) > 1)
    except OSError:
        return False


def _free_gb(p):
    try:
        return round(shutil.disk_usage(str(p)).free / 1e9, 1)
    except OSError:
        return None


# ------------------------------------------------------------------ USB
def _root_disk():
    rc, out, _ = _run(["findmnt", "-n", "-o", "SOURCE", "/"], timeout=5)
    src = out.strip()
    if not src:
        return None
    rc, out, _ = _run(["lsblk", "-no", "PKNAME", src], timeout=5)
    return (out.strip().splitlines() or [None])[0] or Path(src).name


def drives():
    """Angesteckte Datenträger mit Dateisystem (ohne die Systemkarte): [{uuid, label, fs, size_gb, model, mounted_at,
    supported, path}]."""
    rc, out, _ = _run(["lsblk", "-J", "-b", "-o", "NAME,PATH,UUID,FSTYPE,LABEL,SIZE,MOUNTPOINT,TRAN,RM,HOTPLUG,TYPE,MODEL"],
                      timeout=10)
    if rc != 0:
        return []
    try:
        tree = json.loads(out).get("blockdevices") or []
    except ValueError:
        return []
    rootd = _root_disk()
    res = []

    def walk(node, disk):
        for ch in node.get("children") or []:
            walk(ch, disk)
        if node.get("type") not in ("part", "disk") or not node.get("fstype") or not node.get("uuid"):
            return
        if node.get("fstype") in ("swap", "crypto_LUKS", "LVM2_member", "linux_raid_member", "squashfs", "iso9660"):
            return
        res.append({"uuid": node["uuid"], "label": node.get("label") or "", "fs": node["fstype"],
                    "fs_name": FS_NAME.get(node["fstype"], node["fstype"]),
                    "size_gb": round(int(node.get("size") or 0) / 1e9, 1), "model": (disk.get("model") or "").strip(),
                    "mounted_at": node.get("mountpoint") or "", "supported": node["fstype"] in FS_OK,
                    "path": node.get("path") or ""})

    for d in tree:
        if d.get("name") == rootd or d.get("type") != "disk" or str(d.get("name", "")).startswith(("loop", "zram", "ram")):
            continue
        if not (d.get("tran") == "usb" or str(d.get("rm")) in ("1", "True", "true") or str(d.get("hotplug")) in ("1", "True", "true")):
            continue
        walk(d, d)
    return res


def use_usb(uuid, label=""):
    """Datenträger einhängen und als Ziel merken. Rückgabe: Zielordner."""
    uuid = str(uuid or "")
    if not re.fullmatch(r"[A-Za-z0-9-]{4,36}", uuid):
        raise ValueError("ungültiger Datenträger")
    if mounted(USB_MP):
        cur = target()
        if cur.get("kind") != "usb" or cur.get("uuid") != uuid:
            helper("umount-usb")
    ok, out = helper("mount-usb", uuid)
    if not ok:
        raise ValueError(_fail(out) or "Einhängen fehlgeschlagen")
    st = read_state()
    st.pop("ejected", None)
    _write_state(st)
    d = host_dir(USB_MP)
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def eject_usb():
    ok, out = helper("umount-usb")
    if not ok:
        raise ValueError(_fail(out))
    st = read_state()
    st["ejected"] = target().get("uuid") or True
    _write_state(st)


# ------------------------------------------------------------------ NAS
def norm_share(share):
    s = str(share or "").strip().replace("\\", "/")
    if s.lower().startswith("smb://"):
        s = "//" + s[6:]
    if not s.startswith("//"):
        s = "//" + s.lstrip("/")
    s = s.rstrip("/")
    if not re.fullmatch(r"//[A-Za-z0-9._-]{1,64}/[^\x00-\x1f,\"'\\]{1,120}", s) or ".." in s:
        raise ValueError("Adresse bitte so: //rechner/freigabe (z. B. //192.168.178.1/FRITZ.NAS)")
    return s


def use_nas(share, user="", password=None, domain=""):
    """Zugangsdaten speichern (nur wenn ein Passwort mitkommt) und einhängen. Rückgabe: (share, Zielordner)."""
    share = norm_share(share)
    user, domain = str(user or "").strip(), str(domain or "").strip()
    if password is not None and password != "":
        ok, out = helper("nas-cred", input=f"{user}\n{password}\n{domain}\n")
        if not ok:
            raise ValueError(_fail(out))
    elif not Path(CRED).exists() and not user:
        ok, out = helper("nas-cred", input="\n\n\n")             # Gastzugang ohne Passwort
        if not ok:
            raise ValueError(_fail(out))
    ok, out = helper("mount-nas", share, timeout=90)
    if not ok:
        raise ValueError(_fail(out))
    d = host_dir(NAS_MP)
    d.mkdir(parents=True, exist_ok=True)
    return share, str(d)


def forget_nas():
    helper("nas-forget")


# ------------------------------------------------------------------ jede Minute (health)
def _present(uuid):
    return Path("/dev/disk/by-uuid", uuid).exists()


def _mount_source(mp):
    try:
        with open("/proc/mounts", encoding="utf-8") as f:
            for line in f:
                p = line.split()
                if len(p) > 1 and p[1].replace("\\040", " ") == str(mp):
                    return p[0]
    except OSError:
        pass
    return None


def usb_stale(uuid):
    """Hängt an USB_MP noch ein Stick, den es so nicht mehr gibt? Typisch nach einem kurzen USB-Aussetzer: der Stick
    meldet sich mit neuer Gerätedatei (sda → sdb) zurück, die alte Einhängung liefert nur noch Ein-/Ausgabefehler.
    Prüft ohne zu schreiben (läuft jede Minute)."""
    src = _mount_source(USB_MP)
    if not src or not uuid:
        return False
    want = Path("/dev/disk/by-uuid", uuid)
    if not Path(src).exists():
        return True
    return want.exists() and os.path.realpath(src) != os.path.realpath(want)


def remount_usb(uuid, log=None, why=""):
    """Stick aus- und wieder einhängen (Helfer remount-usb). Rückgabe (ok, Meldung)."""
    ok, out = helper("remount-usb", uuid)
    st = read_state()
    st["usb_error"] = None if ok else _fail(out)
    st["usb_try"] = time.time()
    _write_state(st)
    if log:
        log("INFO" if ok else "WARN", "storage", "usb_remounted" if ok else "usb_remount_failed", reason=why, detail=out[-200:])
    return ok, out


def ensure(now=None, settings=None, log=None):
    """Gewähltes Ziel eingehängt halten: USB nach dem Wiederanstecken, NAS nach einem Verbindungsausfall."""
    now = now or time.time()
    t = target(settings)
    st = read_state()
    changed = False
    if t.get("kind") == "usb" and t.get("uuid"):
        present = _present(t["uuid"])
        if st.get("ejected") and not present:                 # abgezogen: beim nächsten Anstecken wieder einhängen
            st.pop("ejected", None); changed = True
        src = _mount_source(USB_MP)
        if not present and src and not Path(src).exists():
            # abgezogen, ohne auszuwerfen: tote Einhängung lösen, damit „steckt nicht“ angezeigt wird und das
            # Wiederanstecken normal eingehängt wird
            ok, out = helper("umount-usb")
            if log:
                log("INFO" if ok else "WARN", "storage", "usb_gone_unmounted" if ok else "usb_gone_unmount_failed", detail=out[-200:])
            return
        if present and not st.get("ejected") and mounted(USB_MP) and usb_stale(t["uuid"]) \
                and now - st.get("usb_try", 0) >= 60:
            remount_usb(t["uuid"], log, "Stick war kurz vom USB getrennt")
            return
        if present and not st.get("ejected") and not mounted(USB_MP) and now - st.get("usb_try", 0) >= 60:
            st["usb_try"] = now; changed = True
            ok, out = helper("mount-usb", t["uuid"])
            st["usb_error"] = None if ok else _fail(out)
            if log:
                log("INFO" if ok else "WARN", "storage", "usb_mounted" if ok else "usb_mount_failed", detail=out[-200:])
    elif t.get("kind") == "nas" and t.get("share"):
        if not mounted(NAS_MP) and now - st.get("nas_try", 0) >= NAS_RETRY_S:
            st["nas_try"] = now; changed = True
            ok, out = helper("mount-nas", t["share"], timeout=90)
            st["nas_error"] = None if ok else _fail(out)
            if log:
                log("INFO" if ok else "WARN", "storage", "nas_mounted" if ok else "nas_mount_failed", detail=out[-200:])
    if changed:
        _write_state(st)


def ensure_now(settings=None):
    """Vor einer Sicherung: Ziel sofort einhängen (ohne Wartezeit)."""
    st = read_state()
    st.pop("usb_try", None); st.pop("nas_try", None)
    _write_state(st)
    ensure(settings=settings)


# ------------------------------------------------------------------ Sicherungen auf dem Ziel
def roots():
    return [p for p in (USB_MP, NAS_MP) if mounted(p)]


def list_backups():
    """Sicherungen auf den eingehängten Zielen – auch die anderer Hesperos-DX-Stationen (z. B. beim Umzug auf einen neuen Pi)."""
    out = []
    for mp in roots():
        for d in [mp, mp / SUB] + sorted((mp / SUB).glob("*/")) if (mp / SUB).is_dir() else [mp]:
            try:
                files = list(d.iterdir())
            except OSError:
                continue
            for f in files:
                if BACKUP_RE.match(f.name) and f.is_file():
                    try:
                        sz, mt = f.stat().st_size, f.stat().st_mtime
                    except OSError:
                        continue
                    out.append({"path": str(f), "name": f.name, "size": sz, "mtime": mt,
                                "where": "USB" if mp == USB_MP else "NAS",
                                "host": d.name if d.parent.name == SUB else "",
                                "scope": BACKUP_RE.match(f.name).group(1)})
    out.sort(key=lambda x: x["name"][17:30], reverse=True)
    return out


def checked_path(path):
    """Nur Sicherungsdateien unterhalb der eingehängten Ziele."""
    p = Path(str(path or "")).resolve()
    if not BACKUP_RE.match(p.name) or not any(str(p).startswith(str(r) + os.sep) for r in roots()) or not p.is_file():
        raise ValueError("Sicherung nicht gefunden")
    return p


def status(settings=None):
    t = target(settings)
    st = read_state()
    usb_on, nas_on = mounted(USB_MP), mounted(NAS_MP)
    return {
        "target": {k: t.get(k) for k in ("kind", "uuid", "label", "share", "user", "domain")},
        "usb": {"mounted": usb_on, "free_gb": _free_gb(USB_MP) if usb_on else None, "ejected": bool(st.get("ejected")),
                "error": st.get("usb_error"), "dir": str(host_dir(USB_MP))},
        "nas": {"mounted": nas_on, "free_gb": _free_gb(NAS_MP) if nas_on else None, "error": st.get("nas_error"),
                "dir": str(host_dir(NAS_MP))},
        "drives": drives(),
        "backups": list_backups(),
        "helper": Path(HELPER).exists(),
        "host": socket.gethostname(),
    }
