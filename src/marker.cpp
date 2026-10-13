#include "marker.h"
#include "ntsc_timing.h"

namespace marker {

uint8_t crc4(uint32_t data28) {
    uint8_t crc = 0;
    for (int i = 27; i >= 0; i--) {
        uint8_t bit = ((data28 >> i) & 1) ^ ((crc >> 3) & 1);
        crc = (uint8_t)((crc << 1) & 0xF);
        if (bit) crc ^= 0x3;
    }
    return crc;
}

uint32_t word(uint8_t test_code, uint8_t rung, uint16_t frame) {
    uint32_t d = ((uint32_t)test_code << 20) | ((uint32_t)(rung & 0xF) << 16) | frame;
    return (d << 4) | crc4(d);
}

int segments(uint32_t w, Segment *out, int max) {
    // Active picture starts 9.4 us after sync; segments are fractions of its width.
    const float us = (float)ntsc::US;
    const float a0 = (float)ntsc::ACTIVE_START, aw = (float)ntsc::ACTIVE_WIDTH;
    auto x = [&](float t_us) { return (t_us * us - a0) / aw; };
    int bits[CELLS];
    int n = 0;
    bits[n++] = 1; bits[n++] = 0;
    for (int i = 31; i >= 0; i--) bits[n++] = (w >> i) & 1;
    bits[n++] = 0; bits[n++] = 1;

    int m = 0;
    if (m < max) out[m++] = {0, x(START_US), LO_IRE, LO_IRE, 0, 0};
    for (int i = 0; i < CELLS && m < max; i++) {
        float ire = bits[i] ? HI_IRE : LO_IRE;
        float x0 = x(START_US + i * CELL_US), x1 = x(START_US + (i + 1) * CELL_US);
        // Merge runs of equal cells so edges only appear at transitions.
        if (m > 0 && out[m - 1].y0 == ire) out[m - 1].x1 = x1;
        else out[m++] = {x0, x1, ire, ire, 0, 0};
    }
    if (m < max) {
        if (out[m - 1].y0 == LO_IRE) out[m - 1].x1 = 1.0f;
        else out[m++] = {x(START_US + CELLS * CELL_US), 1.0f, LO_IRE, LO_IRE, 0, 0};
    }
    return m;
}

}  // namespace marker
