#include "patterns.h"
#include "ntsc_timing.h"
#include <math.h>
#include <string.h>

using namespace ntsc;

float g_flat_ire = 50.0f;

// RGB (0..1, gamma-corrected) -> luma/chroma in IRE, 7.5 IRE setup.
static Segment rgb(float x0, float x1, float r, float g, float b) {
    float y = 0.299f * r + 0.587f * g + 0.114f * b;
    float u = 0.492f * (b - y);
    float v = 0.877f * (r - y);
    float Y = IRE_SETUP + RGB_SCALE * y;
    return {x0, x1, Y, Y, (float)(RGB_SCALE * u), (float)(RGB_SCALE * v)};
}

// Chroma on the I/Q axes (amplitude in IRE), with setup-level luma.
static Segment iq(float x0, float x1, float i, float q) {
    const float s = sinf(33.0f * (float)M_PI / 180.0f), c = cosf(33.0f * (float)M_PI / 180.0f);
    float v = i * c + q * s;
    float u = -i * s + q * c;
    return {x0, x1, (float)IRE_SETUP, (float)IRE_SETUP, u, v};
}

static Segment luma(float x0, float x1, float ire) { return {x0, x1, ire, ire, 0, 0}; }

static const float BARS75[7][3] = {
    {.75f, .75f, .75f}, {.75f, .75f, 0}, {0, .75f, .75f}, {0, .75f, 0},
    {.75f, 0, .75f},    {.75f, 0, 0},    {0, 0, .75f},
};

// ---- SMPTE EG-1 colour bars -------------------------------------------------
static int smpte_key(int row) {
    if (row < ACTIVE_ROWS * 2 / 3) return 0;
    if (row < ACTIVE_ROWS * 3 / 4) return 1;
    return 2;
}
static int smpte_segs(int key, Segment *o, int) {
    const float w = 1.0f / 7.0f;
    if (key == 0) {
        for (int i = 0; i < 7; i++) o[i] = rgb(i * w, (i + 1) * w, BARS75[i][0], BARS75[i][1], BARS75[i][2]);
        return 7;
    }
    if (key == 1) {  // reverse-order castellations: blue, blk, magenta, blk, cyan, blk, gray
        static const int idx[7] = {6, -1, 4, -1, 2, -1, 0};
        for (int i = 0; i < 7; i++) {
            if (idx[i] < 0) o[i] = rgb(i * w, (i + 1) * w, 0, 0, 0);
            else o[i] = rgb(i * w, (i + 1) * w, BARS75[idx[i]][0], BARS75[idx[i]][1], BARS75[idx[i]][2]);
        }
        return 7;
    }
    // -I, 100% white, +Q, black (each 5/4 bar), PLUGE (3 x 1/3 bar), black
    float x = 0, b = 1.25f * w, p = w / 3.0f;
    int n = 0;
    o[n++] = iq(x, x + b, -20, 0);              x += b;
    o[n++] = luma(x, x + b, 100);               x += b;
    o[n++] = iq(x, x + b, 0, 20);               x += b;
    o[n++] = luma(x, x + b, IRE_SETUP);         x += b;
    o[n++] = luma(x, x + p, IRE_SETUP - 4);     x += p;
    o[n++] = luma(x, x + p, IRE_SETUP);         x += p;
    o[n++] = luma(x, x + p, IRE_SETUP + 4);     x += p;
    o[n++] = luma(x, 1.0f, IRE_SETUP);
    return n;
}

// ---- Full-field 75% EIA bars (with black) -----------------------------------
static int one_key(int) { return 0; }
static int eia_segs(int, Segment *o, int) {
    const float w = 1.0f / 8.0f;
    for (int i = 0; i < 7; i++) o[i] = rgb(i * w, (i + 1) * w, BARS75[i][0], BARS75[i][1], BARS75[i][2]);
    o[7] = rgb(7 * w, 1.0f, 0, 0, 0);
    return 8;
}

// ---- 10-step luma staircase (setup .. 100 IRE) ------------------------------
static int stair_segs(int, Segment *o, int) {
    const int steps = 10;
    for (int i = 0; i < steps; i++) {
        float ire = IRE_SETUP + (IRE_WHITE - IRE_SETUP) * i / (steps - 1);
        o[i] = luma((float)i / steps, (float)(i + 1) / steps, ire);
    }
    return steps;
}

// ---- Flat field at g_flat_ire ------------------------------------------------
static int flat_segs(int, Segment *o, int) { o[0] = luma(0, 1, g_flat_ire); return 1; }

