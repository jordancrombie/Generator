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
0.5 s at 50 MS/s is 100 MB per run. About 25 exploratory runs filled 2.6 GB,
so runs are now deleted after their report unless `--keep` is given.

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
  Per-line `fmod`, `sin` or division in double cost as much again (L3 hit
  28.8 ms). Periodic tests now use a 32-bit integer phase (`n × phase_inc`,
  wrapping at 2³²) with float trig. That is exact and reproducible in Python,
  and every test rewrites in under 10 ms.
- **Ties in quantization:** F2's shifts land exactly on half-steps
  (910 samples × ppm × 10⁻⁶), where float rounding in C and Python disagree.
  F2 is quantized in exact 64-bit integer arithmetic instead.

- **DMA stop signature, more data:** also about once per verifier run at
  X1 stress 5 (no signal, snow and a 5 µs skew mixed). Never during a capture
  so far.
- **Open issue: the I2S DMA occasionally stops.** Seen as the frame counter
  freezing, with `diag` showing the same descriptor every time. It happens
  about once per full `verify_timing.py` run, always on the switch from S8
  (variable-length frames) to S9, after the freeze/dump/unfreeze and two
  rebuilds that the verifier does. 48 plain test switches (including S8→S9)
  did not reproduce it. Mitigations: the APLL is only retuned when its rate
  changes; a frame watchdog restarts the DMA after 200 ms without an
  end-of-frame interrupt and counts it (`dma_restarts` and
  `dma_restart_last` in `info`); and runs flag any restart during a
  capture.

## Analog signal

- **The ESP32 DAC can push its output up but barely pull it down.** Into an
  unterminated line, falling edges decay slowly through the cable
  capacitance: the burst became a sawtooth and chroma came out about 4×
  too small, while luma (slow) looked fine. Every coax run needs exactly one
  75 Ω termination at its far end (see `docs/wiring.svg`).
- **Sync sits at DAC code 0, so nothing can go below it.** Noise on the
  sync tip (L2) is half clipped, and S1 can't make sync *deeper* than 40 IRE.
  When levels are calibrated, leave headroom below the sync tip (e.g. sync
  at code ~15–20).
- **The DAC's quantization floor** is about 1.9 IRE per code (direct drive),
  or 0.55 IRE rms of quantization noise: roughly 45 dB SNR. Below that, L2
  measures the DAC, not the test.
- **Line buffers place sync 16 samples in** (`LINE_ORIGIN`). With 4, S2's
  slowest edge (1000 ns, about 24 samples wide) started before the buffer
  did, giving a jump halfway down the edge instead of a slow edge.
- **Unloaded DAC output** is about 12.9 mV per code (0–3.3 V). Measured
  open-circuit: blanking 283 mV (code 21), white 911 mV (code 73). With a
  75 Ω series resistor into a 75 Ω termination, expect about half.
- **A back-terminated TBC output into an open line reads about 1.9×**
  (540 mV of sync instead of 286 mV).

## AVT-8710

- **Its fallback output (no input)** is 8 full-field bars at 100 % white
  (100, 69, 56, 48, 36, 28, 16, 7.5 IRE). The yellow bar's hue matches ours
  (~165°), so a bar-shape check alone can be fooled; our frame marker can't
  be. The analysis only measures fields that carry our marker.
- **Lost fields:** rides through up to 6 consecutive fields of no signal or
  snow (black during the gap, often one garbled field as it re-acquires);
  from 7 it switches to its own bars.
- **Reported faults of the newer "black" units** (forum reports, no
  manufacturer statement): stuck/frozen fields, random dropped and repeated
  frames, a "ghost mode" after bad tape that only a power-cycle clears, and
  tearing, jitter and ghosting. X1 is designed around these. The unit tested
  here recovered cleanly from every X1 stress, with 1–2 ghosted fields under
  jitter alone.

- **It blanks lines 10–21** (the vertical interval). That is why the frame
  marker lives on picture lines 24 and 287.
- **With no V-sync at all (S4 rung 6)** it still outputs standard fields in
  order, but the picture sits far lower: the marker at line 201–209 after its
  V-sync instead of 15, landing somewhere different each time it locks. That is why the marker decoder searches the
  whole field and reports the marker's line.
- **Its output is always standard:** 262.5 lines per field and 0.4–0.6 ns
  rms line jitter, whatever the input does.

## Datavideo TBC-1000

- **Its output intermittently goes dead for about a minute.** No sync at all,
  only ~40 mV of energy near 3.58 MHz on CH1, then it recovers on its own.
  Seen twice on 2026-10-13, both times starting during an input-loss test
  (X1 stress 3, lost fields; S11 rung 2, snow). It's flagged as "output dead"
  in reports.
- **Dropout watch, 5 minutes** (60 s clean / 180 s S11:2 / 60 s clean):
  one dead sample, 134 s into the stimulus, alive again a second later.
  It's genuine: 0 syncs and under 0.2 V of swing for the whole 50 ms
  window, which two fields of snow can't produce. No dropouts on clean
  input. So the dropouts vary from under a second to about a minute, and
  have only been seen during input loss.
