"""Import and export EQ profiles in EqualizerAPO (.txt) and EasyEffects (.json) formats."""

import json
import re
import os
import logging

log = logging.getLogger("tonal.import_export")

# ── EqualizerAPO type mappings ──────────────────────────────────────────────
# EqualizerAPO uses these abbreviations; some variants exist (LSC/LSQ, HSC/HSQ)

_APO_TO_TONAL = {
    "PK": "peak", "PEQ": "peak",
    "LS": "lowshelf", "LSC": "lowshelf", "LSQ": "lowshelf",
    "HS": "highshelf", "HSC": "highshelf", "HSQ": "highshelf",
    "LP": "lowpass", "LPQ": "lowpass",
    "HP": "highpass", "HPQ": "highpass",
    "BP": "bandpass",
    "NO": "notch",
    "AP": "allpass",
}

_TONAL_TO_APO = {
    "peak": "PK",
    "lowshelf": "LS",
    "highshelf": "HS",
    "lowpass": "LP",
    "highpass": "HP",
    "bandpass": "BP",
    "notch": "NO",
    "allpass": "AP",
}

# ── EasyEffects type mappings ───────────────────────────────────────────────

_EE_TO_TONAL = {
    "Bell": "peak",
    "Lo-shelf": "lowshelf", "Low Shelf": "lowshelf", "Lo Shelf": "lowshelf",
    "Hi-shelf": "highshelf", "High Shelf": "highshelf", "Hi Shelf": "highshelf",
    "Lo-pass": "lowpass", "Low Pass": "lowpass", "Lo Pass": "lowpass",
    "Hi-pass": "highpass", "High Pass": "highpass", "Hi Pass": "highpass",
    "Band-pass": "bandpass", "Band Pass": "bandpass",
    "Notch": "notch",
    "All-pass": "allpass", "All Pass": "allpass",
}

_TONAL_TO_EE = {
    "peak": "Bell",
    "lowshelf": "Lo-shelf",
    "highshelf": "Hi-shelf",
    "lowpass": "Lo-pass",
    "highpass": "Hi-pass",
    "bandpass": "Band-pass",
    "notch": "Notch",
    "allpass": "All-pass",
}


# ── Peace filter type codes ──────────────────────────────────────────────

_PEACE_TYPE_CODES = {
    0: "peak",
    1: "lowshelf",
    2: "highshelf",
    3: "lowpass",
    4: "highpass",
    5: "bandpass",
    6: "notch",
    7: "allpass",
}


# ═══════════════════════════════════════════════════════════════════════════
#  IMPORT
# ═══════════════════════════════════════════════════════════════════════════

