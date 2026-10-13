#include "manifest.h"

#include "marker.h"
#include "ntsc_gen.h"
#include "ntsc_timing.h"
#include "patterns.h"

using namespace ntsc;

static void print_pattern(Print &o) {
    const Pattern &pat = PATTERNS[gen::pattern()];
    o.printf("\"pattern\":{\"name\":\"%s\",\"regions\":[", pat.name);
    int start = 0;
    bool first = true;
    for (int row = 1; row <= ACTIVE_ROWS; row++) {
        if (row < ACTIVE_ROWS && pat.row_key(row) == pat.row_key(start)) continue;
        int key = pat.row_key(start);
        Segment s[64];
        int n = pat.segments(key, s, 64);
        o.printf("%s{\"rows\":[%d,%d],\"key\":%d,\"segments\":[", first ? "" : ",", start, row - 1, key);
        for (int i = 0; i < n; i++)
            o.printf("%s{\"x0\":%.6f,\"x1\":%.6f,\"y0\":%.3f,\"y1\":%.3f,\"u\":%.3f,\"v\":%.3f}", i ? "," : "",
                     s[i].x0, s[i].x1, s[i].y0, s[i].y1, s[i].u, s[i].v);
        o.print("]}");
        first = false;
        start = row;
    }
    o.print("]}");
}

void print_manifest(Print &o) {
    const perturb::Test &t = gen::test();
    gen::Levels lv = gen::levels();
    o.printf("{\"schema\":\"tbc-test/1\",\"generator\":{\"firmware\":\"%s\",\"sample_hz\":%.3f,\"nominal_sample_hz\":%.3f,"
             "\"clock_ppm_base\":%.3f,\"levels\":{\"sync_code\":%.2f,\"white_code\":%.2f},"
             "\"subsample_steps\":%d,\"max_ext_samples\":%d},",
             FIRMWARE_VERSION, gen::actual_sample_hz(), SAMPLE_HZ, gen::clock_ppm(), lv.sync_code, lv.white_code,
             gen::subsample_steps(), gen::ext_samples());
    o.printf("\"test\":{\"id\":\"%s\",\"code\":%d,\"name\":\"%s\",\"rung\":%d,\"rungs\":%d,\"params\":{",
             t.def->id, t.def->code, t.def->name, t.rung, t.def->rungs);
    bool any = false;
    if (*t.def->p1_name) { o.printf("\"%s\":%g", t.def->p1_name, t.p1); any = true; }
    if (*t.def->p2_name) o.printf("%s\"%s\":%g", any ? "," : "", t.def->p2_name, t.p2);
    o.printf("},\"seed\":%u,\"model\":\"perturb/1\",\"chroma\":\"%s\"},", (unsigned)t.seed,
             gen::chroma_follows() ? "follows_timing" : "colour_under");
    o.printf("\"timing\":{\"samples_per_line\":%d,\"lines_per_frame\":%d,\"half_line_samples\":%d,"
             "\"loop\":\"none (frame counter)\",\"time_origin\":\"50%% of H-sync leading edge\","
             "\"structure\":\"perturb/1 frame_slots\"},",
             SAMPLES_PER_LINE, LINES_PER_FRAME, perturb::HALF_SAMPLES);
    o.printf("\"marker\":{\"lines\":[%d,%d],\"cells\":%d,\"cell_us\":%.2f,\"start_us\":%.2f,\"lo_ire\":%.1f,\"hi_ire\":%.1f,"
             "\"layout\":\"start 10, code 8, rung 4, frame 16, crc4 (poly 0x3 init 0, 28 bits MSB first), stop 01\"},",
             marker::LINE_F1, marker::LINE_F2, marker::CELLS, marker::CELL_US, marker::START_US, marker::LO_IRE,
             marker::HI_IRE);
    print_pattern(o);
    o.print(",\"ideal\":{\"timing\":\"nominal\",\"pattern\":\"unperturbed\"}}\n");
}
