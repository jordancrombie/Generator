#pragma once
// NTSC-M (SMPTE 170M) timing expressed at 4x subcarrier sampling.
// All horizontal positions are in samples relative to the 50% point of the
// horizontal sync leading edge (sample 0). Fractional positions are allowed:
// edges are rendered band-limited, so sub-sample placement is meaningful.

#include <stdint.h>

namespace ntsc {

constexpr double FSC_HZ          = 315.0e6 / 88.0;   // 3.579545... MHz
constexpr double SAMPLE_HZ       = 4.0 * FSC_HZ;      // 14.318181... MHz
constexpr double US              = SAMPLE_HZ / 1e6;   // samples per microsecond

constexpr int    SAMPLES_PER_LINE = 910;              // 227.5 subcarrier cycles
constexpr int    HALF_LINE        = SAMPLES_PER_LINE / 2;
constexpr int    LINES_PER_FRAME  = 525;

// Horizontal timing (SMPTE 170M nominal values)
constexpr double HSYNC_WIDTH     = 4.7  * US;
constexpr double EQ_WIDTH        = 2.3  * US;
constexpr double SERRATION_WIDTH = 4.7  * US;
constexpr double BURST_START     = 5.3  * US;          // 19 cycles after sync
constexpr double BURST_CYCLES    = 9.0;
constexpr double FRONT_PORCH     = 1.5  * US;
constexpr double H_BLANKING      = 10.9 * US;
constexpr double ACTIVE_START    = H_BLANKING - FRONT_PORCH;          // 9.4 us
constexpr double ACTIVE_END      = SAMPLES_PER_LINE - FRONT_PORCH;     // 52.6 us later
constexpr double ACTIVE_WIDTH    = ACTIVE_END - ACTIVE_START;

// Edge widths (raised-cosine, full width in samples; 10-90% ~= 0.6 x width)
constexpr double SYNC_EDGE   = 3.4;   // ~140 ns rise
constexpr double LUMA_EDGE   = 2.5;
constexpr double CHROMA_EDGE = 8.0;
constexpr double BURST_EDGE  = 4.0;

// Levels in IRE
constexpr double IRE_SYNC   = -40.0;
constexpr double IRE_BLANK  = 0.0;
constexpr double IRE_SETUP  = 7.5;
constexpr double IRE_WHITE  = 100.0;
constexpr double BURST_AMPL = 20.0;  // 40 IRE p-p
constexpr double RGB_SCALE  = IRE_WHITE - IRE_SETUP;  // 92.5 IRE per unit Y

constexpr int ACTIVE_ROWS = 486;  // D1 numbering: F1 lines 21-263, F2 283-525

// Line buffers place the sync 50% point LINE_ORIGIN samples in, so each
// line starts at blanking and can be lengthened by padding blank at its end.
constexpr double LINE_ORIGIN = 4.0;
constexpr int    MAX_EXT     = 200;      // max samples a line may be lengthened
constexpr int    MIN_LEN     = 600;      // shortest line the ring will play
constexpr double SAMPLE_NS   = 1e9 / SAMPLE_HZ;
constexpr double FH_HZ       = SAMPLE_HZ / SAMPLES_PER_LINE;  // 15734.2657

}  // namespace ntsc
