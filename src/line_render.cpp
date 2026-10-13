#include "line_render.h"

#include <math.h>
#include "i2s_dac.h"
#include "ntsc_timing.h"

using namespace ntsc;

namespace render {

LineSpec line_spec(int L) {
    auto row = [](int L) { return L <= 263 ? 2 * (L - 21) + 1 : 2 * (L - 283); };
    if (L <= 3) return {H_EQ, H_EQ, -1};
    if (L <= 6) return {H_VS, H_VS, -1};
    if (L <= 9) return {H_EQ, H_EQ, -1};
    if (L <= 20) return {H_BLANK, H_BLANK, -1};
    if (L <= 262) return {H_ACTIVE, H_ACTIVE, row(L)};
    if (L == 263) return {H_ACTIVE, H_EQ, row(L)};
    if (L <= 265) return {H_EQ, H_EQ, -1};
    if (L == 266) return {H_EQ, H_VS, -1};
    if (L <= 268) return {H_VS, H_VS, -1};
    if (L == 269) return {H_VS, H_EQ, -1};
    if (L <= 271) return {H_EQ, H_EQ, -1};
    if (L == 272) return {H_EQ, H_BLANK, -1};
    if (L <= 282) return {H_BLANK, H_BLANK, -1};
    if (L == 283) return {H_BLANK, H_ACTIVE, row(L)};
    return {H_ACTIVE, H_ACTIVE, row(L)};
}

// Raised-cosine step: 0 below -w/2, 1 above +w/2.
static inline float step(float t, float w) {
    if (t <= -0.5f * w) return 0.0f;
    if (t >= 0.5f * w) return 1.0f;
    return 0.5f - 0.5f * cosf((float)M_PI * (t / w + 0.5f));
}
static inline float pulse(float t, float t0, float t1, float w) { return step(t - t0, w) - step(t - t1, w); }

int place_segments(const Segment *raw, int n, Half a, Half b, ActiveSeg *out, int max) {
    // Active window for the half-line arrangement (lines 263 and 283 are half picture).
    float w0 = ACTIVE_START, w1 = ACTIVE_END;
    if (a == H_ACTIVE && b != H_ACTIVE) w1 = HALF_LINE - FRONT_PORCH;
    if (a != H_ACTIVE && b == H_ACTIVE) w0 = HALF_LINE + FRONT_PORCH;
    int m = 0;
    for (int i = 0; i < n && m < max; i++) {
        float s0 = ACTIVE_START + raw[i].x0 * ACTIVE_WIDTH;
        float s1 = ACTIVE_START + raw[i].x1 * ACTIVE_WIDTH;
        float c0 = s0 < w0 ? w0 : s0, c1 = s1 > w1 ? w1 : s1;
        if (c1 <= c0) continue;
        float span = s1 - s0;  // keep a ramp's slope when clipping
        float y0 = raw[i].y0 + (raw[i].y1 - raw[i].y0) * (c0 - s0) / span;
        float y1 = raw[i].y0 + (raw[i].y1 - raw[i].y0) * (c1 - s0) / span;
        out[m++] = {c0, c1, y0, y1, raw[i].u, raw[i].v};
    }
    return m;
}

void render_line(uint16_t *out, int n, Half a, Half b, int phase_off, float origin, float carrier_shift,
                 const ActiveSeg *segs, int nseg, const Levels &lv, bool no_hsync) {
    // Timing constants are double; the ESP32 emulates double math in
    // software, so take float copies before the per-sample loop.
    const float H = HALF_LINE;
    const float eq_w = EQ_WIDTH, vs_w = H - (float)SERRATION_WIDTH, hs_w = HSYNC_WIDTH, sync_e = SYNC_EDGE;
    const float b0 = BURST_START, b1 = (float)(BURST_START + BURST_CYCLES * 4), burst_e = BURST_EDGE;
    const float luma_e = LUMA_EDGE, chroma_e = CHROMA_EDGE, burst_a = BURST_AMPL;
    const float sync_ire = IRE_SYNC, blank_ire = IRE_BLANK;
    const float scale = (lv.white_code - lv.sync_code) / (float)(IRE_WHITE - IRE_SYNC);
    static const int SIN4[4] = {0, 1, 0, -1}, COS4[4] = {1, 0, -1, 0};
    const float phi = (float)M_PI_2 * carrier_shift;
    const float cphi = cosf(phi), sphi = sinf(phi);
    const bool burst = a == H_BLANK || a == H_ACTIVE;
    int first = 0;

    for (int k = 0; k < n; k++) {
        const float t = (float)k - origin;
        float ire = blank_ire;

        float low = 0;
        for (int h = 0; h < 2; h++) {
            Half kind = h ? b : a;
            float o = h * H;
            switch (kind) {
                case H_EQ:     low += pulse(t, o, o + eq_w, sync_e); break;
                case H_VS:     low += pulse(t, o, o + vs_w, sync_e); break;
                case H_BLANK:
                case H_ACTIVE: if (h == 0 && !no_hsync) low += pulse(t, 0, hs_w, sync_e); break;
            }
        }
        ire += (sync_ire - blank_ire) * low;

        const int ph = (k + phase_off) & 3;
        const float sn = SIN4[ph] * cphi - COS4[ph] * sphi;   // sin(a - phi)
        const float cs = COS4[ph] * cphi + SIN4[ph] * sphi;   // cos(a - phi)
        if (burst) {  // colour burst on the -U axis
            float env = pulse(t, b0, b1, burst_e);
            ire += -burst_a * env * sn;
        }
        // Segments are sorted left to right; only those within an edge width
        // of t contribute, so skip past ones that ended well before it.
        const float reach = 0.5f * chroma_e;
        while (first < nseg && segs[first].s1 + reach < t) first++;
        for (int i = first; i < nseg && segs[i].s0 - reach <= t; i++) {
            const ActiveSeg &s = segs[i];
            float wl = pulse(t, s.s0, s.s1, luma_e);
            if (wl > 0) {
                float f = (t - s.s0) / (s.s1 - s.s0);
                f = f < 0 ? 0 : (f > 1 ? 1 : f);
                ire += wl * (s.y0 + (s.y1 - s.y0) * f);
            }
            if (s.u != 0 || s.v != 0) {
                float wc = pulse(t, s.s0, s.s1, chroma_e);
                ire += wc * (s.u * sn + s.v * cs);
            }
        }

        float code = lv.sync_code + (ire - sync_ire) * scale;
        int c = (int)lroundf(code);
        c = c < 0 ? 0 : (c > 255 ? 255 : c);
        out[i2s_dac::index(k)] = i2s_dac::sample((uint8_t)c);
    }
}

}  // namespace render
