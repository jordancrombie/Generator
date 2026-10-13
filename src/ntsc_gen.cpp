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

struct Base { Half a, b; uint8_t phase; int16_t key; uint8_t mods; };

static lldesc_t *s_ring;                 // [2 * HALF_CAP]
static int s_count[2];                   // lines in each half
static uint16_t *s_safe;                 // parking line while the pool is rebuilt
static Base s_bases[MAX_BASES];
// Base per NTSC line, [standard / the test's alternate rendering (alt_mods)]
// [subcarrier phase 0 or 2 at line start].
// Every descriptor length is even, so lines only ever start on phase 0 or 2.
static uint8_t s_base_idx[LINES_PER_FRAME + 1][2][2];
static int s_nbases;
static uint16_t **s_pool;                // [base * nsub + v]
static uint16_t *s_marker[2][MAX_MARKERS];
static uint16_t *s_noise[perturb::NOISE_VARIANTS];  // snow lines (S9), only when the test uses them
static uint16_t *s_dropout[perturb::DROPOUT_VARIANTS][2];  // bar lines with a dropout (L1), [variant][phase]
static render::Style s_style;            // the test's static rendering style
static uint16_t *s_dead;                 // no signal (0 V), for S10/V1/X1
static int s_nsub = 1, s_ext = 0, s_cap = SAMPLES_PER_LINE;

static SemaphoreHandle_t s_lock;
static TaskHandle_t s_task;
static uint32_t s_written[2];            // frame number last written to each half
static volatile uint32_t s_max_write_us;
static volatile bool s_hold;
static volatile uint32_t s_dma_restarts;
static char s_restart_note[48];          // test and time of the last watchdog restart

static int s_pattern;
static Levels s_levels;
static float s_ppm;
static double s_sample_hz = SAMPLE_HZ;
static perturb::Test s_test;
static bool s_chroma_follows = false;    // default colour-under (VHS/Betamax)
static uint32_t s_start_frame;           // frame number the ring starts from after a rebuild

// ---- Placement ------------------------------------------------------------------
LinePlan plan(uint32_t n) {
    if (perturb::variant_mode(s_test) != perturb::VM_TIMING)  // copies differ in phase/noise/DC, not timing
        return {0, (uint8_t)perturb::line_variant(s_test, n, s_nsub), 0.0f};
    double s = perturb::shift_ns(s_test, n);
    // Steps of 2/nsub samples. One double multiply per line (no division:
    // the ESP32 emulates double math in software).
    const double steps_per_ns = s_nsub / (2.0 * SAMPLE_NS);
    int32_t q = s_test.def->kind == perturb::FH_UNLOCKED ? perturb::fh_unlocked_steps(s_test, n, s_nsub)
                                                         : (int32_t)llround(s * steps_per_ns);
    int32_t e2 = q >= 0 ? q / s_nsub : -((-q + s_nsub - 1) / s_nsub);
    return {2 * e2, (uint8_t)(q - e2 * s_nsub), (float)s};
}

float plan_shift_ns(const LinePlan &p) {
    if (perturb::variant_mode(s_test) != perturb::VM_TIMING) return 0.0f;
    return (float)((p.e + p.v * 2.0 / s_nsub) * SAMPLE_NS);
}

// What pre-rendered copy v of a line looks like, by the test's variant mode.
static inline bool timing_copies() { return perturb::variant_mode(s_test) == perturb::VM_TIMING; }
static inline float origin_of(int v) { return (float)LINE_ORIGIN + (timing_copies() ? v * 2.0f / s_nsub : 0.0f); }
static inline float carrier_of(int v) {
    switch (perturb::variant_mode(s_test)) {
        case perturb::VM_TIMING:  return s_chroma_follows ? v * 2.0f / s_nsub : 0.0f;
        case perturb::VM_CARRIER: return perturb::variant_value(s_test, v, s_nsub) / 90.0f;  // degrees -> samples
        default:                  return 0.0f;
    }
}
static render::Style style_of(int v, uint8_t mods) {
    render::Style st = s_style;
    st.mods = mods;
    if (perturb::variant_mode(s_test) == perturb::VM_DC) st.dc_ire = perturb::variant_value(s_test, v, s_nsub);
    if (perturb::variant_mode(s_test) == perturb::VM_NOISE) st.noise_seed = (uint32_t)perturb::variant_value(s_test, v, s_nsub);
    return st;
}

