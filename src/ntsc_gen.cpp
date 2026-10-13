#include "ntsc_gen.h"

#include <math.h>
#include <string.h>
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "i2s_dac.h"
#include "ntsc_timing.h"
#include "patterns.h"

using namespace ntsc;

namespace gen {

// ---- Frame structure ----------------------------------------------------------
// Every line is two halves. Lines whose first half is NORMAL or ACTIVE start
// with a horizontal sync and carry colour burst; EQ and VS halves are
// equalising and broad (vertical sync) pulses.
enum Half : uint8_t { H_EQ, H_VS, H_BLANK, H_ACTIVE };

struct LineSpec {
    Half a, b;
    int row;  // picture row for active halves, -1 otherwise
};

static LineSpec line_spec(int L /* 1..525 */) {
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

// ---- State --------------------------------------------------------------------
static constexpr int MAX_BUFS = 96;
static constexpr int LINE_BYTES = SAMPLES_PER_LINE * 2;

struct Pool {
    uint32_t key[MAX_BUFS];
    uint16_t *buf[MAX_BUFS];
    int n;
};

static lldesc_t *s_ring;
static Pool s_pools[2];
static int s_live = 0;           // pool the DMA ring currently points into
static int s_pattern = 0;
static Levels s_levels;
static float s_ppm = 0;
static double s_sample_hz = SAMPLE_HZ;

// ---- Rendering ------------------------------------------------------------------
// Raised-cosine step: 0 below -w/2, 1 above +w/2.
static inline float step(float t, float w) {
    if (t <= -0.5f * w) return 0.0f;
    if (t >= 0.5f * w) return 1.0f;
    return 0.5f - 0.5f * cosf((float)M_PI * (t / w + 0.5f));
}
static inline float pulse(float t, float t0, float t1, float w) { return step(t - t0, w) - step(t - t1, w); }

struct ActiveSeg { float s0, s1, y0, y1, u, v; };

static void render_line(uint16_t *out, Half a, Half b, int phase_off,
                        const ActiveSeg *segs, int nseg) {
    const float H = HALF_LINE;
    const float scale = (s_levels.white_code - s_levels.sync_code) / (IRE_WHITE - IRE_SYNC);
    static const int SIN4[4] = {0, 1, 0, -1}, COS4[4] = {1, 0, -1, 0};

    for (int k = 0; k < SAMPLES_PER_LINE; k++) {
        const float t = (float)k;
        float ire = IRE_BLANK;

        // Sync: each half starts with a falling edge (except a BLANK/ACTIVE
        // second half). The next line always starts with one at t = 910.
        float low = 0;
        for (int h = 0; h < 2; h++) {
            Half kind = h ? b : a;
            float o = h * H;
            switch (kind) {
                case H_EQ:     low += pulse(t, o, o + EQ_WIDTH, SYNC_EDGE); break;
                case H_VS:     low += pulse(t, o, o + H - SERRATION_WIDTH, SYNC_EDGE); break;
                case H_BLANK:
                case H_ACTIVE: if (h == 0) low += pulse(t, 0, HSYNC_WIDTH, SYNC_EDGE); break;
            }
        }
        low += step(t - SAMPLES_PER_LINE, SYNC_EDGE);
        ire += (IRE_SYNC - IRE_BLANK) * low;

        const int ph = (k + phase_off) & 3;
        if (a == H_BLANK || a == H_ACTIVE) {  // colour burst on the -U axis
            float env = pulse(t, BURST_START, BURST_START + BURST_CYCLES * 4, BURST_EDGE);
            ire += -BURST_AMPL * env * SIN4[ph];
        }

        for (int i = 0; i < nseg; i++) {
            const ActiveSeg &s = segs[i];
            float wl = pulse(t, s.s0, s.s1, LUMA_EDGE);
            if (wl > 0) {
                float f = (t - s.s0) / (s.s1 - s.s0);
                f = f < 0 ? 0 : (f > 1 ? 1 : f);
                ire += wl * (s.y0 + (s.y1 - s.y0) * f);
            }
            if (s.u != 0 || s.v != 0) {
                float wc = pulse(t, s.s0, s.s1, CHROMA_EDGE);
                ire += wc * (s.u * SIN4[ph] + s.v * COS4[ph]);
            }
        }

        float code = s_levels.sync_code + (ire - IRE_SYNC) * scale;
        int c = (int)lroundf(code);
        c = c < 0 ? 0 : (c > 255 ? 255 : c);
        out[i2s_dac::index(k)] = i2s_dac::sample((uint8_t)c);
    }
}

// Active window (samples) for each half-line arrangement.
static void active_window(Half a, Half b, float &w0, float &w1) {
    w0 = ACTIVE_START; w1 = ACTIVE_END;
    if (a == H_ACTIVE && b != H_ACTIVE) w1 = HALF_LINE - FRONT_PORCH;   // line 263
    if (a != H_ACTIVE && b == H_ACTIVE) w0 = HALF_LINE + FRONT_PORCH;   // line 283
}

static uint16_t *get_line(Pool &p, const Pattern &pat, const LineSpec &ls, int phase_off) {
    bool has_active = ls.row >= 0;
    bool has_colour = ls.a == H_BLANK || ls.a == H_ACTIVE;
    int key_row = has_active ? pat.row_key(ls.row) : 0xFF;
    uint32_t key = (uint32_t)ls.a | ((uint32_t)ls.b << 2) |
                   ((uint32_t)(has_colour ? phase_off : 0) << 4) | ((uint32_t)key_row << 8);
    for (int i = 0; i < p.n; i++)
        if (p.key[i] == key) return p.buf[i];
    if (p.n >= MAX_BUFS) return nullptr;

    uint16_t *buf = (uint16_t *)heap_caps_malloc(LINE_BYTES, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    if (!buf) return nullptr;

    ActiveSeg segs[64];
    int nseg = 0;
    if (has_active) {
        Segment raw[64];
        int n = pat.segments(key_row, raw, 64);
        float w0, w1;
        active_window(ls.a, ls.b, w0, w1);
        for (int i = 0; i < n; i++) {
            float s0 = ACTIVE_START + raw[i].x0 * ACTIVE_WIDTH;
            float s1 = ACTIVE_START + raw[i].x1 * ACTIVE_WIDTH;
            float c0 = s0 < w0 ? w0 : s0, c1 = s1 > w1 ? w1 : s1;
            if (c1 <= c0) continue;
            // Keep a ramp's slope when clipping.
            float span = s1 - s0;
            float y0 = raw[i].y0 + (raw[i].y1 - raw[i].y0) * (c0 - s0) / span;
            float y1 = raw[i].y0 + (raw[i].y1 - raw[i].y0) * (c1 - s0) / span;
            segs[nseg++] = {c0, c1, y0, y1, raw[i].u, raw[i].v};
        }
    }
    render_line(buf, ls.a, ls.b, phase_off, segs, nseg);
    p.key[p.n] = key;
    p.buf[p.n] = buf;
    p.n++;
    return buf;
}

static void free_pool(Pool &p) {
    for (int i = 0; i < p.n; i++) heap_caps_free(p.buf[i]);
    p.n = 0;
}

// Render all lines for the current settings into the spare pool and point the
// ring at it. The ring keeps running; the switch lands within one frame.
static bool rebuild() {
    int spare = s_ring ? 1 - s_live : 0;
    Pool &p = s_pools[spare];
    free_pool(p);
    const Pattern &pat = PATTERNS[s_pattern];

    static uint16_t *bufs[CHAIN_LINES];
    for (int i = 0; i < CHAIN_LINES; i++) {
        // 910 samples/line = 227.5 cycles, so subcarrier phase advances 180
        // degrees per line; 1050 lines brings it back to zero.
        int phase_off = (2 * i) & 3;
        bufs[i] = get_line(p, pat, line_spec(i % LINES_PER_FRAME + 1), phase_off);
        if (!bufs[i]) { free_pool(p); return false; }
    }

    bool first = s_ring == nullptr;
    if (first) {
        s_ring = (lldesc_t *)heap_caps_calloc(CHAIN_LINES, sizeof(lldesc_t), MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
        if (!s_ring) { free_pool(p); return false; }
        for (int i = 0; i < CHAIN_LINES; i++) {
            lldesc_t &d = s_ring[i];
            d.size = LINE_BYTES;
            d.length = LINE_BYTES;
            d.owner = 1;
            d.eof = 0;
            d.empty = (uint32_t)&s_ring[(i + 1) % CHAIN_LINES];
        }
    }
    for (int i = 0; i < CHAIN_LINES; i++) s_ring[i].buf = (const uint8_t *)bufs[i];

    if (first) {
        s_live = spare;
        s_sample_hz = i2s_dac::set_sample_rate(SAMPLE_HZ * (1.0 + s_ppm * 1e-6));
        i2s_dac::start(s_ring);
    } else {
        // Let the DMA finish any line still being read from the old pool.
        vTaskDelay(pdMS_TO_TICKS(100));
        free_pool(s_pools[s_live]);
        s_live = spare;
    }
    return true;
}

bool begin(int pattern, Levels lv) {
    s_pattern = pattern;
    s_levels = lv;
    return rebuild();
}

bool set_pattern(int pattern) {
    if (pattern < 0 || pattern >= NUM_PATTERNS) return false;
    int old = s_pattern;
    s_pattern = pattern;
    if (!rebuild()) { s_pattern = old; return false; }
    return true;
}

bool set_levels(Levels lv) {
    Levels old = s_levels;
    s_levels = lv;
    if (!rebuild()) { s_levels = old; return false; }
    return true;
}

bool set_clock_ppm(float ppm) {
    s_ppm = ppm;
    s_sample_hz = i2s_dac::set_sample_rate(SAMPLE_HZ * (1.0 + ppm * 1e-6));
    return true;
}

int pattern() { return s_pattern; }
Levels levels() { return s_levels; }
float clock_ppm() { return s_ppm; }
double actual_sample_hz() { return s_sample_hz; }
int unique_lines() { return s_pools[s_live].n; }

int read_line(int index, uint8_t *out, int max) {
    if (!s_ring || index < 0 || index >= CHAIN_LINES) return 0;
    const uint16_t *b = (const uint16_t *)s_ring[index].buf;
    int n = s_ring[index].length / 2;
    if (n > max) n = max;
    for (int k = 0; k < n; k++) out[k] = b[i2s_dac::index(k)] >> 8;
    return n;
}

}  // namespace gen