def detect_format(path):
    """Detect file format from extension and content."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        return "easyeffects"
    elif ext == ".peace":
        return "peace"
    elif ext == ".apeq":
        return "apo"
    # For .txt, check content to distinguish APO from Peace
    try:
        with open(path, "r") as f:
            first = f.read(512)
        if first.strip().startswith("{"):
            return "easyeffects"
        if "[General]" in first or "[Frequencies]" in first:
            return "peace"
        if "Filter" in first or "Preamp" in first:
            return "apo"
    except IOError:
        pass
    return None


def import_profile(path):
    """Import an EQ profile from file. Returns (preamp_db, bands, error_message).

    On success: (preamp_db, bands_list, None)
    On failure: (None, None, error_string)
    """
    fmt = detect_format(path)
    if fmt == "apo":
        return _import_apo(path)
    elif fmt == "peace":
        return _import_peace(path)
    elif fmt == "easyeffects":
        return _import_easyeffects(path)
    else:
        return None, None, f"Unrecognized file format: {os.path.basename(path)}"


def _import_apo(path):
    """Parse EqualizerAPO parametric EQ format.

    Supported line formats:
        Preamp: -6.2 dB
        Filter 1: ON PK Fc 1000 Hz Gain 3.0 dB Q 1.41
        Filter 2: OFF LS Fc 105 Hz Gain -3.2 dB Q 0.71
    """
    preamp_db = 0.0
    bands = []

    # Regex for filter lines — flexible with spacing and optional "dB"/"Hz"
    filter_re = re.compile(
        r"Filter\s+\d+\s*:\s*(ON|OFF)\s+(\w+)\s+Fc\s+([\d.]+)\s*(?:Hz)?\s+"
        r"Gain\s+([+-]?[\d.]+)\s*(?:dB)?\s+Q\s+([\d.]+)",
        re.IGNORECASE,
    )
    preamp_re = re.compile(r"Preamp\s*:\s*([+-]?[\d.]+)\s*(?:dB)?", re.IGNORECASE)

    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                m = preamp_re.match(line)
                if m:
                    preamp_db = float(m.group(1))
                    continue

                m = filter_re.match(line)
                if m:
                    enabled = m.group(1).upper() == "ON"
                    apo_type = m.group(2).upper()
                    freq = float(m.group(3))
                    gain = float(m.group(4))
                    q = float(m.group(5))

                    tonal_type = _APO_TO_TONAL.get(apo_type, "peak")

                    # Skip disabled filters — import only active ones
                    if not enabled:
                        continue

                    bands.append({
                        "freq": round(freq, 1),
                        "gain": round(gain, 2),
                        "q": round(q, 3),
                        "type": tonal_type,
                    })

    except IOError as e:
        return None, None, f"Could not read file: {e}"
    except ValueError as e:
        return None, None, f"Parse error: {e}"

    if not bands and preamp_db == 0.0:
        return None, None, "No EQ filters found in file"

    log.info("Imported APO profile: preamp %.1f dB, %d bands", preamp_db, len(bands))
    return preamp_db, bands, None


def _import_peace(path):
    """Parse Peace Equalizer .peace format (INI-style with sections).

    Reads [General] for PreAmp, [Frequencies] for center frequencies,
    [Gains] for gain values (missing = 0 dB), [Qualities] for Q factors,
    and [Filters] for filter type codes (missing = 0 = peak).
    Only imports the "All" speaker config (not per-channel overrides).
    """
    try:
        with open(path, "r") as f:
            content = f.read()
    except IOError as e:
        return None, None, f"Could not read file: {e}"

    # Parse INI sections into dicts
    sections = {}
    current_section = None
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1]
            sections[current_section] = {}
        elif "=" in line and current_section:
            key, val = line.split("=", 1)
            sections[current_section][key.strip()] = val.strip()

    # PreAmp
    general = sections.get("General", {})
    preamp_db = float(general.get("PreAmp", "0"))

    # Frequencies — numbered from 1
    freq_section = sections.get("Frequencies", {})
    gain_section = sections.get("Gains", {})
    quality_section = sections.get("Qualities", {})
    filter_section = sections.get("Filters", {})

    if not freq_section:
        return None, None, "No [Frequencies] section found in Peace file"

    # Determine how many bands exist
    band_numbers = []
    for key in freq_section:
        m = re.match(r"Frequency(\d+)", key)
        if m:
            band_numbers.append(int(m.group(1)))
    band_numbers.sort()

    bands = []
    for n in band_numbers:
        freq = float(freq_section.get(f"Frequency{n}", "1000"))
        gain = float(gain_section.get(f"Gain{n}", "0"))
        q = float(quality_section.get(f"Quality{n}", "1.41"))
        filter_code = int(filter_section.get(f"Filter{n}", "0"))
        tonal_type = _PEACE_TYPE_CODES.get(filter_code, "peak")

        bands.append({
            "freq": round(freq, 1),
            "gain": round(gain, 2),
            "q": round(q, 3),
            "type": tonal_type,
        })

    if not bands and preamp_db == 0.0:
        return None, None, "No EQ bands found in Peace file"

    log.info("Imported Peace profile: preamp %.1f dB, %d bands", preamp_db, len(bands))
    return preamp_db, bands, None


def _import_easyeffects(path):
    """Parse EasyEffects JSON profile format (v6/v7)."""
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except (IOError, json.JSONDecodeError) as e:
        return None, None, f"Could not read JSON: {e}"

    # EasyEffects nests the EQ under output/equalizer#0 or similar
    eq_section = None

    # Try common paths in EasyEffects profiles
    for root_key in ("output", "input", ""):
        container = data.get(root_key, data) if root_key else data
        if isinstance(container, dict):
            for key in container:
                if "equalizer" in key.lower():
                    eq_section = container[key]
                    break
        if eq_section:
            break

    # Fallback: if the JSON itself has band0/band1 keys, treat it as the EQ section
    if not eq_section and any(k.startswith("band") for k in data):
        eq_section = data

    if not eq_section:
        return None, None, "No equalizer section found in EasyEffects profile"

    preamp_db = eq_section.get("input-gain", eq_section.get("preamp", 0.0))
    bands = []

    # Collect bands — EasyEffects uses band0, band1, etc.
    band_idx = 0
    while True:
        band_key = f"band{band_idx}"
        band_data = eq_section.get(band_key)
        if band_data is None:
            break

        freq = band_data.get("frequency", 1000.0)
        gain = band_data.get("gain", 0.0)
        q = band_data.get("q", 1.0)
        ee_type = band_data.get("type", "Bell")
        tonal_type = _EE_TO_TONAL.get(ee_type, "peak")

        bands.append({
            "freq": round(float(freq), 1),
            "gain": round(float(gain), 2),
            "q": round(float(q), 3),
            "type": tonal_type,
        })
        band_idx += 1

    if not bands and preamp_db == 0.0:
        return None, None, "No EQ bands found in EasyEffects profile"

    log.info("Imported EasyEffects profile: preamp %.1f dB, %d bands", preamp_db, len(bands))
    return float(preamp_db), bands, None


# ═══════════════════════════════════════════════════════════════════════════
#  EXPORT
# ═══════════════════════════════════════════════════════════════════════════

def export_apo(path, preamp_db, bands, profile_name="Tonal"):
    """Export to EqualizerAPO parametric EQ format (.txt).

    Returns (success, error_message).
    """
    try:
        lines = [
            f"# Exported from Tonal — Profile: {profile_name}",
            f"Preamp: {preamp_db:+.1f} dB",
        ]
        for i, band in enumerate(bands):
            apo_type = _TONAL_TO_APO.get(band["type"], "PK")
            lines.append(
                f"Filter {i + 1}: ON {apo_type} Fc {band['freq']:.1f} Hz "
                f"Gain {band['gain']:+.1f} dB Q {band['q']:.3f}"
            )

        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")

        log.info("Exported APO profile: %s (%d bands)", path, len(bands))
        return True, None
    except IOError as e:
        return False, f"Could not write file: {e}"


def export_easyeffects(path, preamp_db, bands, profile_name="Tonal"):
    """Export to EasyEffects JSON format (.json).

    Returns (success, error_message).
    """
    eq_data = {
        "input-gain": preamp_db,
        "output-gain": 0.0,
        "num-bands": len(bands),
    }

    for i, band in enumerate(bands):
        ee_type = _TONAL_TO_EE.get(band["type"], "Bell")
        eq_data[f"band{i}"] = {
            "frequency": band["freq"],
            "gain": band["gain"],
            "mode": "RLC (BT)",
            "q": band["q"],
            "slope": "x1",
            "type": ee_type,
        }

    profile = {
        "output": {
            "equalizer#0": eq_data,
        },
    }

    try:
        with open(path, "w") as f:
            json.dump(profile, f, indent=2)

        log.info("Exported EasyEffects profile: %s (%d bands)", path, len(bands))
        return True, None
    except IOError as e:
        return False, f"Could not write file: {e}"
