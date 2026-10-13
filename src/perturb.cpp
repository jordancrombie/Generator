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
    {"F2", 0x22, "H not locked to subcarrier", FH_UNLOCKED, "ppm", "", 8,
     {10, -10, 50, -50, 200, -200, 1000, -1000}, {0}, BOTH,
     "locks; hue correct (SCH drifts with the input)", "locks; hue correct, output SCH steady"},
    {"F3", 0x23, "slow H-rate drift", FREQ_DRIFT, "peak_ppm", "period_s", 3, {50, 300, 1000}, {10, 10, 10}, BOTH,
     "follows the drift; picture steady", "continuous lock; output at its own rate"},
    {"S1", 0x31, "sync amplitude", SYNC_DEPTH, "sync_ire", "", 5, {30, 25, 20, 15, 10}, {0}, BOTH,
     "locks; picture steady", "locks; sync regenerated at 40 IRE"},
    {"S2", 0x32, "slow sync edges", SYNC_EDGE, "rise_ns", "", 3, {250, 500, 1000}, {0}, BOTH,
     "locks; picture steady (no extra jitter)", "locks; output sync edges nominal"},
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
    {"C1", 0x41, "burst amplitude", BURST_LEVEL, "burst_pp_ire", "", 4, {30, 20, 10, 60}, {0}, BOTH,
     "burst and chroma passed, or saturation corrected by ACC",
     "burst regenerated at 40 IRE p-p; saturation follows burst (ACC) or is unchanged"},
    {"C2", 0x42, "no burst", NO_BURST, "", "", 1, {0}, {0}, BOTH,
     "monochrome output, no colour noise", "monochrome output (colour killer), no colour noise"},
    {"C3", 0x43, "burst/chroma phase jitter", PHASE_JITTER, "rms_deg", "", 4, {2, 5, 10, 20}, {0}, BOTH,
     "hue steady if it corrects chroma phase per line; otherwise passed",
     "hue steady if it corrects chroma phase per line; otherwise passed"},
    {"C4", 0x44, "hue offset", HUE_OFFSET, "deg", "", 4, {5, 10, 20, 45}, {0}, BOTH,
     "hue offset passed unchanged", "hue offset passed unchanged (proc amp at unity)"},
    {"L1", 0x51, "dropouts", DROPOUTS, "length_us", "per_field", 5, {2, 5, 10, 20, 50}, {1, 2, 5, 5, 10}, BOTH,
     "dropouts passed (or concealed, given a DOC signal); timing unaffected",
     "dropouts passed (or concealed, given a DOC signal); timing unaffected"},
    {"L2", 0x52, "additive noise", NOISE, "snr_db", "", 5, {40, 34, 28, 22, 16}, {0}, BOTH,
     "locks; noise passed, not amplified", "locks; noise passed, not amplified"},
    {"L3", 0x53, "60 Hz hum", HUM, "pp_ire", "", 4, {5, 10, 20, 40}, {0}, BOTH,
     "hum passed", "hum removed by clamping (DC restore)"},
    {"L4", 0x54, "video level", VIDEO_LEVEL, "white_pct", "", 4, {80, 90, 110, 120}, {0}, BOTH,
     "level passed unchanged", "level passed, or corrected by AGC/proc amp"},
    {"S10", 0x3A, "lost fields (no signal)", LOSS_DEAD, "fields", "", 11, {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11}, {0}, BOTH,
     "passes the gap through and relocks at once",
     "holds the last frame through the gap (no fallback bars, no roll) and relocks at once"},
    {"S11", 0x3B, "lost fields (snow)", LOSS_SNOW, "fields", "", 11, {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11}, {0}, BOTH,
     "passes the gap through and relocks at once",
     "holds the last frame through the gap (no fallback bars, no roll) and relocks at once"},
    {"V1", 0x01, "no signal", NO_SIGNAL, "", "", 1, {0}, {0}, BOTH,
     "nothing, or its own fallback output", "its own fallback output (e.g. bars or black)"},
    {"X1", 0x61, "fault hunt (stress, recovery)", FAULT_HUNT, "stress", "", 5, {1, 2, 3, 4, 5}, {0}, BOTH,
     "recovers fully after each stress burst",
     "no stuck or blended fields; clean again within a few frames of each stress burst"},
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
    *out = {d, rung, d->p1[rung - 1], d->p2[rung - 1], seed, 0};
    double hz = d->kind == JITTER_SINE ? out->p2 : d->kind == FREQ_DRIFT ? 1.0 / out->p2 : d->kind == HUM ? 60.0 : 0;
    out->phase_inc = (uint32_t)llround(4294967296.0 * hz / FH_HZ);
    return true;
}

