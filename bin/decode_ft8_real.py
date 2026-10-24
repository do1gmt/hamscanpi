#!/usr/bin/env python3

import json
import sys
import re
import sqlite3
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly



# --- FT8 FREQUENCY GATE AUTOPATCH ---
def _ft8_frequency_gate_from_argv():
    import os as _os
    import re as _re
    import sys as _sys

    _nominal = {
        "160m": 1840000.0,
        "80m": 3573000.0,
        "60m": 5357000.0,
        "40m": 7074000.0,
        "30m": 10136000.0,
        "20m": 14074000.0,
        "17m": 18100000.0,
        "15m": 21074000.0,
        "12m": 24915000.0,
        "10m": 28074000.0,
    }

    _tol = 3500.0

    for _arg in _sys.argv[1:]:
        _name = _os.path.basename(str(_arg))
        _m = _re.search(
            r'_(160m|80m|60m|40m|30m|20m|17m|15m|12m|10m)_([0-9]+)Hz(?:_|\.|$)',
            _name
        )
        if not _m:
            continue

        _band = _m.group(1)
        _rf = float(_m.group(2))
        _f0 = _nominal.get(_band)

        if _f0 is not None and abs(_rf - _f0) > _tol:
            return False

    return True


if __name__ == "__main__" and not _ft8_frequency_gate_from_argv():
    raise SystemExit(0)
# --- END FT8 FREQUENCY GATE AUTOPATCH ---

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "scanner.db"
CFG = BASE / "config" / "scanner.json"
REPORTS = BASE / "reports"

DECODER = Path.home() / "ft8_lib" / "decode_ft8"


# Standard-FT8-Dialfrequenzen USB
DIALS = {
    "160m":  1_840_000.0,
    "80m":   3_573_000.0,
    "60m":   5_357_000.0,
    "40m":   7_074_000.0,
    "30m":  10_136_000.0,
    "20m":  14_074_000.0,
    "17m":  18_100_000.0,
    "15m":  21_074_000.0,
    "12m":  24_915_000.0,
    "10m":  28_074_000.0,
}


cfg = json.loads(
    CFG.read_text(encoding="utf-8")
)

PPM = float(
    cfg.get(
        "frequency_correction_ppm",
        0.0
    )
)

CONJUGATE = bool(
    cfg.get(
        "iq_conjugate",
        False
    )
)


def decode_json_path(recording_path):
    src = Path(recording_path)

    return (
        REPORTS /
        f"{src.stem}_decode.json"
    )


def load_json(path):
    try:
        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        return None


def save_json(path, data):
    path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )


def find_ft8_candidate(data):

    if not data:
        return None

    for d in data.get(
        "decoders",
        []
    ):
        if d.get("type") in (
            "FT8",
            "FT8_CANDIDATE"
        ):
            return d

    return None


def remove_ft8(data):

    if not data:
        return

    decoders = data.get(
        "decoders",
        []
    )

    data["decoders"] = [
        d
        for d in decoders
        if d.get("type")
        not in (
            "FT8",
            "FT8_CANDIDATE"
        )
    ]


def make_ft8_audio(
    recording_path,
    center_hz,
    dial_hz,
    outfile
):

    data, fs = sf.read(
        recording_path,
        always_2d=True
    )

    if data.shape[1] < 2:
        raise RuntimeError(
            "RAW-Aufnahme ist kein Stereo-I/Q"
        )


    i = data[:, 0].astype(
        np.float64
    )

    q = data[:, 1].astype(
        np.float64
    )

    z = i + 1j*q


    if CONJUGATE:
        z = np.conj(z)


    z -= np.mean(z)


    corrected_center = (
        float(center_hz) *
        (
            1.0 +
            PPM * 1e-6
        )
    )


    # FT8-Dialfrequenz auf 0 Hz legen.
    #
    # Ein Signal oberhalb der Dialfrequenz
    # erscheint dadurch bei seiner normalen
    # USB-Audiofrequenz.
    shift = (
        float(dial_hz) -
        corrected_center
    )


    n = np.arange(
        len(z),
        dtype=np.float64
    )


    bb = (
        z *
        np.exp(
            -2j *
            np.pi *
            shift *
            n /
            fs
        )
    )


    audio = np.real(
        bb
    )

    audio -= np.mean(
        audio
    )


    # Auf 12 kHz bringen
    if fs != 12000:

        if fs == 48000:

            audio = resample_poly(
                audio,
                1,
                4
            )

        else:

            frac = Fraction(
                12000,
                int(fs)
            ).limit_denominator(
                1000
            )

            audio = resample_poly(
                audio,
                frac.numerator,
                frac.denominator
            )


    # ft8_lib verträgt maximal ca. 15 s
    audio = audio[
        :int(
            14.8 *
            12000
        )
    ]


    if len(audio) < int(
        10 *
        12000
    ):
        raise RuntimeError(
            "Aufnahme zu kurz"
        )


    peak = float(
        np.max(
            np.abs(audio)
        )
    )


    if peak > 0:
        audio = (
            audio /
            peak *
            0.8
        )


    sf.write(
        outfile,
        audio,
        12000,
        subtype="PCM_16"
    )


