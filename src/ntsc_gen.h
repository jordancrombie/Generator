#pragma once
#include <stdint.h>

namespace gen {

struct Levels {
    float sync_code;   // DAC code for -40 IRE
    float white_code;  // DAC code for 100 IRE
};

bool begin(int pattern, Levels lv);
bool set_pattern(int pattern);
bool set_levels(Levels lv);
bool set_clock_ppm(float ppm);   // trim sample clock (and so fsc/line rate)

int    pattern();
Levels levels();
float  clock_ppm();
double actual_sample_hz();
int    unique_lines();          // line buffers in use for the current pattern
// DAC codes of chain line `index` (0..1049) in playback order; returns count.
int    read_line(int index, uint8_t *out, int max);

}  // namespace gen
