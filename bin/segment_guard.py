#!/usr/bin/env python3

import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CFG = BASE / "config" / "scanner.json"
SETTINGS = BASE / "config" / "web_settings.json"


def _load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _band_cfg(name):
    cfg = _load(CFG, {"ranges": []})
    for band in cfg.get("ranges", []):
        if str(band.get("name")) == str(name):
            return band
    return None


def _selected_segments(name):
    settings = _load(SETTINGS, {})
    selected = settings.get("selected_segments", {})
    if not isinstance(selected, dict):
        return ["all"]

    value = selected.get(str(name), ["all"])
    if not isinstance(value, list) or not value:
        return ["all"]

    return [str(x) for x in value]


def _segment_by_id(band, seg_id):
    for seg in band.get("segments", []):
        if str(seg.get("id")) == str(seg_id):
            return seg
    return None


def _frequency_matches_segment(
    band,
    seg,
    rf_hz,
    discrete_tolerance_hz=None
):
    rf = float(rf_hz)

    for a, b in seg.get("ranges", []):
        if float(a) <= rf <= float(b):
            return True

    freqs = seg.get("frequencies_hz", [])

    if freqs:
        if discrete_tolerance_hz is None:
            step = float(band.get("step_hz", 0) or 0)
            tol = max(500.0, step / 2.0)
        else:
            tol = float(discrete_tolerance_hz)

        for f in freqs:
            if abs(rf - float(f)) <= tol:
                return True

    return False


def rf_allowed(band_name, rf_hz):
    band = _band_cfg(band_name)
    if not band:
        return False

    selected = _selected_segments(band_name)

    if "all" in selected:
        return (
            float(band["start_hz"])
            <= float(rf_hz)
            <= float(band["stop_hz"])
        )

    for seg_id in selected:
        seg = _segment_by_id(band, seg_id)
        if seg and _frequency_matches_segment(
            band,
            seg,
            rf_hz
        ):
            return True

    return False


def decoder_allowed(kind, band_name, rf_hz):
    band = _band_cfg(band_name)
    if not band:
        return False

    selected = _selected_segments(band_name)
    kind = str(kind).lower()

    if "all" not in selected and kind not in selected:
        return False

    seg = _segment_by_id(band, kind)
    if not seg:
        return False

    if kind == "ft8":
        return _frequency_matches_segment(
            band,
            seg,
            rf_hz,
            discrete_tolerance_hz=4000.0
        )

    return _frequency_matches_segment(
        band,
        seg,
        rf_hz
    )


def matching_selected_segments(band_name, rf_hz):
    band = _band_cfg(band_name)
    if not band:
        return []

    selected = _selected_segments(band_name)

    if "all" in selected:
        selected = [
            str(x.get("id"))
            for x in band.get("segments", [])
            if x.get("id")
        ]

    result = []

    for seg_id in selected:
        seg = _segment_by_id(band, seg_id)
        if seg and _frequency_matches_segment(
            band,
            seg,
            rf_hz
        ):
            result.append(seg_id)

    return result
