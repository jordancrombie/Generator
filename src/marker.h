#pragma once
// Frame marker on lines 24 and 287: the picture's top rows (normally under
// overscan). Not in the vertical interval, which TBCs commonly blank, and
// ahead of every line the structure tests add, remove or skew, so it keeps
// its place in the field. 36 cells of 1.4 us from 10.5 us after
// sync. Cells: start "10", then 32 bits MSB first -- test code (8), rung (4),
// frame counter (16), CRC-4 (4) -- then stop "01". 0 = 7.5 IRE, 1 = 70 IRE.
// CRC-4: poly x^4 + x + 1 (0x3), init 0, over the 28 data bits MSB first.

#include <stdint.h>
#include "patterns.h"

namespace marker {

constexpr int LINE_F1 = 24, LINE_F2 = 287;
constexpr float CELL_US = 1.4f, START_US = 10.5f, LO_IRE = 7.5f, HI_IRE = 70.0f;
constexpr int CELLS = 36;

uint32_t word(uint8_t test_code, uint8_t rung, uint16_t frame);
uint8_t crc4(uint32_t data28);
// Fill pattern segments (fractions of active width) for this word.
int segments(uint32_t word, Segment *out, int max);

}  // namespace marker
