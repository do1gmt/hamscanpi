#!/usr/bin/env python3

import argparse
import numpy as np
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CFG = BASE / "config" / "scanner.json"
SETTINGS = BASE / "config" / "web_settings.json"


def read_json(path, default):
    try:
        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        return default


# Frequenzraster: Faktor auf die Schrittweite des Bandplans. Kleiner = mehr Überlappung der
# Empfangsfenster (keine Lücken an den Fensterrändern und in der Mitte), dafür längerer Scan.
RASTER = {"normal": 1.0, "fein": 0.75, "sehr_fein": 0.5}


def raster_factor(settings, device):
    r = (settings.get("scan_raster") or {}) if isinstance(settings, dict) else {}
    return RASTER.get(str(r.get(device, "normal")), 1.0)


def band_step(band, settings):
    return max(1000, int(int(band["step_hz"]) * raster_factor(settings, str(band.get("device", "pmsdr")))))


def interval_centers(
    start,
    stop,
    step
):
    start = int(start)
    stop = int(stop)
    step = max(
        1,
        int(step)
    )

    if stop < start:
        return []

    result = []
    f = start

    while f <= stop:
        result.append(f)
        f += step

    if not result:
        result.append(start)

    return result


# ---------------------------------------------------------------- RTL-SDR: Fenster so legen,
# dass kein Kanal im Gleichanteil (Fenstermitte) oder jenseits des nutzbaren Fensters liegt.
#
# Bisher begann jedes Fenster am Bereichsanfang und lief in 900-kHz-Schritten weiter. Folgen:
#   * die Fenstermitte (Gleichanteil des RTL, ±8 kHz unbrauchbar) lag genau auf einem Kanal –
#     z. B. 438,650 MHz, 145,500 MHz (Anruf) oder 433,920 MHz,
#   * Bereiche, die schmaler als ein Schritt sind, wurden nur bis Anfang + 435 kHz abgehört –
#     bei den 70-cm-Relais 438,650–439,425 fehlte alles ab 439,085 MHz.
RTL_USABLE_HZ = 435000.0        # nutzbare Halbbreite (1,024 MS/s, Ränder des Filters ausgenommen)
RTL_DC_HZ = 6000.0              # so weit muss ein Kanal mindestens von der Mitte weg sein
RASTER_HZ = 12500.0             # Kanalraster 2 m/70 cm; Mitten liegen genau zwischen zwei Kanälen
DIGITAL_MODES = {"ft8", "ft4", "wspr", "sstv"}   # eigene Wachen mit eigener Abstimmung


def _half_raster(f, origin=0.0):
    """Auf die Mitte zwischen zwei Kanälen legen (Raster ab origin, z. B. PMR446 ab 446,00625):
    der nächste Kanal ist dann 6,25 kHz entfernt."""
    return origin + (np.floor((f - origin) / RASTER_HZ) + 0.5) * RASTER_HZ


def rtl_centers(ranges, points, step):
    """Fenstermitten für den RTL-SDR: jeder Bereich vollständig im nutzbaren Teil eines Fensters,
    keine Fenstermitte auf einem Kanal, jede Einzelfrequenz mindestens RTL_DC_HZ neben der Mitte."""
    reach = min(RTL_USABLE_HZ, max(float(step) / 2.0, 50000.0)) - 3000.0
    centers = []
    for a, b in sorted((float(min(a, b)), float(max(a, b))) for a, b in ranges):
        span = b - a
        n = max(1, int(np.ceil((span + 2 * RASTER_HZ) / (2 * reach))))
        for k in range(n):
            centers.append(_half_raster(a + (k + 0.5) * span / n, a))
    for f in sorted(points):
        if any(RTL_DC_HZ <= abs(f - c) <= reach for c in centers):
            continue
        centers.append(_half_raster(f + 100000.0, f))      # eigenes Fenster, Kanal ~100 kHz neben der Mitte
    return sorted({int(round(c)) for c in centers})


