#include "perturb.h"

#include <math.h>
#include <string.h>
#include "line_render.h"
#include "ntsc_timing.h"

using namespace ntsc;

namespace perturb {

// Ideal-output wording shared by several tests.
#define BOTH "line,frame"
#define FRAME "frame"
#define TIMING_FIXED "timing corrected; vertical edges straight, picture steady"
#define STANDARD_FIELDS "standard 262.5-line fields; drops/repeats allowed, no roll or tear"

const Def DEFS[] = {
    {"V0", 0x00, "clean baseline", NONE, "", "", 1, {0}, {0}, BOTH,
     "identical to the input", "identical to the input"},
    {"H1", 0x11, "random line jitter", JITTER_RANDOM, "rms_ns", "", 6,
     {5, 10, 20, 50, 100, 200}, {0}, BOTH, TIMING_FIXED, TIMING_FIXED},
    {"H2", 0x12, "sinusoidal jitter", JITTER_SINE, "peak_ns", "hz", 6,
     {50, 100, 200, 500, 1000, 2000}, {1, 3, 5, 10, 20, 30}, BOTH, TIMING_FIXED, TIMING_FIXED},
    {"H3", 0x13, "timing step", STEP_SQUARE, "step_us", "", 5,
     {0.25f, 0.5f, 1, 2, 5}, {0}, BOTH, TIMING_FIXED " within a few lines of each step",
     TIMING_FIXED " within a few lines of each step"},
    {"H4", 0x14, "head-switch skew", HEAD_SWITCH, "skew_us", "", 5,
     {0.5f, 1, 2, 5, 10}, {0}, BOTH, TIMING_FIXED "; no bend at the head switch",
     TIMING_FIXED "; no bend at the head switch"},
    {"H5", 0x15, "displaced single line", DISPLACED_LINE, "samples", "", 8,
     {2, -2, 4, -4, 10, -10, 20, -20}, {0}, BOTH, TIMING_FIXED, TIMING_FIXED},
    {"F1", 0x21, "clock offset", FREQ_OFFSET, "ppm", "", 10,
     {10, -10, 30, -30, 100, -100, 300, -300, 1000, -1000}, {0}, BOTH,
     "locks and follows the input rate; picture steady",
     "locks; output at its own (or the reference) rate, dropping/repeating frames as needed"},
    {"S3", 0x33, "missing H syncs", MISSING_SYNC, "lines", "", 5, {1, 2, 5, 10, 20}, {0}, BOTH,
     "flywheels through the missing syncs; no tear", "flywheels through the missing syncs; no tear"},
    {"S4", 0x34, "malformed vertical interval", BAD_VBI, "variant", "", 6, {1, 2, 3, 4, 5, 6}, {0}, FRAME,
     "passes the vertical interval as received", "stable vertical lock; standard vertical interval out"},
    {"S5", 0x35, "progressive 240p", PROGRESSIVE, "", "", 1, {0}, {0}, FRAME,
     "passes 240p unchanged", "stable output: 240p passed or converted to standard fields"},
    {"S6", 0x36, "line count per field", LINE_COUNT, "delta_lines", "", 6, {-1, 1, -3, 3, -10, 10}, {0}, FRAME,
     "passes the line count unchanged", STANDARD_FIELDS},
    {"S7", 0x37, "dropped field", FIELD_DROP, "every_frames", "", 5, {60, 30, 15, 6, 2}, {0}, FRAME,
     "passes the dropped field unchanged", STANDARD_FIELDS},
    {"S8", 0x38, "field-length wow", FIELD_WOW, "peak_lines", "hz", 5, {1, 2, 5, 10, 20}, {0.5f, 1, 1, 2, 2}, FRAME,
     "passes the varying field length unchanged", STANDARD_FIELDS},
    {"S9", 0x39, "search noise bar", NOISE_BAR, "lines", "", 5, {2, 5, 10, 20, 40}, {0}, BOTH,
     "flywheels through the bar; the rest of the picture straight",
     "stable lock; bar passed or concealed; no roll"},
};
const int NUM_DEFS = sizeof(DEFS) / sizeof(DEFS[0]);

const Def *find(const char *id) {
    for (int i = 0; i < NUM_DEFS; i++)
        if (!strcasecmp(DEFS[i].id, id)) return &DEFS[i];
    return nullptr;
}

bool make(const char *id, int rung, uint32_t seed, Test *out) {
    const Def *d = find(id);
    if (!d || rung < 1 || rung > d->rungs) return false;
    *out = {d, rung, d->p1[rung - 1], d->p2[rung - 1], seed};
    return true;
}

// lowbias32 (Chris Wellons)
uint32_t hash32(uint32_t x) {
    x ^= x >> 16; x *= 0x7feb352du;
    x ^= x >> 15; x *= 0x846ca68bu;
    x ^= x >> 16;
    return x;
}

// Approximately N(0,1): sum of four 16-bit uniforms (Irwin-Hall), integer
// exact so other implementations reproduce it bit for bit. Bounded at
// +-3.46 sigma.
static float gauss(uint32_t seed, uint32_t n) {
    uint32_t sum = 0;
    for (uint32_t i = 0; i < 4; i++) sum += hash32(seed + 0x9E3779B9u * (4u * n + i)) & 0xFFFF;
    const float mean = 4 * 32767.5f, sd = 65536.0f * 0.57735027f;
    return ((float)sum - mean) / sd;
}

float shift_ns(const Test &t, uint32_t n) {
    switch (t.def->kind) {
        case JITTER_RANDOM: return t.p1 * gauss(t.seed, n);
        case JITTER_SINE: {
            // Phase in double (n grows large), sine in float: double sin is
            // emulated in software and too slow for 525 lines per frame.
            double cyc = fmod((double)n * t.p2 / FH_HZ, 1.0);
            return t.p1 * sinf(2.0f * (float)M_PI * (float)cyc);
        }
        case STEP_SQUARE: {
            const uint32_t half = 7867;  // lines in 0.5 s
            return ((n / half) & 1) ? t.p1 * 1000.0f : 0.0f;
        }
        case HEAD_SWITCH: {
            int L = n % LINES_PER_FRAME + 1;
            return (L >= 257 && L < 520) ? 0.0f : t.p1 * 1000.0f;
        }
        case DISPLACED_LINE: {
            int L = n % LINES_PER_FRAME + 1;
            return (L == 100 || L == 362) ? t.p1 * (float)SAMPLE_NS : 0.0f;
        }
        default: return 0.0f;
    }
}

float max_delta_samples(const Test &t) {
    const float ns = (float)SAMPLE_NS;
    switch (t.def->kind) {
        case JITTER_RANDOM: return 2 * 3.47f * t.p1 / ns;
        case JITTER_SINE:   return t.p1 * 2 * (float)M_PI * t.p2 / (float)FH_HZ / ns;
        case STEP_SQUARE:
        case HEAD_SWITCH:   return t.p1 * 1000.0f / ns;
        case DISPLACED_LINE: return fabsf(t.p1);
        default: return 0;
    }
}

float clock_ppm(const Test &t) { return t.def->kind == FREQ_OFFSET ? t.p1 : 0.0f; }

uint32_t frame_start_mod4(const Test &t, uint32_t frame) {
    // A standard frame is 525 x 910 samples = 2 (mod 4); so are frames with
    // whole lines added or removed in pairs (S6). A 240p frame (524 lines)
    // and a dropped-field frame (262 lines + 456) are 0 (mod 4).
    switch (t.def->kind) {
        case PROGRESSIVE: return 0;
        case FIELD_DROP: {
            uint32_t every = (uint32_t)t.p1;
            return (2 * (frame - frame / every)) & 3;
        }
        default: return (2 * frame) & 3;
    }
}

uint8_t alt_mods(const Test &t, int line) {
    using namespace render;
    if (t.def->kind == MISSING_SYNC) return (line >= 100 && line < 100 + (int)t.p1) ? MOD_NO_HSYNC : 0;
    if (t.def->kind != BAD_VBI) return 0;
    LineSpec ls = line_spec(line);
    bool eq = ls.a == H_EQ || ls.b == H_EQ, vs = ls.a == H_VS || ls.b == H_VS;
    uint8_t m = 0;
    switch ((int)t.p1) {
        case 1: if (eq) m = MOD_EQ_AS_HSYNC; break;
        case 2: if (vs) m = MOD_VS_UNSERRATED; break;
        case 3: m = (eq ? MOD_EQ_AS_HSYNC : 0) | (vs ? MOD_VS_UNSERRATED : 0); break;
        case 4:
        case 5: if (vs && line <= 9) m = MOD_VS_AS_EQ; break;  // field 1 only
        case 6: if (vs) m = MOD_VS_AS_EQ; break;
    }
    return m;
}

// Lines added (>0) or removed (<0) per field by S8 in this frame.
static int wow_lines(const Test &t, uint32_t frame) {
    const double frame_hz = FH_HZ / LINES_PER_FRAME;
    double d = t.p1 * sin(2.0 * M_PI * t.p2 * frame / frame_hz);  // once per frame: double is fine
    return (int)lround(d);
}

int frame_slots(const Test &t, uint32_t frame, Slot *out, int max) {
    int n = 0;
    auto add = [&](int line, uint8_t flags) { if (n < max) out[n++] = {(uint16_t)line, flags}; };
    switch (t.def->kind) {
        case PROGRESSIVE:  // two 262-line fields, both with field-1 timing
            for (int f = 0; f < 2; f++)
                for (int L = 1; L <= 262; L++) add(L, 0);
            break;
        case FIELD_DROP: {
            uint32_t every = (uint32_t)t.p1;
            if (frame % every == every - 1) {  // field 1 only: ends half-way along line 263
                for (int L = 1; L <= 262; L++) add(L, 0);
                add(263, SLOT_HALF);
            } else {
                for (int L = 1; L <= LINES_PER_FRAME; L++) add(L, 0);
            }
            break;
        }
        case LINE_COUNT:    // lines removed or repeated at line 200 (F1) and 462 (F2)
        case FIELD_WOW: {
            int d = t.def->kind == LINE_COUNT ? (int)t.p1 : wow_lines(t, frame);
            for (int L = 1; L <= LINES_PER_FRAME; L++) {
                if (d < 0 && ((L >= 200 && L < 200 - d) || (L >= 462 && L < 462 - d))) continue;
                add(L, 0);
                if (d > 0 && (L == 200 || L == 462))
                    for (int j = 0; j < d; j++) add(L, 0);
            }
            break;
        }
        case NOISE_BAR: {  // a band moving down 23 lines a frame through lines 30..229 (+263)
            int top = 30 + (int)((frame * 23) % 200), h = (int)t.p1;
            for (int L = 1; L <= LINES_PER_FRAME; L++) {
                bool bar = (L >= top && L < top + h) || (L >= top + 263 && L < top + 263 + h);
                add(L, bar ? SLOT_NOISE : 0);
            }
            break;
        }
        case BAD_VBI: {
            int v = (int)t.p1;
            // Variants 4 and 5 alter only some frames; the rest alter every frame.
            bool active = v == 4 ? frame % 30 == 0 : v == 5 ? frame % 6 == 0 : true;
            for (int L = 1; L <= LINES_PER_FRAME; L++) add(L, active && alt_mods(t, L) ? SLOT_ALT : 0);
            break;
        }
        default:
            for (int L = 1; L <= LINES_PER_FRAME; L++) add(L, alt_mods(t, L) ? SLOT_ALT : 0);
    }
    return n;
}

}  // namespace perturb