- **More data, 2026-10-13 evening** (reports `20261013-2122*` to `2147*`):
  - Three suites back to back (`compare_suites.py`): suites 1 and 2 were
    clean, with lost-field tolerance ∞ in both. In suite 3 the output died
    from S4 r6 to S6 r6 (~13 s) and was back by S7 r4. S10 tolerance fell
    to 2.
  - S4:6 watch (30 / 180 / 30 s): 5 episodes, lasting 1–10 s, including one
    in the clean baseline.
  - **Rig artifact found and fixed:** on every test switch the generator
    parked the DMA ring on sync-only lines with no vertical sync for
    ≥100 ms. It now parks on a standard 525-line black frame.
  - After the fix, a 4-minute clean V0 control had 0/238 dead samples.
  - After the fix, an S4:6 repeat died 98 s into the stimulus. It stayed
    dead through 30 s of clean V0 and was still dead minutes later, with
    0 syncs and ~0.2 V of swing. The generator was healthy (frames
    advancing, no DMA restart).
  - **The swing sags before it dies.** CH1 peak-to-peak fell over the
    preceding ~6 s before 4 of 7 episodes (e.g. 1.39 → 1.08 V, 1.33 →
    1.21 V). One stretch ran at ~0.24 V with sync still present. The
    control held a steady 1.45–1.52 V. This looks like the output stage or
    supply browning out (heat or capacitors?) rather than a sync-logic
    lock-up. So far it has only started while the input is disturbed or
    soon after.
- **After a power-cycle (with the parking fix): 10 suites, 45 minutes, no
  dead output** (`reports/20261013-2228*` to `2308*`, comparison
  `20261013-230832_compare10_*`). Output sync depth held at 0.42–0.43 V
  throughout, with no sag. Lost-field tolerance was ∞ / ∞ for 8 suites. In
  suites 9 and 10, one snow run each lost sync for a field (S11 r1 and
  r6), so the snow tolerance read 0 and 5. These look like one-off
  glitches, not a dying output. So the long episode before this cleared
  with a power-cycle and didn't come back in 45 minutes. Either the
  test-switch artifact was a trigger, or the fault needs a longer warm or
  stressed run to appear.
- **CH2 wired (2026-10-14): latency and lock.** The TBC-1000's output is
  **not locked to its input**: its line period is 63555.05 ns against our
  63556.45 ns, so it runs about +22 ppm fast. It behaves as a frame
  synchroniser with its own clock. Latency was 9.1 ms (0.55 field) in one
  session and 30.1 ms (1.8 fields) an hour later. Within a session it shrinks
  by about 0.1 ms per run (22 ppm × ~4 s). Expect it to repeat a frame about
  every 25 minutes when the delay wraps. Confirmed: latency fell from
  30.1 ms to 25.0 ms over 217 s, a drift of 23 ppm.
- **Gain against the real input (CH2):** luma ×0.64 (gray 53 IRE out for
  82 in), burst brought up to ~38 IRE whatever comes in (×1.42 on our
  27 IRE burst), chroma ×1.34 with it. So it has ACC keyed on the burst. With
  a 6 IRE burst (C1 r3) chroma goes ×2.1 and hue shifts by 14°.
- **No proc amp.** Its output runs about 30 % below the AVT-8710's: gray
  bar 56 IRE (AVT 79), burst 38 (43), yellow chroma 58 (70).
- **Lost fields** vary between runs: rode through up to 8 (then its own
  pattern at 9, lost sync at 10–11) in one run, and all 11 with one odd
  261-line field in another. With no input at all it outputs black (V1)
  rather than bars.
- **Colour:** kills colour when burst is missing (C2), unlike the AVT-8710.
  Partial ACC for a weak burst (×1.6). Clamps hum cleanly.
- **Ghost detection** first flagged every field because of the lower output
  level. Marker cells are now judged against each field's own framing cells.

## Measurement pitfalls

- **Generator output on CH2** (tee at the TBC's 75 Ω input): sync 0.226 V
  (79 % of 286 mV), burst 27 IRE p-p and yellow chroma 44 IRE p-p (both
  about 0.68× nominal), gray bar 82 IRE. Line jitter floor 3.1 ns rms
  (CH2's line-period jitter for H1 r4, 50 ns rms injected, reads 77 ns: a
  period spans two independently jittered syncs, so expect √2 × 50 = 71 ns).
- **Picture lines are picked relatively.** `picture_metrics` used to accept
  only lines with the gray bar above 55 IRE. The TBC-1000's gray sits near
  53, so most of its output was skipped and its picture figures were
  missing or based on a few lines. Lines now qualify when gray is above
  35 IRE and at least twice the blue bar.

- **Sync slicing:** with full-size chroma, red and blue bars (down to
  −16 IRE) and the burst trough (−20 IRE) dip into a mid-level sync slicer.
  Accept only pulses wider than 2 µs.
- **"No sync here" checks** must use a threshold below −20 IRE (we use
  −30 IRE), or the burst trough reads as sync.
- **Blanking level:** measure it on the back porch (8.2–9.1 µs after sync).
  A histogram of the whole line picks the wrong level.
- **False markers in snow:** S9's snow, scanned at 241 offsets on every line,
  occasionally passes the marker's 4-bit CRC (it once produced a "frame
  31410"). Only markers naming the run's own test code and rung are accepted;
  rejects are counted as `spurious_rejected`.
- **Vertical-interval lines have no burst:** a check that measures burst
  phase must skip them. S8's varying line counts move them to unexpected
  slot positions; that gave a false 180° error.
- **Picture displacement:** compare lines of one type against their median
  (template correlation). Edge-finding across mixed line types gives
  nonsense (530 ns "spread" on a clean signal).
