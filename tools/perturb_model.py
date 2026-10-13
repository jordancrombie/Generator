"""Reference implementation of the generator's perturbation model ("perturb/1")
and frame marker, for the grader and for verifying the firmware.

Mirrors src/perturb.cpp and src/marker.cpp. Every perturbation is a pure
function of the absolute line number n = frame * 525 + line_index.
"""
import math

SAMPLE_HZ = 4 * 315e6 / 88
SAMPLE_NS = 1e9 / SAMPLE_HZ
SAMPLES_PER_LINE = 910
LINES_PER_FRAME = 525
FH_HZ = SAMPLE_HZ / SAMPLES_PER_LINE
M32 = 0xFFFFFFFF


def hash32(x):
    """lowbias32 (Chris Wellons)."""
    x &= M32
    x ^= x >> 16; x = (x * 0x7FEB352D) & M32
    x ^= x >> 15; x = (x * 0x846CA68B) & M32
    x ^= x >> 16
    return x


def gauss(seed, n):
    """Approx. N(0,1): Irwin-Hall sum of four 16-bit uniforms."""
    s = sum(hash32((seed + 0x9E3779B9 * (4 * n + i)) & M32) & 0xFFFF for i in range(4))
    return (s - 4 * 32767.5) / (65536.0 * 0.57735027)


