# Bench notes

Measured facts about the hardware, toolchain and signal that are easy to
forget and slow to rediscover. Each one cost time once. Add to this file
whenever something surprises you, with the number and how it was found.

## Where test-run time goes

One rung with `run_suite.py` takes about **3.0 s**:

| Stage | Time | Notes |
|---|---|---|
| Switch test on the ESP32 | ~0.4 s | Serial command; the line pool is re-rendered |
| TBC lock (`--settle`) | 2.0 s | A fixed allowance, not measured. A candidate for tuning per TBC |
| Scope input settling | 2.0 s, **once per session** | Overlaps the TBC lock. See the ADP2230 section |
| Acquisition | = capture length | 0.4–0.6 s typical |
| Download, 2 × 30 M samples | ~0.25 s | int16 over USB |
| Save | ~0.1 s | Uncompressed `.npz` |

Before the two waits overlapped and the scope stayed open, each run took
6.5 s. `verify_timing.py` took about 10 minutes at 115200 baud with blocking
serial reads; at 921600 baud with non-blocking reads it takes 24 s.

**Disk:** a capture is 4 bytes × rate × seconds (two int16 channels), so
0.5 s at 50 MS/s is 100 MB per run. `runs/` grows quickly. Lower `--rate` or
`--seconds` for long suites.

## ADP2230 and the WaveForms SDK

- **The scope inputs need about 2 s to settle after a range or offset
  change.** The first capture after one reads garbage levels (e.g.
  0.001–0.105 V for a 0–1 V signal). Found in the earlier NTSC project
  (`../NTSC/NTSC_FINDINGS.md`). `dwf_scope.Scope` starts the clock when
  opened, and only the first capture waits.
- **Buffer:** 64 M samples per channel in the default configuration
  (config 5 has 128 M); at most 100 MS/s. That is 0.67 s at 100 MS/s or
  1.34 s at 50 MS/s.
- **Download as int16** (`FDwfAnalogInStatusData16`, in 1 M-sample chunks).
  As doubles, a full buffer is 512 MB per channel. Volts come from a linear
  fit against a short `FDwfAnalogInStatusData` read.
- **The inputs are 1 MΩ.** They never terminate a 75 Ω line.
- `FDwfEnumDevices` does not exist; it is `FDwfEnum`.

## ESP32 and toolchain

- **PlatformIO:** Homebrew's `pio` runs on Python 3.14, which the
  pioarduino platform rejects ("Python version must be between 3.10 and
  3.13"). Use `~/.platformio/penv/bin/pio` (Python 3.11).
- **The esptool in the PlatformIO env** fails standalone (missing
  `rich_click`). Use a separate venv `pip install esptool` for `chip-id`
  and similar.
- **The serial port is renamed per USB socket** (`/dev/cu.wchusbserial210`,
  then `…110`). The tools auto-detect `wchusbserial*`, or set `GEN_PORT`.
- **Serial runs at 921600 baud.** The CH340 handles it. `BAUD` in
  `tools/gen_cmd.py` must match `Serial.begin()` in `src/main.cpp`. Read
  only what has arrived (`in_waiting`): `read(65536)` with a timeout blocks
  for the whole timeout on every call.
- **IDF 5 APLL:** `rtc_clk_apll_coeff_set()` hangs forever if the APLL is
  not powered. Call `periph_rtc_apll_acquire()` first.
- **APLL resolution** is about 1.8 ppm per step. The default sample clock
  is 14,318,186 Hz (+0.3 ppm), before crystal error.
- **The I2S → DAC path plays 16-bit samples swapped in pairs:** sample *k*
  is stored at index *k* ^ 1, with the code in the high byte. Every DMA
  descriptor must hold an **even** number of samples, which is why a lone
  half-line plays as 456 samples, not 455.
- **Double-precision math is software-emulated.** A stray `double` constant
  in the per-sample loop made a frame rewrite take 27 ms of a 33 ms budget.
  With float copies plus visiting only nearby segments, it takes 6–9 ms.

## Analog signal

- **The ESP32 DAC can push its output up but barely pull it down.** Into an
  unterminated line, falling edges decay slowly through the cable
  capacitance: the burst became a sawtooth and chroma came out about 4×
  too small, while luma (slow) looked fine. Every coax run needs exactly one
  75 Ω termination at its far end (see `docs/wiring.svg`).
- **Unloaded DAC output** is about 12.9 mV per code (0–3.3 V). Measured
  open-circuit: blanking 283 mV (code 21), white 911 mV (code 73). With a
  75 Ω series resistor into a 75 Ω termination, expect about half.
- **A back-terminated TBC output into an open line reads about 1.9×**
  (540 mV of sync instead of 286 mV).

## The TBC under test

- **It blanks lines 10–21** (the vertical interval). That is why the frame
  marker lives on picture lines 24 and 287.
- **Its output is always standard:** 262.5 lines per field and 0.4–0.6 ns
  rms line jitter, whatever the input does.

## Measurement pitfalls

- **Sync slicing:** with full-size chroma, red and blue bars (down to
  −16 IRE) and the burst trough (−20 IRE) dip into a mid-level sync slicer.
  Accept only pulses wider than 2 µs.
- **"No sync here" checks** must use a threshold below −20 IRE (we use
  −30 IRE), or the burst trough reads as sync.
- **Blanking level:** measure it on the back porch (8.2–9.1 µs after sync).
  A histogram of the whole line picks the wrong level.
- **Picture displacement:** compare lines of one type against their median
  (template correlation). Edge-finding across mixed line types gives
  nonsense (530 ns "spread" on a clean signal).