def run_decoder(path, protocol="ft8"):
    # ft8_lib: decode_ft8 [-ft4] <wav>
    args = [str(DECODER)]
    if protocol == "ft4":
        args.append("-ft4")
    args.append(str(path))
    r = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=30
    )

    return (
        r.stdout +
        "\n" +
        r.stderr
    )


def parse_messages(output):

    messages = []

    rx = re.compile(
        r"^\d{6}\s+"
        r"([+-]?\d+(?:\.\d+)?)\s+"
        r"([+-]?\d+(?:\.\d+)?)\s+"
        r"(\d+)\s+~\s+"
        r"(.+?)\s*$"
    )


    for line in output.splitlines():

        m = rx.match(
            line.strip()
        )

        if not m:
            continue


        text = m.group(
            4
        ).strip()


        # Leere oder offensichtlich kaputte
        # Decoderzeilen nicht übernehmen.
        if len(text) < 3:
            continue


        messages.append({
            "snr": float(
                m.group(1)
            ),
            "dt": float(
                m.group(2)
            ),
            "audio_hz": int(
                m.group(3)
            ),
            "text": text
        })


    return messages


PROTOCOL_LABEL = "FT8"


def mark_real_ft8(
    data,
    dial_hz,
    messages
):

    remove_ft8(
        data
    )


    texts = [
        m["text"]
        for m in messages
    ]


    data.setdefault(
        "decoders",
        []
    ).insert(
        0,
        {
            "type": PROTOCOL_LABEL,
            "confidence": 100,
            "text": "\n".join(
                texts
            ),
            "detail": (
                f"{len(messages)} echte "
                f"FT8-Nachrichten · "
                f"Dial "
                f"{dial_hz/1e6:.6f} MHz USB"
            ),
            "validated": True,
            "decoder": "ft8_lib",
            "messages": messages
        }
    )


