"""Biquad filter frequency response computation for Tonal."""

import math

SAMPLE_RATE = 48000

# All supported filter types
FILTER_TYPES = [
    "peak", "lowshelf", "highshelf",
    "lowpass", "highpass", "bandpass", "notch", "allpass",
]

FILTER_LABELS = {
    "peak": "Peak",
    "lowshelf": "Low Shelf",
    "highshelf": "High Shelf",
    "lowpass": "Low Pass",
    "highpass": "High Pass",
    "bandpass": "Band Pass",
    "notch": "Notch",
    "allpass": "All Pass",
}

FILTER_SHORT = {
    "peak": "PK",
    "lowshelf": "LS",
    "highshelf": "HS",
    "lowpass": "LP",
    "highpass": "HP",
    "bandpass": "BP",
    "notch": "NO",
    "allpass": "AP",
}


def _biquad_coeffs(f0, gain_db, q, filter_type):
    """Compute biquad filter coefficients (b0, b1, b2, a0, a1, a2)."""
    A = 10 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * f0 / SAMPLE_RATE
    cos_w0 = math.cos(w0)
    sin_w0 = math.sin(w0)
    alpha = sin_w0 / (2.0 * q) if q > 0 else 0.0

    if filter_type == "peak":
        b0 = 1.0 + alpha * A
        b1 = -2.0 * cos_w0
        b2 = 1.0 - alpha * A
        a0 = 1.0 + alpha / A
        a1 = -2.0 * cos_w0
        a2 = 1.0 - alpha / A

    elif filter_type == "lowshelf":
        sqrt_A = math.sqrt(max(A, 1e-10))
        b0 = A * ((A + 1) - (A - 1) * cos_w0 + 2 * sqrt_A * alpha)
        b1 = 2 * A * ((A - 1) - (A + 1) * cos_w0)
        b2 = A * ((A + 1) - (A - 1) * cos_w0 - 2 * sqrt_A * alpha)
        a0 = (A + 1) + (A - 1) * cos_w0 + 2 * sqrt_A * alpha
        a1 = -2 * ((A - 1) + (A + 1) * cos_w0)
        a2 = (A + 1) + (A - 1) * cos_w0 - 2 * sqrt_A * alpha

    elif filter_type == "highshelf":
        sqrt_A = math.sqrt(max(A, 1e-10))
        b0 = A * ((A + 1) + (A - 1) * cos_w0 + 2 * sqrt_A * alpha)
        b1 = -2 * A * ((A - 1) + (A + 1) * cos_w0)
        b2 = A * ((A + 1) + (A - 1) * cos_w0 - 2 * sqrt_A * alpha)
        a0 = (A + 1) - (A - 1) * cos_w0 + 2 * sqrt_A * alpha
        a1 = 2 * ((A - 1) - (A + 1) * cos_w0)
        a2 = (A + 1) - (A - 1) * cos_w0 - 2 * sqrt_A * alpha

    elif filter_type == "lowpass":
        b0 = (1.0 - cos_w0) / 2.0
        b1 = 1.0 - cos_w0
        b2 = (1.0 - cos_w0) / 2.0
        a0 = 1.0 + alpha
        a1 = -2.0 * cos_w0
        a2 = 1.0 - alpha

    elif filter_type == "highpass":
        b0 = (1.0 + cos_w0) / 2.0
        b1 = -(1.0 + cos_w0)
        b2 = (1.0 + cos_w0) / 2.0
        a0 = 1.0 + alpha
        a1 = -2.0 * cos_w0
        a2 = 1.0 - alpha

    elif filter_type == "bandpass":
        b0 = alpha
        b1 = 0.0
        b2 = -alpha
        a0 = 1.0 + alpha
        a1 = -2.0 * cos_w0
        a2 = 1.0 - alpha

    elif filter_type == "notch":
        b0 = 1.0
        b1 = -2.0 * cos_w0
        b2 = 1.0
        a0 = 1.0 + alpha
        a1 = -2.0 * cos_w0
        a2 = 1.0 - alpha

    elif filter_type == "allpass":
        b0 = 1.0 - alpha
        b1 = -2.0 * cos_w0
        b2 = 1.0 + alpha
        a0 = 1.0 + alpha
        a1 = -2.0 * cos_w0
        a2 = 1.0 - alpha

    else:
        return 1, 0, 0, 1, 0, 0

    return b0, b1, b2, a0, a1, a2


def biquad_response(freq, f0, gain_db, q, filter_type="peak"):
    """Compute magnitude response (dB) of a biquad filter at a given frequency."""
    if filter_type == "peak" and abs(gain_db) < 0.001:
        return 0.0

    b0, b1, b2, a0, a1, a2 = _biquad_coeffs(f0, gain_db, q, filter_type)

    w = 2.0 * math.pi * freq / SAMPLE_RATE
    cos_w = math.cos(w)
    cos_2w = math.cos(2.0 * w)
    sin_w = math.sin(w)
    sin_2w = math.sin(2.0 * w)

    num_re = b0 + b1 * cos_w + b2 * cos_2w
    num_im = -(b1 * sin_w + b2 * sin_2w)
    den_re = a0 + a1 * cos_w + a2 * cos_2w
    den_im = -(a1 * sin_w + a2 * sin_2w)

    num_sq = num_re ** 2 + num_im ** 2
    den_sq = den_re ** 2 + den_im ** 2

    if den_sq < 1e-30:
        return 0.0
    magnitude_sq = num_sq / den_sq
    if magnitude_sq < 1e-30:
        return -100.0

    return 10.0 * math.log10(magnitude_sq)


def compute_eq_curve(bands, num_points=300):
    """Compute combined frequency response of EQ bands (without pre-amp)."""
    curve = []
    for i in range(num_points):
        t = i / (num_points - 1)
        f = 20.0 * (20000.0 / 20.0) ** t
        total_db = 0.0
        for band in bands:
            total_db += biquad_response(
                f, band["freq"], band["gain"], band["q"], band["type"]
            )
        curve.append((f, total_db))
    return curve


def find_peak(bands, preamp_db=0.0, num_points=300):
    """Find the maximum gain (dB) across the frequency range, including pre-amp."""
    peak = -100.0
    for i in range(num_points):
        t = i / (num_points - 1)
        f = 20.0 * (20000.0 / 20.0) ** t
        total_db = preamp_db
        for band in bands:
            total_db += biquad_response(
                f, band["freq"], band["gain"], band["q"], band["type"]
            )
        if total_db > peak:
            peak = total_db
    return peak