// ---- Crosshatch: 16 x 12 white grid on black --------------------------------
static const int XH_COLS = 16, XH_ROWS = 12;
static int xhatch_key(int row) {
    // Horizontal grid lines 2 rows (one per field) thick, incl. top & bottom edge.
    int pitch = ACTIVE_ROWS / XH_ROWS;
    int r = row % pitch;
    return (r < 2 || row >= ACTIVE_ROWS - 2) ? 1 : 0;
}
static int xhatch_segs(int key, Segment *o, int max) {
    if (key == 1) { o[0] = luma(0, 1, IRE_WHITE); return 1; }
    const float lw = 0.004f;  // ~3 samples wide
    int n = 0;
    float x = 0;
    for (int c = 0; c <= XH_COLS && n + 2 <= max; c++) {
        float cx = (float)c / XH_COLS;
        float l0 = cx - lw / 2, l1 = cx + lw / 2;
        if (l0 < 0) { l0 = 0; l1 = lw; }
        if (l1 > 1) { l1 = 1; l0 = 1 - lw; }
        if (l0 > x) o[n++] = luma(x, l0, IRE_SETUP);
        o[n++] = luma(l0, l1, IRE_WHITE);
        x = l1;
    }
    return n;
}

// ---- Black (sync, burst and setup only) --------------------------------------
static int black_segs(int, Segment *o, int) { o[0] = luma(0, 1, IRE_SETUP); return 1; }

// ---- Title card: not a test pattern ----------------------------------------
// The TBT title, for photos and posts. Only shown on request (`p title`, or the
// host tools' --title flag before a field trial); never used by a test.
// Text is a 5x7 block font scaled to square pixels on the 4:3 picture.
struct Glyph { char c; uint8_t rows[7]; };   // 5 bits per row, MSB = leftmost
static const Glyph FONT[] = {                // just the letters the title uses
    {'A', {0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11}}, {'B', {0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E}},
    {'C', {0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E}}, {'D', {0x1E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1E}},
    {'E', {0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F}}, {'H', {0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11}},
    {'I', {0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E}}, {'J', {0x07, 0x02, 0x02, 0x02, 0x02, 0x12, 0x0C}},
    {'M', {0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11}}, {'O', {0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E}},
    {'P', {0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10}}, {'R', {0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11}},
    {'S', {0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E}}, {'T', {0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04}},
    {'U', {0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E}}, {'V', {0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04}},
};
static uint8_t glyph_row(char c, int r) {
    for (const Glyph &g : FONT)
        if (g.c == c) return g.rows[r];
    return 0;  // space, or a letter the font doesn't have
}

struct TitleLine { const char *text; int top; int px; float ire; };  // px: picture rows per font pixel
static const TitleLine TITLE[] = {
    {"TBT", 56, 24, 100.0f},
    {"TIME BASE TORTURE", 256, 5, 80.0f},
    {"VIDEO ARCHIVE PROJECT", 322, 4, 60.0f},
};
static const int NUM_TITLE = sizeof(TITLE) / sizeof(TITLE[0]);
static const int TITLE_BARS_TOP = 400, TITLE_BARS_ROWS = 24;  // a slim strip of colour bars

// Keys: 0 black, 1 the bar strip, 2 + 8 * line + font row for text.
static int title_key(int row) {
    for (int i = 0; i < NUM_TITLE; i++) {
        int r = row - TITLE[i].top;
        if (r >= 0 && r < 7 * TITLE[i].px) return 2 + 8 * i + r / TITLE[i].px;
    }
    if (row >= TITLE_BARS_TOP && row < TITLE_BARS_TOP + TITLE_BARS_ROWS) return 1;
    return 0;
}
static int title_segs(int key, Segment *o, int max) {
    if (key == 1) {
        const float x0 = 0.15f, w = 0.7f / 7.0f;
        int n = 0;
        o[n++] = luma(0, x0, IRE_SETUP);
        for (int i = 0; i < 7; i++) o[n++] = rgb(x0 + i * w, x0 + (i + 1) * w, BARS75[i][0], BARS75[i][1], BARS75[i][2]);
        o[n++] = luma(x0 + 7 * w, 1, IRE_SETUP);
        return n;
    }
    o[0] = luma(0, 1, IRE_SETUP);  // black background; text runs add to it
    if (key < 2) return 1;
    const TitleLine &L = TITLE[(key - 2) / 8];
    const int r = (key - 2) % 8;
    const float pw = L.px * 0.75f / ACTIVE_ROWS;  // a square font pixel, as a fraction of the width
    const int len = (int)strlen(L.text), cols = 6 * len - 1;
    const float left = 0.5f - 0.5f * cols * pw;
    int n = 1, run = -1;
    for (int c = 0; c <= cols && n < max; c++) {  // one segment per run of lit pixels
        bool lit = c < cols && (c % 6) < 5 && (glyph_row(L.text[c / 6], r) >> (4 - c % 6) & 1);
        if (lit && run < 0) run = c;
        if (!lit && run >= 0) {
            o[n++] = luma(left + run * pw, left + c * pw, L.ire - (float)IRE_SETUP);
            run = -1;
        }
    }
    return n;
}

const Pattern PATTERNS[] = {
    {"smpte_bars", smpte_key, smpte_segs},
    {"eia_bars_75", one_key, eia_segs},
    {"staircase", one_key, stair_segs},
    {"flat", one_key, flat_segs},
    {"crosshatch", xhatch_key, xhatch_segs},
    {"black", one_key, black_segs},
    {"title", title_key, title_segs},
};
const int NUM_PATTERNS = sizeof(PATTERNS) / sizeof(PATTERNS[0]);
