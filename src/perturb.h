#pragma once
// Test catalog and perturbation model ("perturb/1").
//
// Every perturbation is a pure function of the absolute line number n since
// the test started (n = frame * 525 + line_index), or of the frame number,
// so any line of any frame can be computed independently and the grader can
// reproduce the sequence from the manifest alone. tools/perturb_model.py
// mirrors this file.

#include <stdint.h>

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
};

// S4 variants (p1):
//   1 equalising pulses replaced by plain H sync     4 V-sync missing in field 1 every 30 frames
//   2 unserrated V-sync                              5 V-sync missing in field 1 every 6 frames
//   3 both 1 and 2                                   6 V-sync missing in every field
// "V-sync missing" renders the broad pulses as equalising pulses.

// One played line of the output stream: content of NTSC line `line`
// (1..525), possibly only its first half (456 samples), rendered with the
// test's alternate sync structure (alt_mods), or replaced by a line of snow.
enum SlotFlags : uint8_t { SLOT_HALF = 1, SLOT_ALT = 2, SLOT_NOISE = 4 };
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
    float p1[10], p2[10];
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
};

const Def *find(const char *id);
bool make(const char *id, int rung, uint32_t seed, Test *out);

float shift_ns(const Test &t, uint32_t n);
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

uint32_t hash32(uint32_t x);

}  // namespace perturb