// lowbias32 (Chris Wellons)
uint32_t hash32(uint32_t x) {
    x ^= x >> 16; x *= 0x7feb352du;
    x ^= x >> 15; x *= 0x846ca68bu;
    x ^= x >> 16;
    return x;
}

// Fraction of a cycle (0..1) of line n for a periodic test.
static inline float cycle(const Test &t, uint32_t n) {
    return (float)(uint32_t)((uint64_t)n * t.phase_inc) * 2.3283064e-10f;  // / 2^32
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

// X1: whether frame `frame` is in the stress part of its 3 s cycle.
static inline bool hunt_stress(uint32_t frame) {
    uint32_t ph = frame % HUNT_CYCLE;
    return ph >= HUNT_STRESS_START && ph < HUNT_STRESS_END;
}

double shift_ns(const Test &t, uint32_t n) {
    const double line_ns = 1e9 / FH_HZ;
    switch (t.def->kind) {
        case FAULT_HUNT: {  // X1 stress types 1, 4, 5 jitter; 5 adds a 5 us head-switch skew
            if (!hunt_stress(n / LINES_PER_FRAME)) return 0.0;
            int s = (int)t.p1, L = n % LINES_PER_FRAME + 1;
            float ns = s == 1 ? 200.0f * gauss(t.seed, n) : (s == 4 || s == 5) ? 100.0f * gauss(t.seed, n) : 0.0f;
            if (s == 5 && !(L >= 257 && L < 520)) ns += 5000.0f;
            return ns;
        }
        case FH_UNLOCKED:  // H runs p1 ppm fast: each line starts that much earlier
            return -(double)n * line_ns * t.p1 * 1e-6;
        case FREQ_DRIFT: {  // rate offset p1 sin(2 pi t / p2) ppm, integrated: peak p1e-6 * p2/pi s
            // 1 - cos(x) = 2 sin^2(x/2): keeps precision near x = 0, where cosf ~ 1.
            float amp_ns = t.p1 * 1e-6f * t.p2 / (2.0f * (float)M_PI) * 1e9f;
            float s = sinf((float)M_PI * cycle(t, n));
            return -(double)(amp_ns * 2.0f * s * s);
        }
        case JITTER_RANDOM: return t.p1 * gauss(t.seed, n);
        case JITTER_SINE: return t.p1 * sinf(2.0f * (float)M_PI * cycle(t, n));
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

int32_t fh_unlocked_steps(const Test &t, uint32_t n, int nsub) {
    // shift = -n * 910 * ppm * 1e-6 samples = -n * 910 * ppm * nsub / 2e6 steps, rounded half up
    int64_t num = (int64_t)n * 910 * (int64_t)lroundf(t.p1) * nsub + 1000000;
    int64_t q = num >= 0 ? num / 2000000 : -((-num + 1999999) / 2000000);  // floor division
    return (int32_t)-q;
}

float max_delta_samples(const Test &t) {
    const float ns = (float)SAMPLE_NS;
    switch (t.def->kind) {
        case JITTER_RANDOM: return 2 * 3.47f * t.p1 / ns;
        case JITTER_SINE:   return t.p1 * 2 * (float)M_PI * t.p2 / (float)FH_HZ / ns;
        case STEP_SQUARE:
        case HEAD_SWITCH:   return t.p1 * 1000.0f / ns;
        case DISPLACED_LINE: return fabsf(t.p1);
        case FH_UNLOCKED:
        case FREQ_DRIFT: return fabsf(t.p1) * 1e-6f * (float)(1e9 / FH_HZ) / ns + 1;
        case FAULT_HUNT: {
            int s = (int)t.p1;
            return s == 1 ? 2 * 3.47f * 200 / ns : s == 4 ? 2 * 3.47f * 100 / ns : s == 5 ? (5000 + 2 * 3.47f * 100) / ns : 0;
        }
        default: return 0;
    }
}

float clock_ppm(const Test &t) { return t.def->kind == FREQ_OFFSET ? t.p1 : 0.0f; }

// X1 stress type 2 drops field 2 of every odd frame in the stress window.
static inline bool hunt_drops_field(const Test &t, uint32_t frame) {
    return t.def->kind == FAULT_HUNT && (int)t.p1 == 2 && hunt_stress(frame) && (frame & 1);
}

uint32_t frame_start_mod4(const Test &t, uint32_t frame) {
    if (t.def->kind == FAULT_HUNT && (int)t.p1 == 2) {
        // Dropped-field frames are 0 (mod 4); count them before `frame`.
        auto odd_in = [](uint32_t a, uint32_t b) { return b > a ? (b / 2) - (a / 2) : 0u; };  // odd f in [a, b)
        uint32_t full = frame / HUNT_CYCLE, rem = frame % HUNT_CYCLE;
        uint32_t per = odd_in(HUNT_STRESS_START, HUNT_STRESS_END);
        uint32_t drops = full * per + odd_in(HUNT_STRESS_START, rem < (uint32_t)HUNT_STRESS_END ? (rem > (uint32_t)HUNT_STRESS_START ? rem : HUNT_STRESS_START) : HUNT_STRESS_END);
        return (2 * (frame - drops)) & 3;
    }
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
        case LOSS_DEAD:
        case LOSS_SNOW:
        case NO_SIGNAL: {
            uint8_t lost = t.def->kind == LOSS_SNOW ? SLOT_NOISE : SLOT_DEAD;
            for (int L = 1; L <= LINES_PER_FRAME; L++) {
                // Field 1 = lines 1..263 (263 carries its last half-line), field 2 = 264..525.
                uint32_t field = 2 * frame + (L >= 264 ? 1 : 0);
                bool gone = t.def->kind == NO_SIGNAL || (int)(field % LOSS_PERIOD) < (int)t.p1;
                add(L, gone ? lost : 0);
            }
            break;
        }
        case FAULT_HUNT: {
            int s = (int)t.p1;
            bool stress = hunt_stress(frame);
            if (hunt_drops_field(t, frame)) {  // stress 2: field 1 only
                for (int L = 1; L <= 262; L++) add(L, 0);
                add(263, SLOT_HALF);
                break;
            }
            int top = 30 + (int)((frame * 23) % 200), h = s == 4 ? 40 : 20;
            for (int L = 1; L <= LINES_PER_FRAME; L++) {
                uint8_t f = 0;
                uint32_t field = 2 * frame + (L >= 264 ? 1 : 0);
                if (stress && s == 3 && field % 20 < 3) f = SLOT_DEAD;           // 3 fields lost every 10 frames
                if (stress && s == 5 && field % 30 < 2) f = SLOT_DEAD;           // 2 fields lost every 15 frames
                if (stress && (s == 4 || s == 5) && !f &&
                    ((L >= top && L < top + h) || (L >= top + 263 && L < top + 263 + h)))
                    f = SLOT_NOISE;                                              // noise bar
                add(L, f);
            }
            break;
        }
        case DROPOUTS: {  // p2 dropouts per field, on lines 30..170 and 292..432 (colour-bar rows)
            for (int L = 1; L <= LINES_PER_FRAME; L++) add(L, 0);
            for (int j = 0; j < (int)t.p2; j++) {
                int L = 30 + (int)(hash32(t.seed * 2654435761u + frame * 64u + (uint32_t)j) % 141);
                uint8_t f = SLOT_DROPOUT | (uint8_t)((j % DROPOUT_VARIANTS) << 4);
                if (L - 1 < n) out[L - 1].flags = f;
                if (L + 261 < n) out[L + 261].flags = f;  // same place in field 2 (line L + 262)
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

bool uses_snow(const Test &t) {
    return t.def->kind == NOISE_BAR || t.def->kind == LOSS_SNOW ||
           (t.def->kind == FAULT_HUNT && ((int)t.p1 == 4 || (int)t.p1 == 5));
}

bool uses_dead(const Test &t) {
    return t.def->kind == LOSS_DEAD || t.def->kind == NO_SIGNAL ||
           (t.def->kind == FAULT_HUNT && ((int)t.p1 == 3 || (int)t.p1 == 5));
}

render::Style base_style(const Test &t) {
    render::Style s;
    switch (t.def->kind) {
        case SYNC_DEPTH:  s.sync_ire = -t.p1; break;
        case SYNC_EDGE:   s.sync_edge = t.p1 / 0.59f / (float)SAMPLE_NS; break;  // 10-90% of a raised cosine = 0.59 width
        case BURST_LEVEL: s.burst_amp = t.p1 / 2; break;
        case NO_BURST:    s.burst_amp = 0; break;
        case HUE_OFFSET:  s.chroma_rot_deg = t.p1; break;
        case NOISE:       s.noise_rms_ire = 100.0f / powf(10.0f, t.p1 / 20.0f); break;
        case VIDEO_LEVEL: s.video_gain = t.p1 / 100.0f; break;
        default: break;
    }
    return s;
}

VariantMode variant_mode(const Test &t) {
    switch (t.def->kind) {
        case PHASE_JITTER: return VM_CARRIER;
        case NOISE:        return VM_NOISE;
        case HUM:          return VM_DC;
        default:           return VM_TIMING;
    }
}

static int clamp_v(long v, int nsub) { return v < 0 ? 0 : (v >= nsub ? nsub - 1 : (int)v); }

int line_variant(const Test &t, uint32_t n, int nsub) {
    if (nsub <= 1) return 0;
    switch (variant_mode(t)) {
        case VM_CARRIER: {  // nearest of nsub phases spanning +-3.47 sigma
            float step = 2 * 3.47f * t.p1 / (nsub - 1);
            return clamp_v(lroundf(t.p1 * gauss(t.seed, n) / step + (nsub - 1) / 2.0f), nsub);
        }
        case VM_NOISE: return (int)(hash32(t.seed * 0x9E3779B9u + n) % (uint32_t)nsub);
        case VM_DC: {  // nearest of nsub levels across the hum's p-p range
            float h = 0.5f * sinf(2.0f * (float)M_PI * cycle(t, n));  // in units of p-p
            return clamp_v(lroundf((h + 0.5f) * (nsub - 1)), nsub);
        }
        default: return 0;
    }
}

float variant_value(const Test &t, int v, int nsub) {
    float mid = (nsub - 1) / 2.0f;
    switch (variant_mode(t)) {
        case VM_CARRIER: return nsub > 1 ? (v - mid) * 2 * 3.47f * t.p1 / (nsub - 1) : 0;
        case VM_DC:      return nsub > 1 ? -0.5f * t.p1 + v * t.p1 / (nsub - 1) : 0;
        case VM_NOISE:   return (float)(v + 1);
        default:         return 0;
    }
}

void dropout_span(const Test &t, int var, float *k0, float *k1) {
    // Start at a fraction of the room left in the active window, so even a
    // 50 us dropout stays inside the picture.
    static const float POS[DROPOUT_VARIANTS] = {0.15f, 0.35f, 0.55f, 0.75f};
    float len = t.p1 * (float)US, room = (float)ACTIVE_WIDTH - len;
    *k0 = (float)ACTIVE_START + POS[var] * (room > 0 ? room : 0);
    *k1 = *k0 + len;
}

}  // namespace perturb
