#include "ntsc_gen.h"

#include <math.h>
#include <string.h>
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "i2s_dac.h"
#include "marker.h"
#include "ntsc_timing.h"
#include "patterns.h"

using namespace ntsc;
using namespace render;

namespace gen {

// ---- State ----------------------------------------------------------------------
// The ring has two halves of up to HALF_CAP descriptors, one frame each (two
// frames = the 4-field colour sequence). Each half is rewritten by the frame
// task while the other plays, and may hold any number of lines, so frames
// can drop fields or change line counts. Lines point into a pool of
// pre-rendered "base" lines, each in `nsub` sub-sample shifted copies; the
// descriptor length sets where the next line's sync falls.
static constexpr int HALF_CAP = 600;
static constexpr int MAX_BASES = 64;
static constexpr int MAX_MARKERS = 4;    // marker lines per frame (240p shows each marker line twice)
static constexpr int HEAP_RESERVE = 48 * 1024;
static constexpr int SAFE_CAP = SAMPLES_PER_LINE + MAX_EXT;

struct Base { Half a, b; uint8_t phase; int16_t key; bool nosync; };

static lldesc_t *s_ring;                 // [2 * HALF_CAP]
static int s_count[2];                   // lines in each half
static uint16_t *s_safe;                 // parking line while the pool is rebuilt
static Base s_bases[MAX_BASES];
// Base per NTSC line, [no-sync copy][subcarrier phase 0 or 2 at line start].
// Every descriptor length is even, so lines only ever start on phase 0 or 2.
static uint8_t s_base_idx[LINES_PER_FRAME + 1][2][2];
static int s_nbases;
static uint16_t **s_pool;                // [base * nsub + v]
static uint16_t *s_marker[2][MAX_MARKERS];
static int s_nsub = 1, s_ext = 0, s_cap = SAMPLES_PER_LINE;

static SemaphoreHandle_t s_lock;
static TaskHandle_t s_task;
static uint32_t s_written[2];            // frame number last written to each half
static volatile uint32_t s_max_write_us;
static volatile bool s_hold;

static int s_pattern;
static Levels s_levels;
static float s_ppm;
static double s_sample_hz = SAMPLE_HZ;
static perturb::Test s_test;
static bool s_chroma_follows = false;    // default colour-under (VHS/Betamax)

// ---- Placement ------------------------------------------------------------------
LinePlan plan(uint32_t n) {
    float s = perturb::shift_ns(s_test, n);
    float step = 2.0f / s_nsub;
    int32_t q = (int32_t)lroundf(s / (float)SAMPLE_NS / step);
    int32_t e2 = q >= 0 ? q / s_nsub : -((-q + s_nsub - 1) / s_nsub);
    return {2 * e2, (uint8_t)(q - e2 * s_nsub), s};
}

float plan_shift_ns(const LinePlan &p) {
    return (float)((p.e + p.v * 2.0 / s_nsub) * SAMPLE_NS);
}

static inline float origin_of(int v) { return (float)LINE_ORIGIN + v * 2.0f / s_nsub; }
static inline float carrier_of(int v) { return s_chroma_follows ? v * 2.0f / s_nsub : 0.0f; }

static void render_marker(uint16_t *buf, uint32_t frame, int phase, int v) {
    Segment raw[48];
    ActiveSeg segs[48];
    uint32_t w = marker::word(s_test.def->code, (uint8_t)s_test.rung, (uint16_t)frame);
    int n = marker::segments(w, raw, 48);
    int m = place_segments(raw, n, H_ACTIVE, H_ACTIVE, segs, 48);
    render_line(buf, s_cap, H_BLANK, H_BLANK, phase, origin_of(v), carrier_of(v), segs, m, s_levels);
}

// Write frame number `frame` into half `h` of the ring.
static void write_half(int h, uint32_t frame) {
    int64_t t0 = esp_timer_get_time();
    static perturb::Slot slots[HALF_CAP];
    static LinePlan p[HALF_CAP + 1];
    int ns = perturb::frame_slots(s_test, frame, slots, HALF_CAP);
    for (int i = 0; i < ns; i++) p[i] = plan(frame * LINES_PER_FRAME + i);
    p[ns] = plan((frame + 1) * LINES_PER_FRAME);  // first line of the next frame

    lldesc_t *d = &s_ring[h * HALF_CAP];
    uint32_t cum = perturb::frame_start_mod4(s_test, frame);
    int markers = 0;
    for (int i = 0; i < ns; i++) {
        int nominal = (slots[i].flags & perturb::SLOT_HALF) ? perturb::HALF_SAMPLES : SAMPLES_PER_LINE;
        int len = nominal + p[i + 1].e - p[i].e;
        len = len < 64 ? 64 : (len > s_cap ? s_cap : len);
        // Subcarrier phase at the line's first sample. Colour-under keeps the
        // carrier continuous through timing shifts; direct colour moves it.
        int phase = (int)(cum + (s_chroma_follows ? 0 : p[i].e)) & 3;
        int line = slots[i].line;
        const uint16_t *buf;
        if ((line == marker::LINE_F1 || line == marker::LINE_F2) && markers < MAX_MARKERS) {
            uint16_t *mb = s_marker[h][markers++];
            render_marker(mb, frame, phase, p[i].v);
            buf = mb;
        } else {
            int nosync = (slots[i].flags & perturb::SLOT_NOSYNC) ? 1 : 0;
            buf = s_pool[s_base_idx[line][nosync][phase >> 1] * s_nsub + p[i].v];
        }
        d[i].size = s_cap * 2;
        d[i].length = len * 2;
        d[i].buf = (const uint8_t *)buf;
        bool last = i == ns - 1;
        d[i].eof = last;
        d[i].empty = (uint32_t)(last ? &s_ring[(1 - h) * HALF_CAP] : &d[i + 1]);
        cum = (cum + nominal) & 3;
    }
    s_count[h] = ns;
    s_written[h] = frame;
    uint32_t us = (uint32_t)(esp_timer_get_time() - t0);
    if (us > s_max_write_us) s_max_write_us = us;
}

static void IRAM_ATTR on_eof(uint32_t desc) {
    uint32_t bit = desc >= (uint32_t)&s_ring[HALF_CAP] ? 2 : 1;
    BaseType_t woken = pdFALSE;
    xTaskNotifyFromISR(s_task, bit, eSetBits, &woken);
    portYIELD_FROM_ISR(woken);
}

static void frame_task(void *) {
    for (;;) {
        uint32_t bits = 0;
        xTaskNotifyWait(0, 0xFFFFFFFF, &bits, portMAX_DELAY);
        if (s_hold) continue;
        xSemaphoreTake(s_lock, portMAX_DELAY);
        // When half h finishes, the DMA moves on to the other half; h plays
        // next after that, so it gets the frame after the other half's.
        // (Not "this half's frame + 2": after a rebuild the first interrupt
        // can come from either half, which would swap frames in pairs.)
        for (int h = 0; h < 2; h++)
            if (bits & (1u << h)) write_half(h, s_written[1 - h] + 1);
        xSemaphoreGive(s_lock);
    }
}

// ---- Pool ------------------------------------------------------------------------
static void free_pool() {
    if (s_pool) {
        for (int i = 0; i < s_nbases * s_nsub; i++) heap_caps_free(s_pool[i]);
        free(s_pool);
        s_pool = nullptr;
    }
    for (auto &half : s_marker)
        for (auto &m : half) { heap_caps_free(m); m = nullptr; }
}

static int find_base(const Base &b) {
    for (int i = 0; i < s_nbases; i++)
        if (s_bases[i].a == b.a && s_bases[i].b == b.b && s_bases[i].phase == b.phase && s_bases[i].key == b.key &&
            s_bases[i].nosync == b.nosync)
            return i;
    if (s_nbases >= MAX_BASES) return -1;
    s_bases[s_nbases] = b;
    return s_nbases++;
}

static uint16_t *alloc_line() {
    return (uint16_t *)heap_caps_malloc(s_cap * 2, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
}

// Rebuild everything for the current pattern/levels/test. The ring is parked
// on the safe line meanwhile, so the output blanks for a moment.
static bool rebuild() {
    xSemaphoreTake(s_lock, portMAX_DELAY);
    for (int c = 0; c < 2 * HALF_CAP; c++) {  // links stay as they are
        s_ring[c].size = SAFE_CAP * 2;
        s_ring[c].length = SAMPLES_PER_LINE * 2;
        s_ring[c].buf = (const uint8_t *)s_safe;
    }
    vTaskDelay(pdMS_TO_TICKS(80));  // let the DMA leave the old buffers
    free_pool();

    const Pattern &pat = PATTERNS[s_pattern];
    s_nbases = 0;
    for (int L = 1; L <= LINES_PER_FRAME; L++) {
        LineSpec ls = line_spec(L);
        bool colour = ls.a == H_BLANK || ls.a == H_ACTIVE;
        int16_t key = (int16_t)(ls.row >= 0 ? pat.row_key(ls.row) : -1);
        for (int ns = 0; ns < 2; ns++) {
            if (ns && !perturb::needs_nosync(s_test, L)) {
                s_base_idx[L][1][0] = s_base_idx[L][0][0];
                s_base_idx[L][1][1] = s_base_idx[L][0][1];
                continue;
            }
            for (int ph = 0; ph < 2; ph++) {
                int i = find_base({ls.a, ls.b, (uint8_t)(colour ? 2 * ph : 0), key, ns == 1});
                if (i < 0) { xSemaphoreGive(s_lock); return false; }
                s_base_idx[L][ns][ph] = (uint8_t)i;
            }
        }
    }

    float d = perturb::max_delta_samples(s_test);
    s_ext = ((int)ceilf(d) + 6) & ~1;
    if (s_ext > MAX_EXT) s_ext = MAX_EXT;
    s_cap = SAMPLES_PER_LINE + s_ext;
    long budget = (long)heap_caps_get_free_size(MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL) - HEAP_RESERVE -
                  2L * MAX_MARKERS * s_cap * 2;
    s_nsub = 8;
    while (s_nsub > 1 && (long)s_nbases * s_nsub * s_cap * 2 > budget) s_nsub /= 2;

    s_pool = (uint16_t **)calloc(s_nbases * s_nsub, sizeof(uint16_t *));
    bool ok = s_pool != nullptr;
    for (int i = 0; ok && i < s_nbases; i++) {
        const Base &b = s_bases[i];
        ActiveSeg segs[64];
        int nseg = 0;
        if (b.key >= 0) {
            Segment raw[64];
            int n = pat.segments(b.key, raw, 64);
            nseg = place_segments(raw, n, b.a, b.b, segs, 64);
        }
        for (int v = 0; v < s_nsub; v++) {
            uint16_t *buf = alloc_line();
            if (!buf) { ok = false; break; }
            render_line(buf, s_cap, b.a, b.b, b.phase, origin_of(v), carrier_of(v), segs, nseg, s_levels, b.nosync);
            s_pool[i * s_nsub + v] = buf;
        }
    }
    for (auto &half : s_marker)
        for (auto &m : half)
            if (ok && !(m = alloc_line())) ok = false;
    if (!ok) { free_pool(); xSemaphoreGive(s_lock); return false; }

    s_max_write_us = 0;
    write_half(0, 0);
    write_half(1, 1);
    xSemaphoreGive(s_lock);
    return true;
}

static void apply_clock() {
    s_sample_hz = i2s_dac::set_sample_rate(SAMPLE_HZ * (1.0 + (s_ppm + perturb::clock_ppm(s_test)) * 1e-6));
}

// ---- API --------------------------------------------------------------------------
bool begin(int pattern, Levels lv) {
    s_pattern = pattern;
    s_levels = lv;
    perturb::make("V0", 1, 0, &s_test);

    s_lock = xSemaphoreCreateMutex();
    s_ring = (lldesc_t *)heap_caps_calloc(2 * HALF_CAP, sizeof(lldesc_t), MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    s_safe = (uint16_t *)heap_caps_malloc(SAFE_CAP * 2, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    if (!s_lock || !s_ring || !s_safe) return false;
    render_line(s_safe, SAFE_CAP, H_BLANK, H_BLANK, 0, (float)LINE_ORIGIN, 0, nullptr, 0, s_levels);
    // Start as two standard 525-line halves on the safe line.
    for (int c = 0; c < 2 * HALF_CAP; c++) {
        lldesc_t &d = s_ring[c];
        int h = c / HALF_CAP, i = c % HALF_CAP;
        d.size = SAFE_CAP * 2;
        d.length = SAMPLES_PER_LINE * 2;
        d.buf = (const uint8_t *)s_safe;
        d.owner = 1;
        d.eof = i == LINES_PER_FRAME - 1;
        d.empty = (uint32_t)(d.eof ? &s_ring[(1 - h) * HALF_CAP] : &s_ring[c + 1]);
    }
    apply_clock();
    i2s_dac::start(s_ring);

    if (xTaskCreatePinnedToCore(frame_task, "ntsc_frames", 4096, nullptr, 10, &s_task, 0) != pdPASS) return false;
    i2s_dac::on_eof(on_eof);
    return rebuild();
}

bool set_pattern(int pattern) {
    if (pattern < 0 || pattern >= NUM_PATTERNS) return false;
    int old = s_pattern;
    s_pattern = pattern;
    if (rebuild()) return true;
    s_pattern = old;
    rebuild();
    return false;
}

bool set_levels(Levels lv) {
    Levels old = s_levels;
    s_levels = lv;
    if (rebuild()) return true;
    s_levels = old;
    rebuild();
    return false;
}

bool set_clock_ppm(float ppm) {
    s_ppm = ppm;
    apply_clock();
    return true;
}

bool set_test(const perturb::Test &t) {
    perturb::Test old = s_test;
    s_test = t;
    apply_clock();
    if (rebuild()) return true;
    s_test = old;
    apply_clock();
    rebuild();
    return false;
}

bool set_chroma_follows(bool follows) {
    bool old = s_chroma_follows;
    s_chroma_follows = follows;
    if (rebuild()) return true;
    s_chroma_follows = old;
    rebuild();
    return false;
}

bool chroma_follows() { return s_chroma_follows; }
int pattern() { return s_pattern; }
Levels levels() { return s_levels; }
float clock_ppm() { return s_ppm; }
double actual_sample_hz() { return s_sample_hz; }
const perturb::Test &test() { return s_test; }
int unique_lines() { return s_nbases; }
int subsample_steps() { return s_nsub; }
int ext_samples() { return s_ext; }
uint32_t frames() { return s_written[0] > s_written[1] ? s_written[0] : s_written[1]; }
uint32_t max_write_us() { return s_max_write_us; }

void hold(bool on) { s_hold = on; }
uint32_t frame_in_half(int h) { return s_written[h & 1]; }

int line_length(int index) {
    return (s_ring && index >= 0 && index < 2 * HALF_CAP) ? s_ring[index].length / 2 : 0;
}

int half_lines(int h) { return s_count[h & 1]; }
int half_base(int h) { return (h & 1) * HALF_CAP; }

int read_line(int index, uint8_t *out, int max) {
    if (!s_ring || index < 0 || index >= 2 * HALF_CAP) return 0;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    const uint16_t *b = (const uint16_t *)s_ring[index].buf;
    int n = s_ring[index].length / 2;
    if (n > max) n = max;
    for (int k = 0; k < n; k++) out[k] = b[i2s_dac::index(k)] >> 8;
    xSemaphoreGive(s_lock);
    return n;
}

}  // namespace gen
