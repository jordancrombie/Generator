#pragma once
// I2S0 in LCD/DAC mode streaming a DMA descriptor ring to the internal 8-bit
// DAC on GPIO25, clocked from the APLL at 4 x fsc.
#include <stdint.h>
#include "esp_rom_lldesc.h"

namespace i2s_dac {

void start(lldesc_t *ring);
// Set the APLL so the DAC sample rate is `sample_hz`. Returns actual rate.
double set_sample_rate(double sample_hz);

// Diagnostics: current DMA descriptor address; force a constant DAC code
// (code < 0 returns the DAC to the DMA stream).
uint32_t current_descriptor();
void force_dc(int code);

// DAC consumes 16-bit samples (value in the high byte) and plays each pair
// of 16-bit words swapped, so sample k lives at index k ^ 1.
static inline uint16_t sample(uint8_t code) { return (uint16_t)code << 8; }
static inline int index(int k) { return k ^ 1; }

}  // namespace i2s_dac
