#pragma once
#include <stdint.h>
#include "line_render.h"
#include "perturb.h"

namespace gen {

using Levels = render::Levels;

bool begin(int pattern, Levels lv);
bool set_pattern(int pattern);
bool set_levels(Levels lv);
bool set_clock_ppm(float ppm);   // base trim of the sample clock
bool set_test(const perturb::Test &t);
// Timing errors move the colour carrier too (direct colour, U-matic/1")
// or leave it on a stable reference (colour-under, VHS/Betamax: default).
bool set_chroma_follows(bool follows);
bool chroma_follows();

int    pattern();
Levels levels();
float  clock_ppm();
double actual_sample_hz();
const perturb::Test &test();

int      unique_lines();      // distinct base lines in the current pattern
int      subsample_steps();   // fractional positions per 2-sample step
int      ext_samples();       // how far a line may be lengthened
uint32_t frames();            // frames written since the test started
uint32_t max_write_us();      // worst time to rewrite half the ring
uint32_t dma_restarts();      // times the frame watchdog restarted a stopped DMA
const char *dma_restart_note();  // test and uptime of the last restart ("" if none)

// Placement of absolute line n under the current test.
struct LinePlan { int32_t e; uint8_t v; float shift_ns; };
LinePlan plan(uint32_t n);
float plan_shift_ns(const LinePlan &p);  // the shift actually produced

// Freeze the ring (stop per-frame rewrites) so a frame can be read back.
void hold(bool on);
// Frame number currently in each half of the ring.
uint32_t frame_in_half(int h);

// The ring is two halves (one frame each) of variable length. Line indexes
// below address descriptors: half h starts at half_base(h).
int half_lines(int h);
int half_base(int h);
// Played length of ring line `index`, in samples.
int line_length(int index);

// DAC codes of ring line `index` as played; returns the count.
int read_line(int index, uint8_t *out, int max);

}  // namespace gen
