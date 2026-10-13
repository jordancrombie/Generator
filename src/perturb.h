#pragma once
// Test catalog and perturbation model ("perturb/1").
//
// Every perturbation is a pure function of the absolute line number n since
// the test started (n = frame * 525 + line_index), or of the frame number,
// so any line of any frame can be computed independently and the grader can
// reproduce the sequence from the manifest alone. tools/perturb_model.py
// mirrors this file.

#include <stdint.h>
#include "line_render.h"

namespace perturb {

enum Kind : uint8_t {
    NONE,            // V0: clean baseline
    JITTER_RANDOM,   // H1: p1 = rms ns (Irwin-Hall n=4 approx. Gaussian)
    JITTER_SINE,     // H2: p1 = peak ns, p2 = Hz
    STEP_SQUARE,     // H3: p1 = step us, toggles every 0.5 s
    HEAD_SWITCH,     // H4: p1 = us offset on head B (lines 520..256)
    DISPLACED_LINE,  // H5: p1 = samples, lines 100 and 362 only
    FREQ_OFFSET,     // F1: p1 = ppm on the sample clock
    MISSING_SYNC,    // S3: p1 = consecutive lines from line 100 with no H sync
    BAD_VBI,         // S4: p1 = variant, see vbi_variant below
    PROGRESSIVE,     // S5: 262-line progressive fields (240p), no half-lines
    LINE_COUNT,      // S6: p1 = lines removed (<0) or repeated (>0) per field
    FIELD_DROP,      // S7: p1 = every N frames, field 2 is dropped
    FIELD_WOW,       // S8: p1 = peak lines added/removed per field, p2 = Hz
    NOISE_BAR,       // S9: p1 = height of a moving band of snow, lines per field
    SYNC_DEPTH,      // S1: p1 = sync depth, IRE (40 nominal)
    SYNC_EDGE,       // S2: p1 = sync edge 10-90% rise, ns
    BURST_LEVEL,     // C1: p1 = burst peak-to-peak, IRE (40 nominal)
    NO_BURST,        // C2: burst removed
    PHASE_JITTER,    // C3: p1 = rms degrees of burst + chroma phase, line to line
    HUE_OFFSET,      // C4: p1 = degrees picture chroma is rotated against burst
    FH_UNLOCKED,     // F2: p1 = ppm of H rate against a fixed subcarrier
    FREQ_DRIFT,      // F3: p1 = peak ppm of H-rate drift, p2 = period s
    DROPOUTS,        // L1: p1 = dropout length us, p2 = dropouts per field
    NOISE,           // L2: p1 = SNR dB (unweighted, 100 IRE / rms)
    HUM,             // L3: p1 = 60 Hz hum, IRE peak-to-peak
    VIDEO_LEVEL,     // L4: p1 = picture level, % of nominal
    LOSS_DEAD,       // S10: p1 = consecutive fields of no signal (0 V), once a second
    LOSS_SNOW,       // S11: p1 = consecutive fields of snow (no sync), once a second
    NO_SIGNAL,       // V1: no signal at all (to see a TBC's own fallback output)
    FAULT_HUNT,      // X1: p1 = stress type; 3 s cycle: 1 s clean, 1 s stress, 1 s clean
};

// X1 cycle: frames [0, 30) clean, [30, 60) stress, [60, 90) clean (recovery).
constexpr int HUNT_CYCLE = 90, HUNT_STRESS_START = 30, HUNT_STRESS_END = 60;
// S10/S11: fields are lost in the first p1 of every LOSS_PERIOD fields.
constexpr int LOSS_PERIOD = 60;

// What a line's pre-rendered copies (the `v` index) differ in. Timing tests
// use them for sub-sample shifts; C3, L2 and L3 instead need a few versions of
// each line that differ in carrier phase, noise or DC level.
enum VariantMode : uint8_t { VM_TIMING, VM_CARRIER, VM_NOISE, VM_DC };

// S4 variants (p1):
//   1 equalising pulses replaced by plain H sync     4 V-sync missing in field 1 every 30 frames
//   2 unserrated V-sync                              5 V-sync missing in field 1 every 6 frames
//   3 both 1 and 2                                   6 V-sync missing in every field
// "V-sync missing" renders the broad pulses as equalising pulses.

// One played line of the output stream: content of NTSC line `line`
// (1..525), possibly only its first half (456 samples), rendered with the
// test's alternate sync structure (alt_mods), or replaced by a line of snow.
// SLOT_DROPOUT lines carry a dropout; bits 4-5 of the flags pick which of
// DROPOUT_VARIANTS (segment positions) it is.
// SLOT_DEAD lines are no signal at all (0 V, the sync-tip level).
enum SlotFlags : uint8_t { SLOT_HALF = 1, SLOT_ALT = 2, SLOT_NOISE = 4, SLOT_DROPOUT = 8, SLOT_DEAD = 64 };
constexpr int DROPOUT_VARIANTS = 4;
struct Slot { uint16_t line; uint8_t flags; };
constexpr int HALF_SAMPLES = 456;  // 455 rounded to the DAC's sample pairs
constexpr int NOISE_VARIANTS = 4;  // snow lines cycle through this many renderings

struct Def {
    const char *id;
    uint8_t code;       // marker test code: group << 4 | number
    const char *name;
    Kind kind;
    const char *p1_name, *p2_name;
    int rungs;
    float p1[12], p2[12];
    const char *targets;      // TBC classes the test is aimed at: "line,frame", "frame"
    const char *ideal_line;   // what a perfect line TBC outputs
    const char *ideal_frame;  // what a perfect frame TBC / synchroniser outputs
};

extern const Def DEFS[];
extern const int NUM_DEFS;

struct Test {
    const Def *def;
    int rung;        // 1-based
    float p1, p2;
    uint32_t seed;
    // Periodic tests (H2, F3, L3): the phase of line n is the low 32 bits of
    // n * phase_inc, as a fraction of a cycle. Exact integer arithmetic, so
    // the grader reproduces it bit for bit and the ESP32 avoids software
    // double math. phase_inc = round(2^32 * f / fH).
    uint32_t phase_inc;
};

const Def *find(const char *id);
bool make(const char *id, int rung, uint32_t seed, Test *out);

// Sync shift of line n, ns. Double: F2/F3 accumulate shifts of many ms.
double shift_ns(const Test &t, uint32_t n);
// F2 only: the line's quantized shift in steps of 2/nsub samples, computed in
// exact integer arithmetic (its shifts land exactly on half-steps, where
// float rounding would differ between implementations).
int32_t fh_unlocked_steps(const Test &t, uint32_t n, int nsub);
// Largest increase in sync position from one line to the next, in samples.
float max_delta_samples(const Test &t);
float clock_ppm(const Test &t);

// The lines played for frame `frame` (normally lines 1..525 in order).
// Writes at most `max` slots and returns the count.
int frame_slots(const Test &t, uint32_t frame, Slot *out, int max);
// Nominal samples played before `frame`, mod 4 (sets its subcarrier phase).
uint32_t frame_start_mod4(const Test &t, uint32_t frame);
// Render modifiers (render::RenderMods) for this line's SLOT_ALT copy;
// 0 when the test never alters the line.
uint8_t alt_mods(const Test &t, int line);
// Which snow rendering a SLOT_NOISE slot uses.
inline int noise_variant(uint32_t frame, int slot) { return (int)((frame * 7 + (uint32_t)slot) % NOISE_VARIANTS); }

// Whether the test needs snow lines / a no-signal line rendered.
bool uses_snow(const Test &t);
bool uses_dead(const Test &t);

// Static rendering style for the whole test (S1, S2, C1, C2, C4, L2, L4).
render::Style base_style(const Test &t);
VariantMode variant_mode(const Test &t);
// Which copy (0..nsub-1) line n uses, for the non-timing variant modes.
int line_variant(const Test &t, uint32_t n, int nsub);
// What copy v carries: carrier phase (deg), DC level (IRE) or noise seed.
float variant_value(const Test &t, int v, int nsub);
// L1: samples [k0, k1) after the sync point covered by dropout variant `var`.
void dropout_span(const Test &t, int var, float *k0, float *k1);

uint32_t hash32(uint32_t x);

}  // namespace perturb
