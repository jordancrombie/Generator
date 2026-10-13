# NTSC Test-Signal Generator (ESP32)

The generator side of a rig for grading **time-base correctors (TBCs) used with
VHS and Betamax**. An ESP32 makes NTSC composite video with precisely
controlled faults. The signal passes through the TBC under test, and an
ADP2230 captures what went in and what came out, so a separate grader can
score how well the TBC repaired it.

![Test rig: the ESP32 feeds the TBC; CH2 records the generator output, CH1 the TBC output; the grader compares them](docs/rig.svg)

The test catalog, scoring approach and grader contract are in the design doc,
[TBC Test Catalog & Grader Contract](https://claude.ai/code/artifact/45d38c23-276f-4e00-b547-f803ce8926ae).
Measured hardware and toolchain facts (settling times, quirks, pitfalls) are in
[docs/bench-notes.md](docs/bench-notes.md). Read it before changing the tools.

## Status (2026-10-13)

| Area | State |
|---|---|
| Colour bars, first light | **Done.** SMPTE and EIA bars, staircase, flat, crosshatch. Hue within 1.5° and luma within 0.5 IRE at the DAC |
| Timing tests (H1–H5, F1) | **Built and verified** against the reference model. F1 not yet measured on the scope |
| Structure tests (S3–S9) | **Built and verified**; observed through the TBC on CH1. S4, S8 and S9 target frame synchronisers |
| Device tagging and reports | **Done.** `--dut`/`--note` on every run; optional `--html` report |
| Colour-under (VHS/Betamax) | **Default.** Direct colour (U-matic/1") available with `chroma follows` |
| Frame marker, manifest, capture runner | **Done.** The marker decodes through the TBC on CH1 |
| CH2 reference and level calibration | **Waiting on hardware** (BNC tees and a 75 Ω terminator) |
| Remaining tests | S1, S2 (sync), C1–C4 (colour), F2, F3, L1–L4 (levels, noise, dropouts); H6 velocity error (line TBCs, for the tape-deck phase); latency in frames (needs CH2) |
| Grader | Not part of this repo. `tools/perturb_model.py` is its reference for what was sent |

## Wiring

![ESP32 wiring: GPIO25 through a 75 ohm series resistor to a BNC jack and tee; one branch to the TBC, one to ADP2230 CH2; TBC output through monitor and vectorscope to a terminated CH1](docs/wiring.svg)

| From | To | Notes |
|---|---|---|
| ESP32 GPIO25 (DAC1) | 75 Ω series resistor → BNC centre | Source resistor; matches the cable, does not terminate it |
| ESP32 GND | BNC shield | |
| BNC tee (run 1) | TBC input | The TBC's 75 Ω input terminates run 1 |
| BNC tee (branch) | ADP2230 **CH2** | Short coax, no terminator; the scope's 1 MΩ input only listens |
| TBC output (run 2) | monitor → vectorscope (loop-through, 75 Ω off) | |
| End of run 2 | BNC tee + 75 Ω terminator → ADP2230 **CH1** | The one termination for run 2 |
| ESP32 USB, ADP2230 USB | Mac | Power, serial control, capture |

**Every coax run needs exactly one 75 Ω termination at its far end.** The
ESP32's DAC can push its output up but barely pull it down, so an open-ended
run turns the 3.58 MHz chroma into a sawtooth (about 4× too small) and the
picture goes dark and rolls.

The default levels (`levels 0 73`) assume an unloaded output and still need
calibrating on CH2. The target is 286 mV of sync and 714 mV of white above
blanking.

## How it works

- **Signal path.** The classic ESP32 (D0WD) runs I2S0 in LCD/DAC mode and
  streams DMA straight to its 8-bit DAC on GPIO25 at 4 × fsc =
  14.318182 MHz, clocked from the APLL.
- **Two-frame DMA ring.** The ring has two halves, one frame each. An
  end-of-frame interrupt wakes a task that rewrites the half that just finished
  while the other plays (6–9 ms of a 33 ms frame). Sequences never repeat, and
  a half can hold any number of lines (up to 600), so frames can drop fields or
  change line count.
- **Pre-rendered lines.** Each distinct line is rendered once from a model
  (sync, burst, Y/U/V segments with band-limited edges) in up to 8 sub-sample
  shifted copies.
- **Timing faults live in the ring, not the picture.** A descriptor's length
  sets where the next line's sync falls, in 2-sample (140 ns) steps; the
  shifted copies fill in the fine steps (35 ns with 4 copies).
- **Colour-under by default.** Sync, luma and the colour envelopes jitter while
  the 3.58 MHz carrier stays on a continuous reference, as a VHS/Betamax deck
  delivers it.
- **Reproducible.** Every perturbation is a pure function of the absolute line
  number (`src/perturb.cpp`, mirrored by `tools/perturb_model.py`), so the
  grader can rebuild exactly what was sent from the manifest.
- **Frame marker** on lines 24 and 287, the top picture rows. TBCs commonly
  blank the vertical interval, and the TBC under test blanks lines 10–21. The
  marker is 36 cells of 1.4 µs encoding test code, rung, a 16-bit frame counter
  and a CRC-4.
- **Standards.** Timing follows SMPTE 170M: 4.7 µs H-sync, 2.3 µs equalizing
  pulses, serrated broad pulses, 9-cycle burst at 5.3 µs, 7.5 IRE setup,
  interlace half-lines at 263/283.

## Tests

Each test is tagged with the TBC class it targets. Its manifest states the
ideal output for a **line TBC** (corrects timing, passes structure through)
and for a **frame TBC / synchroniser** (outputs standard fields whatever
comes in). That way a line TBC isn't marked down for passing a dropped field
through.

| ID | Test | Aimed at | Rungs (mild → severe) |
|---|---|---|---|
| V0 | clean baseline | both | 1 |
| H1 | random line jitter, rms ns | both | 5, 10, 20, 50, 100, 200 |
| H2 | sinusoidal jitter, peak ns @ Hz | both | 50@1 … 2000@30 |
| H3 | timing step, µs, toggles every 0.5 s | both | 0.25, 0.5, 1, 2, 5 |
| H4 | head-switch skew, µs (lines 520–256) | both | 0.5, 1, 2, 5, 10 |
| H5 | displaced single line (100 and 362), samples | both | ±2, ±4, ±10, ±20 |
| F1 | sample-clock offset, ppm | both | ±10, ±30, ±100, ±300, ±1000 |
| S3 | missing H syncs from line 100, lines | both | 1, 2, 5, 10, 20 |
| S4 | malformed vertical interval, variant | frame | 1 EQ pulses → H sync · 2 unserrated V-sync · 3 both · 4/5 V-sync missing in field 1 every 30/6 frames · 6 V-sync missing in every field |
| S5 | progressive 240p (two 262-line fields per frame) | frame | 1 |
| S6 | lines removed/repeated per field at lines 200 and 462 | frame | −1, +1, −3, +3, −10, +10 |
| S7 | dropped field: every N frames field 2 is missing | frame | 60, 30, 15, 6, 2 |
| S8 | field-length wow: lines added/removed per field, peak @ Hz | frame | 1@0.5, 2@1, 5@1, 10@2, 20@2 |
| S9 | search noise bar: a band of sync-less snow moving 23 lines a frame, lines | both | 2, 5, 10, 20, 40 |

A dropped field leaves a lone half-line. It plays as 456 samples rather than
455, because the DAC consumes samples in pairs (+35 ns at the drop).

## Running

Build and flash with PlatformIO's own Python. Homebrew's `pio` runs on
Python 3.14, which the platform rejects. The serial port moves between USB
sockets, so look it up:

```
~/.platformio/penv/bin/pio run -t upload --upload-port $(ls /dev/cu.wchusbserial* | head -1)
```

The host tools need `numpy` and `pyserial`.

```
export TBC_DUT="AVT-8710"                         # name the device under test once per session
export TBC_NOTE="composite in/out, controls at default"
python3 tools/run_suite.py                      # smoke set: one rung of every test (~3 s each)
python3 tools/run_suite.py --html               # ...plus an HTML report in reports/
python3 tools/run_suite.py H1:all S7:4-5        # chosen tests and rungs on one scope session
python3 tools/run_test.py H1 4                  # a single rung
python3 tools/run_test.py H1 4 --keep           # ...and keep its capture for debugging
python3 tools/observe_tbc.py runs/<run>         # what the TBC passed, dropped or repeated
python3 tools/verify_timing.py                  # check the firmware against the reference model (~25 s)
python3 tools/check_lines.py                    # check colour bars as rendered
```

Every run prints `observe_tbc`'s report on the TBC output, records the
device under test (`--dut`, or `$TBC_DUT`) and its settings (`--note`, or
`$TBC_NOTE`) in the manifest, and stops with an error if the generator isn't
advancing frames. `--html` (off by default) also writes a self-contained
report to `reports/<time>_<dut>.html`. It contains a summary table and, for
each run, the ideal output per TBC class, key figures, a frame-sequence strip
and one captured line. `reports/` is not committed. The capture is
then **deleted by default**. With `--keep` (on `run_test.py` or
`run_suite.py`), the run stays in `runs/<timestamp>_<test>_r<rung>/` as
`manifest.json` plus `capture.npz` (raw int16 per channel, volts = raw ×
scale + offset). A run whose report fails is always kept. Kept runs are
large, 100 MB per 0.5 s at 50 MS/s, and `runs/` is not committed. Where the time goes is broken down in
[docs/bench-notes.md](docs/bench-notes.md#where-test-run-time-goes).

## Serial commands (921600 baud)

| Command | |
|---|---|
| `tests` | list the test catalog and each test's ladder |
| `test <id> [rung] [seed]` | start a test; `test V0` is the clean baseline |
| `chroma [under\|follows]` | colour-under (default, VHS/Betamax) or direct colour |
| `manifest` | the current run's manifest, one JSON line |
| `list` / `p <n\|name>` | list or select pattern: `smpte_bars`, `eia_bars_75`, `staircase`, `flat`, `crosshatch`, `black` |
| `flat <ire>` | flat field at the given IRE level |
| `levels <sync> <white>` | DAC codes for −40 and 100 IRE |
| `ppm <x>` | trim the sample clock (1.8 ppm steps), e.g. to correct crystal error |
| `info` | current state as JSON |
| `hold <1\|0>` | freeze or unfreeze the ring; prints the frame in each half |
| `pdump <frame> [line] [n]` | planned shift per line: target ns, e, v, played ns |
| `lens <half>` | played length of each line in ring half 0 or 1 |
| `dump <i>` | DAC codes of ring line *i* (half 0 from 0, half 1 from 600) |
| `diag`, `dc [code]`, `gpio <0\|1>` | DMA progress, constant DAC output, digital wiring test |

## First observations through the TBC

These are observations from CH1 alone, not grades. The input side has not been
measured yet.

| Test | What came out of the TBC |
|---|---|
| V0, S3 (20 missing syncs), S5 (240p), H3 (5 µs steps) | Every field passed once, in order |
| H4 (10 µs head-switch skew) | Fully corrected; marker at 0.0 µs offset |
| S6 −10 lines per field | Drops a field now and then to keep up |
| S6 +10 lines per field | Repeats a frame |
| S7 dropped fields | Re-shows earlier fields (…168 169 168 169…) |
| H1 50 ns rms jitter | About 23 ns rms residual picture displacement (floor 0.7 ns) |
| S4 unserrated V-sync, EQ pulses as H sync | Locks normally |
| S4 V-sync missing in every field | Still outputs standard fields in order, but with no vertical reference the picture sits far lower: marker at line 201–209 after V-sync instead of 15, landing somewhere different each time it locks |
| S8 field-length wow (±20 lines at 2 Hz) | Occasionally repeats a field to keep its output standard |
| S9 noise bar (20 lines) | Every field passed in order |

In every run the output stayed standard: 262.5 lines per field and 0.4–0.6 ns
rms line jitter.

## Code map

| Path | |
|---|---|
| `src/main.cpp` | serial command interface |
| `src/ntsc_gen.*` | DMA ring, line pool, frame task |
| `src/line_render.*` | renders one line from the signal model |
| `src/perturb.*` | test catalog, timing model, frame structure |
| `src/marker.*` | frame marker encoding |
| `src/manifest.*` | JSON manifest for the grader |
| `src/i2s_dac.*` | I2S/DAC/APLL hardware setup and EOF interrupt |
| `src/patterns.*` | test patterns as Y/U/V segments |
| `tools/perturb_model.py` | reference model of perturbations, frame structure and marker |
| `tools/run_suite.py`, `tools/run_test.py` | run many rungs on one scope session, or one rung |
| `tools/dwf_scope.py` | ADP2230 capture (`Scope` stays open; inputs settle once) |
| `tools/observe_tbc.py` | TBC output report from a run (`analyze()` returns it as data) |
| `tools/html_report.py` | the optional HTML report |
| `tools/verify_timing.py`, `tools/check_lines.py` | firmware verification over serial |
| `tools/analyze_capture.py` | levels, line rate and bar colours from a capture |
| `tools/gen_cmd.py` | serial helpers (`BAUD`, `request`, `dump_line`) and a command-line sender |
| `tools/dwf_list.py`, `tools/dwf_capture.py` | scope utilities |
| `docs/bench-notes.md` | measured hardware, toolchain and signal facts, open issues |
