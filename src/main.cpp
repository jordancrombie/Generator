// NTSC composite test-signal generator for TBC testing.
// Output: GPIO25 (DAC1). Control: serial 921600, line-based commands ("help").
#include <Arduino.h>
#include "driver/rtc_io.h"

#include "i2s_dac.h"
#include "manifest.h"
#include "ntsc_gen.h"
#include "ntsc_timing.h"
#include "patterns.h"

// Default levels assume GPIO25 drives the 75-ohm monitor input directly:
// ~0.95 V for sync tip..white unloaded. Recalibrate with "levels" once the
// output has been measured on the scope.
static const gen::Levels DEFAULT_LEVELS = {0.0f, 73.0f};

static void print_info() {
    gen::Levels lv = gen::levels();
    Serial.printf("{\"standard\":\"NTSC-M\",\"pattern\":\"%s\",\"pattern_index\":%d,"
                  "\"sync_code\":%.1f,\"white_code\":%.1f,\"flat_ire\":%.1f,"
                  "\"clock_ppm\":%.2f,\"sample_hz\":%.3f,\"fsc_hz\":%.3f,\"line_hz\":%.4f,"
                  "\"test\":\"%s\",\"rung\":%d,\"seed\":%u,\"frames\":%u,"
                  "\"unique_lines\":%d,\"subsample_steps\":%d,\"ext_samples\":%d,\"max_write_us\":%u,"
                  "\"line_origin_samples\":%.1f,\"chroma\":\"%s\",\"dma_restarts\":%u,\"dma_restart_last\":\"%s\",\"free_heap\":%u}\n",
                  PATTERNS[gen::pattern()].name, gen::pattern(), lv.sync_code, lv.white_code, g_flat_ire,
                  gen::clock_ppm(), gen::actual_sample_hz(), gen::actual_sample_hz() / 4.0,
                  gen::actual_sample_hz() / ntsc::SAMPLES_PER_LINE, gen::test().def->id, gen::test().rung,
                  (unsigned)gen::test().seed, (unsigned)gen::frames(), gen::unique_lines(),
                  gen::subsample_steps(), gen::ext_samples(), (unsigned)gen::max_write_us(), ntsc::LINE_ORIGIN,
                  gen::chroma_follows() ? "follows_timing" : "colour_under", (unsigned)gen::dma_restarts(), gen::dma_restart_note(),
                  (unsigned)ESP.getFreeHeap());
}

static void print_help() {
    Serial.println("commands:");
    Serial.println("  list                     list patterns");
    Serial.println("  p <n|name>               select pattern");
    Serial.println("  flat <ire>               set flat-field level (selects 'flat')");
    Serial.println("  levels <sync> <white>    DAC codes for -40 and 100 IRE");
    Serial.println("  ppm <x>                  trim sample clock in ppm");
    Serial.println("  tests                    list the test catalog and ladders");
    Serial.println("  test <id> [rung] [seed]  run a test (test V0 = clean baseline)");
    Serial.println("  chroma [under|follows]   colour-under (VHS/Betamax, default) or direct colour");
    Serial.println("  manifest                 print the current run's manifest (JSON)");
    Serial.println("  pdump <frame> [line] [n] planned shift per line (ns target, e, v, ns actual)");
    Serial.println("  hold <1|0>               freeze/unfreeze the ring; prints the frame in each half");
    Serial.println("  lens <half>              played length of each line in ring half 0 or 1");
    Serial.println("  info                     print state as JSON");
    Serial.println("  diag                     show DMA descriptor progress");
    Serial.println("  dc [code]                force DAC to a constant (no arg: back to video)");
    Serial.println("  gpio <0|1>               wiring test: drive GPIO25 digitally (reset to recover)");
    Serial.println("  dump <i>                 hex DAC codes of ring line i (half 0: 0.., half 1: 600..)");
}

static void ok(bool r) { Serial.println(r ? "ok" : "error"); }

