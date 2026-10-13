#include "perturb.h"

#include <math.h>
#include <string.h>
#include "ntsc_timing.h"

using namespace ntsc;

namespace perturb {

const Def DEFS[] = {
    {"V0", 0x00, "clean baseline", NONE, "", "", 1, {0}, {0}},
    {"H1", 0x11, "random line jitter", JITTER_RANDOM, "rms_ns", "", 6,
     {5, 10, 20, 50, 100, 200}, {0}},
    {"H2", 0x12, "sinusoidal jitter", JITTER_SINE, "peak_ns", "hz", 6,
     {50, 100, 200, 500, 1000, 2000}, {1, 3, 5, 10, 20, 30}},
    {"H3", 0x13, "timing step", STEP_SQUARE, "step_us", "", 5,
     {0.25f, 0.5f, 1, 2, 5}, {0}},
    {"H4", 0x14, "head-switch skew", HEAD_SWITCH, "skew_us", "", 5,
     {0.5f, 1, 2, 5, 10}, {0}},
    {"H5", 0x15, "displaced single line", DISPLACED_LINE, "samples", "", 8,
     {2, -2, 4, -4, 10, -10, 20, -20}, {0}},
    {"F1", 0x21, "clock offset", FREQ_OFFSET, "ppm", "", 10,
     {10, -10, 30, -30, 100, -100, 300, -300, 1000, -1000}, {0}},
    {"S3", 0x33, "missing H syncs", MISSING_SYNC, "lines", "", 5, {1, 2, 5, 10, 20}, {0}},
    {"S5", 0x35, "progressive 240p", PROGRESSIVE, "", "", 1, {0}, {0}},
    {"S6", 0x36, "line count per field", LINE_COUNT, "delta_lines", "", 6, {-1, 1, -3, 3, -10, 10}, {0}},
    {"S7", 0x37, "dropped field", FIELD_DROP, "every_frames", "", 5, {60, 30, 15, 6, 2}, {0}},
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

bool needs_nosync(const Test &t, int line) {
    return t.def->kind == MISSING_SYNC && line >= 100 && line < 100 + (int)t.p1;
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
        case LINE_COUNT: {  // lines removed or repeated at line 200 (F1) and 462 (F2)
            int d = (int)t.p1;
            for (int L = 1; L <= LINES_PER_FRAME; L++) {
                if (d < 0 && ((L >= 200 && L < 200 - d) || (L >= 462 && L < 462 - d))) continue;
                add(L, 0);
                if (d > 0 && (L == 200 || L == 462))
                    for (int j = 0; j < d; j++) add(L, 0);
            }
            break;
        }
        default:
            for (int L = 1; L <= LINES_PER_FRAME; L++) add(L, needs_nosync(t, L) ? SLOT_NOSYNC : 0);
    }
    return n;
}

}  // namespace perturb