def shift_ns(test, n):
    """Target sync shift of absolute line n, ns. `test` is the manifest's test dict."""
    tid, p = test["id"], test["params"]
    if tid == "H1":
        return p["rms_ns"] * gauss(test["seed"], n)
    if tid == "H2":
        return p["peak_ns"] * math.sin(2 * math.pi * math.fmod(n * p["hz"] / FH_HZ, 1.0))
    if tid == "H3":
        return p["step_us"] * 1000.0 if (n // 7867) & 1 else 0.0
    if tid == "H4":
        L = n % LINES_PER_FRAME + 1
        return 0.0 if 257 <= L < 520 else p["skew_us"] * 1000.0
    if tid == "H5":
        L = n % LINES_PER_FRAME + 1
        return p["samples"] * SAMPLE_NS if L in (100, 362) else 0.0
    return 0.0


HALF_SAMPLES = 456  # a lone half-line, rounded to the DAC's sample pairs
SLOT_HALF, SLOT_ALT, SLOT_NOISE = 1, 2, 4
NOISE_VARIANTS = 4
FRAME_HZ = FH_HZ / LINES_PER_FRAME

# Render modifiers for SLOT_ALT lines (src/line_render.h)
MOD_NO_HSYNC, MOD_EQ_AS_HSYNC, MOD_VS_UNSERRATED, MOD_VS_AS_EQ = 1, 2, 4, 8


def line_halves(L):
    """(first half, second half) of NTSC line L: 'EQ', 'VS', 'BLANK' or 'ACTIVE'."""
    if L <= 3: return "EQ", "EQ"
    if L <= 6: return "VS", "VS"
    if L <= 9: return "EQ", "EQ"
    if L <= 20: return "BLANK", "BLANK"
    if L <= 262: return "ACTIVE", "ACTIVE"
    if L == 263: return "ACTIVE", "EQ"
    if L <= 265: return "EQ", "EQ"
    if L == 266: return "EQ", "VS"
    if L <= 268: return "VS", "VS"
    if L == 269: return "VS", "EQ"
    if L <= 271: return "EQ", "EQ"
    if L == 272: return "EQ", "BLANK"
    if L <= 282: return "BLANK", "BLANK"
    if L == 283: return "BLANK", "ACTIVE"
    return "ACTIVE", "ACTIVE"


def alt_mods(test, L):
    """Render modifiers of line L's SLOT_ALT copy (0 = never altered)."""
    tid, p = test["id"], test["params"]
    if tid == "S3":
        return MOD_NO_HSYNC if 100 <= L < 100 + int(p["lines"]) else 0
    if tid != "S4":
        return 0
    h = line_halves(L)
    eq, vs = "EQ" in h, "VS" in h
    v = int(p["variant"])
    if v == 1: return MOD_EQ_AS_HSYNC if eq else 0
    if v == 2: return MOD_VS_UNSERRATED if vs else 0
    if v == 3: return (MOD_EQ_AS_HSYNC if eq else 0) | (MOD_VS_UNSERRATED if vs else 0)
    if v in (4, 5): return MOD_VS_AS_EQ if vs and L <= 9 else 0
    if v == 6: return MOD_VS_AS_EQ if vs else 0
    return 0


def lround(x):
    return int(math.floor(x + 0.5)) if x >= 0 else -int(math.floor(-x + 0.5))


def wow_lines(test, frame):
    """S8: lines added (>0) or removed (<0) per field in this frame."""
    p = test["params"]
    return lround(p["peak_lines"] * math.sin(2 * math.pi * p["hz"] * frame / FRAME_HZ))


def noise_variant(frame, slot):
    return (frame * 7 + slot) % NOISE_VARIANTS


def noise_ire(variant, k):
    """IRE of sample k of snow rendering `variant` (src/line_render.cpp render_noise)."""
    x = ((variant + 1) * 0x9E3779B9 + k * 0x85EBCA6B) & M32
    x ^= x >> 16; x = (x * 0x7FEB352D) & M32
    x ^= x >> 15; x = (x * 0x846CA68B) & M32
    x ^= x >> 16
    return -15.0 + 115.0 * (x & 0xFFFF) / 65535.0


def frame_slots(test, frame):
    """Lines played for `frame`: list of (ntsc_line 1..525, flags)."""
    tid, p = test["id"], test["params"]
    if tid == "S5":  # 240p: two 262-line fields with field-1 timing
        return [(L, 0) for _ in range(2) for L in range(1, 263)]
    if tid == "S7":
        every = int(p["every_frames"])
        if frame % every == every - 1:  # field 1 only, ending half-way along line 263
            return [(L, 0) for L in range(1, 263)] + [(263, SLOT_HALF)]
    if tid in ("S6", "S8"):
        d = int(p["delta_lines"]) if tid == "S6" else wow_lines(test, frame)
        out = []
        for L in range(1, LINES_PER_FRAME + 1):
            if d < 0 and (200 <= L < 200 - d or 462 <= L < 462 - d):
                continue
            out.append((L, 0))
            if d > 0 and L in (200, 462):
                out += [(L, 0)] * d
        return out
    if tid == "S9":  # a band of snow moving down 23 lines a frame through lines 30..229 (+263)
        top, h = 30 + (frame * 23) % 200, int(p["lines"])
        return [(L, SLOT_NOISE if top <= L < top + h or top + 263 <= L < top + 263 + h else 0)
                for L in range(1, LINES_PER_FRAME + 1)]
    if tid == "S4":
        v = int(p["variant"])
        active = frame % 30 == 0 if v == 4 else frame % 6 == 0 if v == 5 else True
        return [(L, SLOT_ALT if active and alt_mods(test, L) else 0) for L in range(1, LINES_PER_FRAME + 1)]
    if tid == "S3":
        return [(L, SLOT_ALT if alt_mods(test, L) else 0) for L in range(1, LINES_PER_FRAME + 1)]
    return [(L, 0) for L in range(1, LINES_PER_FRAME + 1)]


def nominal_samples(flags):
    return HALF_SAMPLES if flags & SLOT_HALF else SAMPLES_PER_LINE


def frame_start_mod4(test, frame):
    """Nominal samples played before `frame`, mod 4 (sets the subcarrier phase)."""
    return sum(nominal_samples(f) for fr in range(frame) for _, f in frame_slots(test, fr)) % 4


def quantize(shift, nsub):
    """(e, v): whole-sample even offset and sub-sample step index, as played."""
    q = int(math.floor(shift / SAMPLE_NS / (2.0 / nsub) + 0.5)) if shift >= 0 else \
        -int(math.floor(-shift / SAMPLE_NS / (2.0 / nsub) + 0.5))
    e2 = q // nsub
    return 2 * e2, q - e2 * nsub


def played_ns(e, v, nsub):
    return (e + v * 2.0 / nsub) * SAMPLE_NS


def crc4(d28):
    crc = 0
    for i in range(27, -1, -1):
        bit = ((d28 >> i) & 1) ^ ((crc >> 3) & 1)
        crc = (crc << 1) & 0xF
        if bit:
            crc ^= 0x3
    return crc


def marker_word(code, rung, frame):
    d = (code << 20) | ((rung & 0xF) << 16) | (frame & 0xFFFF)
    return (d << 4) | crc4(d)


def decode_marker(bits36):
    """bits36: 36 cell values (0/1). Returns (code, rung, frame) or None if invalid."""
    if list(bits36[:2]) != [1, 0] or list(bits36[-2:]) != [0, 1]:
        return None
    w = 0
    for b in bits36[2:34]:
        w = (w << 1) | int(b)
    d = w >> 4
    if crc4(d) != (w & 0xF):
        return None
    return d >> 20, (d >> 16) & 0xF, d & 0xFFFF
