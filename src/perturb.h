#pragma once
// Test catalog and perturbation model ("perturb/1").
//
// Every perturbation is a pure function of the absolute line number n since
// the test started (n = frame * 525 + line_index), so any line of any frame
// can be computed independently and the grader can reproduce the sequence
// from the manifest alone. tools/perturb_model.py mirrors this file.

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
    PROGRESSIVE,     // S5: 262-line progressive fields (240p), no half-lines
    LINE_COUNT,      // S6: p1 = lines removed (<0) or repeated (>0) per field
    FIELD_DROP,      // S7: p1 = every N frames, field 2 is dropped
};

// One played line of the output stream: content of NTSC line `line`
// (1..525), optionally only its first half (456 samples) or without H sync.
enum SlotFlags : uint8_t { SLOT_HALF = 1, SLOT_NOSYNC = 2 };
struct Slot { uint16_t line; uint8_t flags; };
constexpr int HALF_SAMPLES = 456;  // 455 rounded to the DAC's sample pairs

struct Def {
    const char *id;
    uint8_t code;       // marker test code: group << 4 | number
    const char *name;
    Kind kind;
    const char *p1_name, *p2_name;
    int rungs;
    float p1[10], p2[10];
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
// Whether this line needs a no-sync copy rendered.
bool needs_nosync(const Test &t, int line);

uint32_t hash32(uint32_t x);

}  // namespace perturb
