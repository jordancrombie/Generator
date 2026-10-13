#pragma once
// Test patterns, described as horizontal segments of the active line.
// A pattern maps each picture row (0..485, top to bottom, interlaced order)
// to a "row key"; rows sharing a key are identical, so only one line buffer
// per (key, subcarrier phase) is rendered.

#include <stdint.h>

struct Segment {
    float x0, x1;   // fraction of active width, 0..1
    float y0, y1;   // luma in IRE at x0 and x1 (linear ramp; equal for flat)
    float u, v;     // chroma in IRE: c = u*sin(wt) + v*cos(wt)
};

struct Pattern {
    const char *name;
    int (*row_key)(int row);
    // Fill `out` with segments for this key, return count (<= max).
    int (*segments)(int key, Segment *out, int max);
};

extern const Pattern PATTERNS[];
extern const int NUM_PATTERNS;

// Parameter shared by the flat-field pattern (IRE).
extern float g_flat_ire;