def main(dry=False):
    """dry=True: nur zählen, wie viele Aufnahmen auf FT8 zu prüfen wären (für die Statusanzeige)."""

    if dry and not DECODER.exists():
        return 0
    if not DECODER.exists():
        raise SystemExit(
            f"FT8-Decoder fehlt: "
            f"{DECODER}"
        )


    db = sqlite3.connect(
        DB
    )


    rows = db.execute(
        """
        SELECT
            p.id,
            p.rf_hz,
            p.recording_path,
            COALESCE(p.rec_center_hz, s.center_hz),
            s.band,
            COALESCE(p.mode, '')
        FROM peak p
        JOIN scan s
          ON s.id = p.scan_id
        WHERE p.recording_path
              IS NOT NULL
          AND COALESCE(p.mode, '') IN
              ('', 'unknown', 'ft8', 'ft4', 'digital')
        ORDER BY p.id
        """
    ).fetchall()
    db.close()

    # Waehlfrequenzen aus dem Bandplan (FT8 und FT4)
    plan_dials = {}
    for b in cfg.get("ranges", []):
        for seg in b.get("segments", []):
            if seg.get("mode") in ("ft8", "ft4") and seg.get("frequencies_hz"):
                plan_dials[(str(b["name"]), seg["mode"])] = float(seg["frequencies_hz"][0])


    tested = 0
    confirmed = 0
    rejected = 0
    messages_total = 0


    for (
        peak_id,
        rf_hz,
        recording_path,
        center_hz,
        band,
        mode
    ) in rows:
        protocol = "ft4" if mode == "ft4" else "ft8"
        src = Path(
            recording_path
        )
        if not src.exists():
            continue
        json_path = decode_json_path(
            recording_path
        )
        data = load_json(
            json_path
        )
        if data is None:
            data = {
                "peak_id": peak_id,
                "band": band,
                "frequency_hz": rf_hz,
                "decoders": []
            }
        candidate = find_ft8_candidate(
            data
        )
        # Treffer aus FT8/FT4-Segmenten werden immer dekodiert,
        # alle anderen nur bei FT8-Verdacht aus der Signaturpruefung.
        if candidate is None and mode not in ("ft8", "ft4"):
            continue
        if candidate is None:
            candidate = {}
        # Ein Dekodierversuch pro Aufnahme reicht.
        if data.get("ft8_tested") is True:
            continue
        if dry:
            if not (candidate.get("type") == "FT8" and candidate.get("validated") is True) \
                    and plan_dials.get((band, protocol), DIALS.get(band)) is not None:
                tested += 1
            continue
        data["ft8_tested"] = True
        save_json(json_path, data)


        # Bereits mit ft8_lib bestätigte Treffer
        # nicht ständig neu dekodieren.
        if (
            candidate.get(
                "type"
            ) == "FT8"
            and candidate.get(
                "validated"
            ) is True
        ):
            continue


        tested += 1

        dial = plan_dials.get(
            (band, protocol),
            DIALS.get(band)
        )
        if dial is None:
            continue


        tmp_path = None


        try:

            with tempfile.NamedTemporaryFile(
                suffix=".wav",
                delete=False
            ) as tmp:

                tmp_path = Path(
                    tmp.name
                )


            make_ft8_audio(
                src,
                float(
                    center_hz
                ),
                dial,
                tmp_path
            )


            output = run_decoder(
                tmp_path,
                protocol
            )


            messages = parse_messages(
                output
            )
            if messages:                                  # echte SNR (2500 Hz) statt Sync-Bewertung des Dekoders
                import ft8_snr
                ft8_snr.fix_messages(tmp_path, messages, protocol.upper())


            if messages:
                globals()["PROTOCOL_LABEL"] = protocol.upper()
                mark_real_ft8(
                    data,
                    dial,
                    messages
                )

                save_json(
                    json_path,
                    data
                )

                confirmed += 1

                messages_total += len(
                    messages
                )


                print(
                    f"BESTÄTIGT  "
                    f"{band:4s}  "
                    f"{rf_hz/1e6:.6f} MHz  "
                    f"{len(messages)} Nachrichten"
                )


                for m in messages:

                    print(
                        f"   "
                        f"{m['audio_hz']:4d} Hz  "
                        f"{m['text']}"
                    )


            else:

                # WICHTIG:
                #
                # Kein echter Decode =
                # kein FT8 im Dashboard.
                remove_ft8(
                    data
                )

                save_json(
                    json_path,
                    data
                )

                rejected += 1


                print(
                    f"VERWORFEN  "
                    f"{band:4s}  "
                    f"{rf_hz/1e6:.6f} MHz  "
                    f"0 Nachrichten"
                )


        except Exception as e:

            # Auch bei Decoderfehler niemals
            # den FT8-Verdacht als bestätigtes
            # FT8 stehen lassen.
            if data:

                remove_ft8(
                    data
                )

                save_json(
                    json_path,
                    data
                )


            rejected += 1


            print(
                f"FEHLER      "
                f"{band:4s}  "
                f"{rf_hz/1e6:.6f} MHz  "
                f"{e}"
            )


        finally:

            if tmp_path:

                try:
                    tmp_path.unlink()
                except Exception:
                    pass


    if dry:
        return tested

    print()
    print(
        f"FT8-Kandidaten geprüft : {tested}"
    )

    print(
        f"FT8 bestätigt          : {confirmed}"
    )

    print(
        f"FT8 verworfen          : {rejected}"
    )

    print(
        f"Nachrichten dekodiert  : {messages_total}"
    )


if __name__ == "__main__":
    if "--pending" in sys.argv:
        print(main(dry=True) or 0)
    else:
        main()