static void handle(String line) {
    line.trim();
    if (!line.length()) return;
    int sp = line.indexOf(' ');
    String cmd = sp < 0 ? line : line.substring(0, sp);
    String arg = sp < 0 ? "" : line.substring(sp + 1);
    arg.trim();

    if (cmd == "help" || cmd == "?") {
        print_help();
    } else if (cmd == "list") {
        for (int i = 0; i < NUM_PATTERNS; i++) Serial.printf("  %d %s\n", i, PATTERNS[i].name);
    } else if (cmd == "p" || cmd == "pattern") {
        int idx = -1;
        for (int i = 0; i < NUM_PATTERNS; i++)
            if (arg == PATTERNS[i].name) idx = i;
        if (idx < 0 && arg.length() && isDigit(arg[0])) idx = arg.toInt();
        ok(gen::set_pattern(idx));
    } else if (cmd == "flat") {
        g_flat_ire = arg.toFloat();
        for (int i = 0; i < NUM_PATTERNS; i++)
            if (!strcmp(PATTERNS[i].name, "flat")) ok(gen::set_pattern(i));
    } else if (cmd == "levels") {
        float s, w;
        if (sscanf(arg.c_str(), "%f %f", &s, &w) == 2 && s >= 0 && w <= 255 && w > s) ok(gen::set_levels({s, w}));
        else ok(false);
    } else if (cmd == "ppm") {
        ok(gen::set_clock_ppm(arg.toFloat()));
    } else if (cmd == "dump") {
        static uint8_t codes[ntsc::SAMPLES_PER_LINE + ntsc::MAX_EXT];
        int n = gen::read_line(arg.toInt(), codes, sizeof(codes));
        Serial.printf("line %d n=%d\n", (int)arg.toInt(), n);
        for (int k = 0; k < n; k++) Serial.printf("%02x%s", codes[k], (k % 64 == 63 || k == n - 1) ? "\n" : "");
        Serial.println("end");
    } else if (cmd == "diag") {
        for (int i = 0; i < 5; i++) { Serial.printf("dscr %08x\n", (unsigned)i2s_dac::current_descriptor()); delayMicroseconds(37); }
    } else if (cmd == "dc") {
        i2s_dac::force_dc(arg.length() ? arg.toInt() : -1);
        ok(true);
    } else if (cmd == "gpio") {  // wiring test: drive GPIO25 as a digital pin
        rtc_gpio_deinit(GPIO_NUM_25);
        pinMode(25, OUTPUT);
        digitalWrite(25, arg.toInt() ? HIGH : LOW);
        ok(true);
    } else if (cmd == "tests") {
        for (int i = 0; i < perturb::NUM_DEFS; i++) {
            const perturb::Def &d = perturb::DEFS[i];
            Serial.printf("  %-3s %-28s [%s] %s:", d.id, d.name, d.targets, *d.p1_name ? d.p1_name : "-");
            for (int r = 0; r < d.rungs; r++) {
                if (*d.p2_name) Serial.printf(" %g@%g", d.p1[r], d.p2[r]);
                else Serial.printf(" %g", d.p1[r]);
            }
            Serial.println();
        }
    } else if (cmd == "test") {
        char id[8] = {0};
        int rung = 1;
        unsigned seed = 1;
        int n = sscanf(arg.c_str(), "%7s %d %u", id, &rung, &seed);
        perturb::Test t;
        ok(n >= 1 && perturb::make(id, rung, seed, &t) && gen::set_test(t));
    } else if (cmd == "hold") {
        gen::hold(arg.toInt() != 0);
        delay(100);  // let an in-progress rewrite finish
        Serial.printf("frames %u %u\n", (unsigned)gen::frame_in_half(0), (unsigned)gen::frame_in_half(1));
    } else if (cmd == "lens") {
        int h = arg.toInt() & 1, n = gen::half_lines(h);
        for (int L = 0; L < n; L++)
            Serial.printf("%d%c", gen::line_length(gen::half_base(h) + L), L % 25 == 24 ? '\n' : ' ');
        Serial.println("end");
    } else if (cmd == "chroma") {
        if (arg == "under" || arg == "stable") ok(gen::set_chroma_follows(false));
        else if (arg == "follows") ok(gen::set_chroma_follows(true));
        else Serial.println(gen::chroma_follows() ? "follows" : "under");
    } else if (cmd == "manifest") {
        print_manifest(Serial);
    } else if (cmd == "pdump") {
        unsigned frame = 0, first = 0, count = ntsc::LINES_PER_FRAME;
        sscanf(arg.c_str(), "%u %u %u", &frame, &first, &count);
        for (unsigned L = first; L < first + count && L < (unsigned)ntsc::LINES_PER_FRAME; L++) {
            gen::LinePlan p = gen::plan(frame * ntsc::LINES_PER_FRAME + L);
            Serial.printf("%u %u %.3f %d %d %.3f\n", frame, L + 1, p.shift_ns, (int)p.e, p.v, gen::plan_shift_ns(p));
        }
        Serial.println("end");
    } else if (cmd == "info") {
        print_info();
    } else {
        Serial.println("unknown command (try 'help')");
    }
}

void setup() {
    Serial.begin(921600);  // keep in step with BAUD in tools/gen_cmd.py
    delay(200);
    Serial.println("starting NTSC generator...");
    bool r = gen::begin(0, DEFAULT_LEVELS);
    Serial.println(r ? "NTSC generator running on GPIO25" : "NTSC generator FAILED to start");
    print_info();
}

void loop() {
    static String buf;
    while (Serial.available()) {
        char c = Serial.read();
        if (c == '\n' || c == '\r') { handle(buf); buf = ""; }
        else if (buf.length() < 120) buf += c;
    }
    delay(5);
}
