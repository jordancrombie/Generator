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

// Ways a line can be rendered differently from the standard (bitmask).
enum RenderMods : uint8_t {
    MOD_NO_HSYNC      = 1,   // no horizontal sync pulse (burst and picture stay)
    MOD_EQ_AS_HSYNC   = 2,   // equalising pulses replaced by plain H sync at line rate
    MOD_VS_UNSERRATED = 4,   // broad pulses without serrations
    MOD_VS_AS_EQ      = 8,   // vertical sync missing: broad pulses become EQ pulses
    MOD_LOW_START     = 16,  // (internal) unserrated V-sync carried over from the previous line
};

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
// blanking. `mods` (RenderMods) alters the sync structure.
void render_line(uint16_t *out, int n, Half a, Half b, int phase_off, float origin, float carrier_shift,
                 const ActiveSeg *segs, int nseg, const Levels &lv, uint8_t mods = 0);

// A line of noise with no sync at all ("snow", as in VHS search bars):
// uniform luma from -15 to 100 IRE, deterministic in `seed`.
void render_noise(uint16_t *out, int n, uint32_t seed, const Levels &lv);

}  // namespace render