static void render_marker(uint16_t *buf, uint32_t frame, int phase, int v) {
    Segment raw[48];
    ActiveSeg segs[48];
    uint32_t w = marker::word(s_test.def->code, (uint8_t)s_test.rung, (uint16_t)frame);
    int n = marker::segments(w, raw, 48);
    int m = place_segments(raw, n, H_ACTIVE, H_ACTIVE, segs, 48);
    render_line(buf, s_cap, H_BLANK, H_BLANK, phase, origin_of(v), carrier_of(v), segs, m, s_levels, style_of(v, 0));
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
        // Lost lines (no signal / snow) win over everything, the marker included.
        if (slots[i].flags & perturb::SLOT_DEAD) {
            buf = s_dead;
        } else if (slots[i].flags & perturb::SLOT_NOISE) {
            buf = s_noise[perturb::noise_variant(frame, i)];
        } else if ((line == marker::LINE_F1 || line == marker::LINE_F2) && markers < MAX_MARKERS) {
            uint16_t *mb = s_marker[h][markers++];
            render_marker(mb, frame, phase, p[i].v);
            buf = mb;
        } else if (slots[i].flags & perturb::SLOT_DROPOUT) {
            buf = s_dropout[(slots[i].flags >> 4) & 3][phase >> 1];
        } else {
            int alt = (slots[i].flags & perturb::SLOT_ALT) ? 1 : 0;
            buf = s_pool[s_base_idx[line][alt][phase >> 1] * s_nsub + p[i].v];
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
        // A frame ends every ~33 ms. If none ends for 200 ms the DMA has
        // stopped (seen once, cause unknown): restart it and count it.
        if (xTaskNotifyWait(0, 0xFFFFFFFF, &bits, pdMS_TO_TICKS(200)) == pdFALSE) {
            xSemaphoreTake(s_lock, portMAX_DELAY);
            i2s_dac::restart(s_ring);
            s_dma_restarts++;
            snprintf(s_restart_note, sizeof(s_restart_note), "%s r%d at %lu ms", s_test.def->id, s_test.rung,
                     (unsigned long)(esp_timer_get_time() / 1000));
            xSemaphoreGive(s_lock);
            continue;
        }
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
    for (auto &nz : s_noise) { heap_caps_free(nz); nz = nullptr; }
    for (auto &var : s_dropout)
        for (auto &d : var) { heap_caps_free(d); d = nullptr; }
    heap_caps_free(s_dead);
    s_dead = nullptr;
}

static int find_base(const Base &b) {
    for (int i = 0; i < s_nbases; i++)
        if (s_bases[i].a == b.a && s_bases[i].b == b.b && s_bases[i].phase == b.phase && s_bases[i].key == b.key &&
            s_bases[i].mods == b.mods)
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
    s_style = perturb::base_style(s_test);
    s_nbases = 0;
    for (int L = 1; L <= LINES_PER_FRAME; L++) {
        LineSpec ls = line_spec(L);
        bool colour = ls.a == H_BLANK || ls.a == H_ACTIVE;
        int16_t key = (int16_t)(ls.row >= 0 ? pat.row_key(ls.row) : -1);
        uint8_t alt_mods = perturb::alt_mods(s_test, L);
        // An unserrated V-sync that continues from the previous line must
        // start low, not at blanking.
        if ((alt_mods & MOD_VS_UNSERRATED) && ls.a == H_VS && line_spec(L == 1 ? LINES_PER_FRAME : L - 1).b == H_VS)
            alt_mods |= MOD_LOW_START;
        for (int alt = 0; alt < 2; alt++) {
            if (alt && !alt_mods) {
                s_base_idx[L][1][0] = s_base_idx[L][0][0];
                s_base_idx[L][1][1] = s_base_idx[L][0][1];
                continue;
            }
            for (int ph = 0; ph < 2; ph++) {
                int i = find_base({ls.a, ls.b, (uint8_t)(colour ? 2 * ph : 0), key, (uint8_t)(alt ? alt_mods : 0)});
                if (i < 0) { xSemaphoreGive(s_lock); return false; }
                s_base_idx[L][alt][ph] = (uint8_t)i;
            }
        }
    }

    float d = perturb::max_delta_samples(s_test);
    s_ext = ((int)ceilf(d) + 6) & ~1;
    if (s_ext > MAX_EXT) s_ext = MAX_EXT;
    s_cap = SAMPLES_PER_LINE + s_ext;
    long budget = (long)heap_caps_get_free_size(MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL) - HEAP_RESERVE -
                  (2L * MAX_MARKERS + perturb::NOISE_VARIANTS + 2 * perturb::DROPOUT_VARIANTS + 1) * s_cap * 2;
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
            render_line(buf, s_cap, b.a, b.b, b.phase, origin_of(v), carrier_of(v), segs, nseg, s_levels, style_of(v, b.mods));
            s_pool[i * s_nsub + v] = buf;
        }
    }
    for (auto &half : s_marker)
        for (auto &m : half)
            if (ok && !(m = alloc_line())) ok = false;
    if (s_test.def->kind == perturb::DROPOUTS) {
        // A colour-bar line (line 50's content) with a stretch of snow over it.
        LineSpec ls = line_spec(50);
        Segment raw[64];
        ActiveSeg segs[64];
        int nseg = place_segments(raw, pat.segments(pat.row_key(ls.row), raw, 64), ls.a, ls.b, segs, 64);
        for (int j = 0; ok && j < perturb::DROPOUT_VARIANTS; j++)
            for (int ph = 0; ok && ph < 2; ph++) {
                uint16_t *d = s_dropout[j][ph] = alloc_line();
                if (!d) { ok = false; break; }
                render_line(d, s_cap, ls.a, ls.b, 2 * ph, origin_of(0), carrier_of(0), segs, nseg, s_levels, style_of(0, 0));
                float k0, k1;
                perturb::dropout_span(s_test, j, &k0, &k1);
                int a0 = (int)lroundf((float)LINE_ORIGIN + k0), a1 = (int)lroundf((float)LINE_ORIGIN + k1);
                overlay_snow(d, a0, a1 < s_cap ? a1 : s_cap, 100u + (uint32_t)j, s_levels);
            }
    }
    if (ok && perturb::uses_dead(s_test)) {
        if (!(s_dead = alloc_line())) ok = false;
        else for (int k = 0; k < s_cap; k++) s_dead[k] = i2s_dac::sample(0);  // DAC code 0: the lowest it goes
    }
    if (perturb::uses_snow(s_test))
        for (int j = 0; ok && j < perturb::NOISE_VARIANTS; j++) {
            if (!(s_noise[j] = alloc_line())) ok = false;
            else render_noise(s_noise[j], s_cap, (uint32_t)j + 1, s_levels);
        }
    if (!ok) { free_pool(); xSemaphoreGive(s_lock); return false; }

    s_max_write_us = 0;
    write_half(0, s_start_frame);
    write_half(1, s_start_frame + 1);
    xSemaphoreGive(s_lock);
    return true;
}

// Reprogram the APLL only when the rate actually changes: it clocks the DMA,
// and retuning it on every test change is the main suspect for the rare DMA
// stops the frame watchdog recovers from.
static void apply_clock() {
    static double current = 0;
    double want = SAMPLE_HZ * (1.0 + (s_ppm + perturb::clock_ppm(s_test)) * 1e-6);
    if (want == current) return;
    current = want;
    s_sample_hz = i2s_dac::set_sample_rate(want);
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
        // Every descriptor from line 525 on (unused until a frame grows
        // longer) also leads to the other half, so no path runs off the ring.
        d.eof = i >= LINES_PER_FRAME - 1;
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

bool set_test(const perturb::Test &t, uint32_t start_frame) {
    perturb::Test old = s_test;
    s_test = t;
    s_start_frame = start_frame;
    apply_clock();
    if (rebuild()) return true;
    s_test = old;
    s_start_frame = 0;
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
uint32_t dma_restarts() { return s_dma_restarts; }
const char *dma_restart_note() { return s_restart_note; }

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
