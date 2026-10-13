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

// How a line is drawn, beyond its structure: everything the S1/S2, C and L
// tests change. The defaults are standard NTSC-M.
struct Style {
    float sync_ire = -40.0f;       // sync tip (S1: shallow sync)
    float sync_edge = 3.4f;        // sync edge width, samples, ~140 ns rise (S2: slow edges)
    float burst_amp = 20.0f;       // burst peak, IRE (C1, C2)
    float chroma_rot_deg = 0.0f;   // picture chroma rotated against burst (C4: hue offset)
    float video_gain = 1.0f;       // picture luma and chroma above blanking (L4: level)
    float dc_ire = 0.0f;           // offset on the whole line (L3: hum)
    float noise_rms_ire = 0.0f;    // additive noise on the whole line (L2)
    uint32_t noise_seed = 0;
    uint8_t mods = 0;              // RenderMods (sync structure)
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
// blanking. `st` sets levels, edges, burst, hue, noise and sync structure.
void render_line(uint16_t *out, int n, Half a, Half b, int phase_off, float origin, float carrier_shift,
                 const ActiveSeg *segs, int nseg, const Levels &lv, const Style &st = Style());

// Approx. N(0,1) noise for sample k (Irwin-Hall of four 16-bit hashes).
float noise_sample(uint32_t seed, int k);
// Snow (uniform -15..100 IRE), sample k of rendering `seed`.
float snow_ire(uint32_t seed, int k);

// A line of noise with no sync at all ("snow", as in VHS search bars):
// uniform luma from -15 to 100 IRE, deterministic in `seed`.
void render_noise(uint16_t *out, int n, uint32_t seed, const Levels &lv);
// Overwrite samples [k0, k1) of a rendered line with snow (a tape dropout).
void overlay_snow(uint16_t *out, int k0, int k1, uint32_t seed, const Levels &lv);

}  // namespace render