def make_plan(
    cfg=None,
    settings=None
):
    if cfg is None:
        cfg = read_json(
            CFG,
            {"ranges": []}
        )

    if settings is None:
        settings = read_json(
            SETTINGS,
            {}
        )

    all_names = [
        str(b["name"])
        for b in cfg.get(
            "ranges",
            []
        )
    ]

    selected_bands = settings.get(
        "selected_bands"
    )

    if not isinstance(
        selected_bands,
        list
    ):
        selected_bands = [
            "80m",
            "15m"
        ]

    selected_bands = [
        str(x)
        for x in selected_bands
        if str(x) in all_names
    ]

    if not selected_bands:
        selected_bands = [
            x
            for x in ("80m", "15m")
            if x in all_names
        ]

    selected_segments = settings.get(
        "selected_segments",
        {}
    )

    if not isinstance(
        selected_segments,
        dict
    ):
        selected_segments = {}

    active_device = str(
        settings.get("active_device", "pmsdr")
    )
    # Steckt das gewählte Gerät nicht, aber das andere: automatisch das andere nehmen
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from devices import resolve_device
        active_device = resolve_device(active_device)
    except Exception:
        pass

    plan = []

    for band in cfg.get(
        "ranges",
        []
    ):
        name = str(
            band["name"]
        )

        if name not in selected_bands:
            continue

        # Bänder, deren Mithören in vielen Ländern verboten ist (legal = restricted), nur wenn
        # in config/scanner.json ausdrücklich freigeschaltet (allow_restricted_bands)
        if band.get("legal") == "restricted" and not cfg.get("allow_restricted_bands"):
            continue

        # Nur Baender des gerade angeschlossenen Geraets (eine Antenne!)
        if str(band.get("device", "pmsdr")) != active_device:
            continue

        requested = selected_segments.get(
            name,
            ["all"]
        )

        if not isinstance(
            requested,
            list
        ) or not requested:
            requested = [
                "all"
            ]

        step = band_step(band, settings)

        centers = set()

        labels = []

        if "all" in requested:

            centers.update(
                interval_centers(
                    band["start_hz"],
                    band["stop_hz"],
                    step
                )
            )

            labels = [
                "Gesamt"
            ]

        else:

            specs = {
                str(x["id"]): x
                for x in band.get(
                    "segments",
                    []
                )
            }

            for segment_id in requested:

                spec = specs.get(
                    str(segment_id)
                )

                if not spec:
                    continue

                labels.append(
                    str(
                        spec.get(
                            "label",
                            segment_id
                        )
                    )
                )

                for a, b in spec.get(
                    "ranges",
                    []
                ):
                    centers.update(
                        interval_centers(
                            a,
                            b,
                            step
                        )
                    )

                centers.update(
                    int(x)
                    for x in spec.get(
                        "frequencies_hz",
                        []
                    )
                )

        if str(band.get("device", "pmsdr")) == "rtlsdr":
            all_specs = {str(x["id"]): x for x in band.get("segments", [])}
            if "all" in requested:
                rng, pts, keep = [(band["start_hz"], band["stop_hz"])], [], []
                use = list(all_specs)
            else:
                rng, pts, keep = [], [], []
                use = [str(x) for x in requested]
            for sid in use:
                spec = all_specs.get(sid)
                if not spec:
                    continue
                if "all" not in requested:
                    rng += [tuple(x) for x in spec.get("ranges", [])]
                fl = [int(x) for x in spec.get("frequencies_hz", [])]
                (keep if spec.get("mode") in DIGITAL_MODES else pts).extend(fl)
            centers = set(rtl_centers(rng, pts, step)) | set(keep)

        if not centers:
            continue

        plan.append({
            "name": name,
            "device": str(band.get("device", "pmsdr")),
            "step_hz": step,
            "segments": requested,
            "labels": labels,
            "centers_hz":
                sorted(centers)
        })

    return plan


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tsv",
        action="store_true"
    )
    parser.add_argument(
        "--json",
        action="store_true"
    )

    args = parser.parse_args()

    plan = make_plan()

    if args.tsv:
        for p in plan:
            print(
                f"{p['name']}\t"
                f"{len(p['centers_hz'])}\t"
                f"{','.join(p['segments'])}"
            )
        return

    print(
        json.dumps(
            plan,
            indent=2,
            ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
