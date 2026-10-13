// NTSC composite test-signal generator.
// Output: GPIO25 (DAC1). Control: serial 115200, line-based commands ("help").
#include <Arduino.h>
#include "driver/rtc_io.h"

#include "i2s_dac.h"
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
                  "\"unique_lines\":%d,\"free_heap\":%u}\n",
                  PATTERNS[gen::pattern()].name, gen::pattern(), lv.sync_code, lv.white_code, g_flat_ire,
                  gen::clock_ppm(), gen::actual_sample_hz(), gen::actual_sample_hz() / 4.0,
                  gen::actual_sample_hz() / ntsc::SAMPLES_PER_LINE, gen::unique_lines(),
                  (unsigned)ESP.getFreeHeap());
}

static void print_help() {
    Serial.println("commands:");
    Serial.println("  list                     list patterns");
    Serial.println("  p <n|name>               select pattern");
    Serial.println("  flat <ire>               set flat-field level (selects 'flat')");
    Serial.println("  levels <sync> <white>    DAC codes for -40 and 100 IRE");
    Serial.println("  ppm <x>                  trim sample clock in ppm");
    Serial.println("  info                     print state as JSON");
    Serial.println("  diag                     show DMA descriptor progress");
    Serial.println("  dc [code]                force DAC to a constant (no arg: back to video)");
    Serial.println("  dump <i>                 hex DAC codes of chain line i (0..1049)");
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
        static uint8_t codes[ntsc::SAMPLES_PER_LINE];
        int n = gen::read_line(arg.toInt(), codes, sizeof(codes));
        Serial.printf("line %d n=%d\n", (int)arg.toInt(), n);
        for (int k = 0; k < n; k++) Serial.printf("%02x%s", codes[k], (k % 64 == 63 || k == n - 1) ? "\n" : "");
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
    } else if (cmd == "info") {
        print_info();
    } else {
        Serial.println("unknown command (try 'help')");
    }
}

void setup() {
    Serial.begin(115200);
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
