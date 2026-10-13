#pragma once
// Rendering of one NTSC line into DAC samples.

#include <stdint.h>
#include "patterns.h"

namespace render {

// Every line is two halves. Lines whose first half is BLANK or ACTIVE start
// with a horizontal sync and carry colour burst; EQ and VS halves are
// equalising and broad (vertical sync) pulses.
enum Half : uint8_t { H_EQ, H_VS, H_BLANK, H_ACTIVE };

struct LineSpec {
    Half a, b;
    int row;  // picture row for active halves, -1 otherwise
};

LineSpec line_spec(int line /* 1..525 */);

// Active-video segment in samples relative to the sync 50% point.
struct ActiveSeg { float s0, s1, y0, y1, u, v; };

struct Levels {
    float sync_code;   // DAC code for -40 IRE
    float white_code;  // DAC code for 100 IRE
};

// Convert pattern segments (fractions of the active width) to samples,
// clipped to the active window of this half-line arrangement.
int place_segments(const Segment *raw, int n, Half a, Half b, ActiveSeg *out, int max);

// Render `n` samples. `origin` is where the sync 50% point falls, in samples
// (fractional allowed: sync, picture and colour envelopes move with it).
// The subcarrier is (pi/2)(k + phase_off) delayed by `carrier_shift` samples:
// pass origin - LINE_ORIGIN for direct colour (carrier moves with the
// timing), 0 for colour-under (VHS/Betamax: carrier stays on a stable
// reference while sync and picture jitter). Samples past the line's end are
// blanking. `no_hsync` leaves out the horizontal sync pulse (burst and
// picture stay).
void render_line(uint16_t *out, int n, Half a, Half b, int phase_off, float origin, float carrier_shift,
                 const ActiveSeg *segs, int nseg, const Levels &lv, bool no_hsync = false);

}  // namespace render
