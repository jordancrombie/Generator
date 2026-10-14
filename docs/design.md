# TBC Test Catalog & Grader Contract

The design of the rig: what each test sends, what a perfect TBC should do with
it, how results will be scored, and what the generator hands the grader.
Moved into the repo from a claude.ai doc on 2026-10-14 and brought up to date.
Measured facts live in [bench-notes.md](bench-notes.md), and open work in
[TODO.md](../TODO.md).

## Purpose and rig

Each test sends a known, deliberately damaged NTSC signal through the TBC
under test. The grader then judges how close the TBC's output comes to the
undamaged signal. The generator's job is to make every perturbation exact,
repeatable, and described in a machine-readable manifest.

![Test rig: the ESP32 feeds the TBC; CH2 records the generator output, CH1 the TBC output](rig.svg)

- **Generator:** ESP32 DAC on GPIO25 through a 75 Ω series resistor,
  commanded over USB serial by the host tools.
- **CH2, reference:** a tee at the TBC's input, so the analysis sees exactly
  what was sent.
- **TBC under test,** then a monitor and vectorscope on loop-through for a
  human check.
- **CH1, result:** the end of the chain, terminated in 75 Ω.
- **Analysis:** `tools/observe_tbc.py` reads the manifest plus the CH1 (and
  CH2) capture. The grader will score from its output (see Scoring).

**One-channel rigs are supported.** With `--channels 1` only CH1 is captured,
so a single-channel scope and no tee are enough. Everything that depends on
the output alone still works: field classes, the frame markers, lost-field
tolerance, the fault hunt and the dropout watch. What's lost is the input
check, latency, and gain against the real input. Picture levels are then
judged against nominal values. Wiring is in the README.

The TBC re-times the signal, so its output is not sample-aligned with the
input. The analysis therefore compares CH1 against the *ideal* signal the
manifest describes, not against CH2 sample for sample. It uses the frame
marker to know which frame and test rung it's looking at. CH2's job is to
confirm that the input was what the manifest says, and to anchor latency and
gain.

## Signal conventions

"Nominal" means NTSC-M per SMPTE 170M as the generator produces it. Every
perturbation is stated as a change from that.

| Quantity | Convention |
|---|---|
| Sample clock | 4 × fsc = 14.318182 MHz; 1 sample = 69.84 ns; 910 samples per line |
| Horizontal time origin | 50 % point of the H-sync leading edge. In the generator's buffers, sample 16 (`LINE_ORIGIN`), leaving room for early shifts and slow edges |
| Time units in the manifest | ns for timing, samples where the DAC grid matters (both given) |
| Levels | IRE: sync −40, blanking 0, setup 7.5, white 100; 1 V p-p into 75 Ω nominal |
| Line numbers | SMPTE 1–525; field 1 = lines 1–263.5, field 2 = 263.5–525 |
| Picture rows | 0–485 top to bottom, interlaced (field 1 lines 21–263, field 2 lines 283–525) |
| Colour sequence | 4 fields (frames A and B). Each test states the frame its sequence starts on (`frame_start_mod4`), so dropped fields keep the subcarrier continuous |
| Subcarrier phase | Burst on −U (180°). Hue measured relative to burst, as a vectorscope does |
| Pattern | Each test runs over a stated pattern (default SMPTE bars), described as Y/U/V segments per row |
| Colour model | **Colour-under** (default, VHS/Betamax): sync, luma and the colour envelopes are disturbed, but the 3.58 MHz carrier stays on a continuous reference, as the deck's crystal-referenced colour processor delivers it. *Chroma follows timing* (direct colour, U-matic/1") is a switch, recorded in the manifest's `chroma` field |

## Test catalog

The catalog has 30 tests in 7 groups. Each test is tagged with the TBC class it
targets: line TBC, frame TBC/synchroniser, or both. Its manifest gives the
ideal output per class, so a line TBC that rightly passes a dropped field
through is not marked down. Each test sweeps one parameter up a severity
ladder, from rung 1 (mild) upward. `tests` on the serial console prints the
live catalog. If this table and the firmware ever disagree, the firmware
(`src/perturb.cpp`) is right.

### H — Horizontal timing (the core TBC job)

| ID | Test | Parameter | Ladder | Ideal TBC output |
|---|---|---|---|---|
| H1 | Random line-to-line jitter | rms ns (Gaussian, seeded) | 5, 10, 20, 50, 100, 200 | Straight vertical edges; output H jitter < 5 ns rms |
| H2 | Sinusoidal jitter (wow/flutter) | peak ns @ Hz | 50 ns @ 1 Hz … 2 µs @ 30 Hz, 6 rungs | As H1 at every rate |
| H3 | Timing step | µs, toggling every 0.5 s | 0.25, 0.5, 1, 2, 5 | Picture stays put; recovers within N lines (measured) |
| H4 | Head-switch skew | µs, lines 520–256 shifted until V-sync | 0.5, 1, 2, 5, 10 | No bend at the bottom of the picture |
| H5 | Displaced single line | samples, at lines 100 and 362 | ±2, ±4, ±10, ±20 | No tear; following lines unaffected |

H6, velocity error (timing that drifts *within* a line), is deferred to the
tape-deck phase. It needs pre-stretched line copies.

### F — Frequency

| ID | Test | Parameter | Ladder | Ideal TBC output |
|---|---|---|---|---|
| F1 | Line and subcarrier offset together | ppm (APLL retune) | ±10, ±30, ±100, ±300, ±1000 | Locks; output at nominal rate if genlocked |
| F2 | fH not locked to fsc | H rate against a fixed subcarrier, ppm (SCH drifts) | ±10, ±50, ±200, ±1000 | Correct hue; no SCH drift on output |
| F3 | Slow frequency drift | peak ppm over a 10 s period | 50, 300, 1000 | Continuous lock |

F2 and F3 are done in the timing model, not by retuning the clock.

### S — Sync and structure

| ID | Test | Parameter | Ladder | Ideal TBC output |
|---|---|---|---|---|
| S1 | Sync amplitude | sync depth, IRE | 30, 25, 20, 15, 10 | Locks; regenerated 40 IRE sync |
| S2 | Slow sync edges | 10–90 % rise, ns | 250, 500, 1000 | Locks; output edges nominal |
| S3 | Missing H syncs | consecutive lines with no sync, from line 100 | 1, 2, 5, 10, 20 | No tear; lines regenerated |
| S4 | Malformed vertical interval | variant | 1 EQ pulses as H sync · 2 unserrated V-sync · 3 both · 4/5 V-sync missing in field 1 every 30/6 frames · 6 V-sync missing in every field | Stable vertical lock |
| S5 | Progressive 240p | 262 lines, no half-line | 1 rung | Passes or flags; no roll |
| S6 | Wrong line count per field | lines removed/repeated at lines 200 and 462 | −1, +1, −3, +3, −10, +10 | Stable output with a standard line count |
| S7 | Dropped field | field 2 missing every N frames | 60, 30, 15, 6, 2 | Standard 262.5-line fields out; repeats a field cleanly, no tear or roll |
| S8 | Field-length wow | lines added/removed per field, peak @ Hz | 1@0.5, 2@1, 5@1, 10@2, 20@2 | Standard fields out; drops/repeats allowed, no roll |
| S9 | Search noise bar | height of a band of sync-less snow moving 23 lines/frame | 2, 5, 10, 20, 40 | Flywheels through the bar; stable lock, no roll |
| S10 | Lost fields (no signal) | consecutive fields of 0 V, once a second | 1 … 11 (11 = "∞, more than 10") | Holds or blanks through the gap; never falls back to its own pattern; relocks at once |
| S11 | Lost fields (snow) | consecutive fields of sync-less snow, once a second | 1 … 11 | As S10 |

### C — Burst and colour

| ID | Test | Parameter | Ladder | Ideal TBC output |
|---|---|---|---|---|
| C1 | Burst amplitude | burst p-p, IRE (40 nominal) | 30, 20, 10, 60 | Correct saturation (ACC) |
| C2 | No burst | burst removed | 1 rung | Monochrome output; no colour noise |
| C3 | Burst + chroma phase jitter | rms degrees, line to line | 2, 5, 10, 20 | Stable hue |
| C4 | Static hue offset | picture hue against burst, degrees | 5, 10, 20, 45 | Passes unchanged (proc amp off) |

### L — Levels, noise, dropouts

| ID | Test | Parameter | Ladder | Ideal TBC output |
|---|---|---|---|---|
| L1 | Dropouts | snow over part of a line: µs × count per field | 2×1, 5×2, 10×5, 20×5, 50×10 | Concealed (needs a DOC input) or passed |
| L2 | Additive noise | SNR, dB (100 IRE / rms, unweighted) | 40, 34, 28, 22, 16 (DAC floor ~45 dB) | Locks; noise passed, not amplified |
| L3 | Hum / DC wander | 60 Hz sine on video, IRE p-p | 5, 10, 20, 40 | Clamped flat |
| L4 | Video level | white level, % | 80, 90, 110, 120 | Passes, or corrected if the TBC has AGC |

C3, L2 and L3 reuse each line's pre-rendered copies as phase, noise or DC
versions, so their resolution is `subsample_steps` levels (4 with SMPTE bars).
The manifest records each variant's value.

### V — Verification

| ID | Test | Parameter | Ladder | Ideal TBC output |
|---|---|---|---|---|
| V0 | Clean baseline | none | 1 rung | Identical to the input; sets the floor for every other figure |
| V1 | No signal | none | 1 rung | Captures the TBC's own fallback output, proving the analysis never mistakes it for ours |

### X — Fault hunt

| ID | Test | Parameter | Ladder | Ideal TBC output |
|---|---|---|---|---|
| X1 | Stress then recovery: a 90-frame (3 s) cycle of 1 s clean, 1 s stress, 1 s clean | stress type | 1 jitter 200 ns rms · 2 dropped fields · 3 lost fields · 4 noise bar + jitter · 5 bad-tape mix | No stuck or blended fields; clean again within 3 frames of each burst |

X1 targets the faults reported for newer "black" AVT-8710 units: stuck
fields, random dropped and repeated frames, ghosting, and a "ghost mode" that
persists after bad tape. The output is split by input frame into before,
stress, settling and after windows. A fault that outlasts the stress shows up
in the "after" window.

## What the analysis measures today

`tools/observe_tbc.py` observes; it does not grade. For each run it reports:

- **Output timing:** H-sync count, line period, line jitter, field lengths
  (262.5 lines is standard).
- **Field classes.** Every complete output field is one of:
  - ours (carries our marker, so its frame number is known);
  - ours, unmarked (our picture, but the marker line was lost);
  - blended (our marker with in-between cells: two frames mixed);
  - blank;
  - garbled (matches neither ours nor a uniform pattern);
  - the TBC's own pattern (fallback bars, for example).

  Frames whose two fields come from different input frames are counted as
  stuck or torn.
- **Frame sequence** from the markers: back-steps, skipped and repeated
  frames, single-field frames, and the marker's line and offset after the
  output's V-sync (vertical position and residual H error).
- **Picture figures** on our own fields only: burst, yellow hue and chroma,
  gray level, blanking wander, noise. They're scaled from the output's own sync
  depth, so termination errors don't skew them. **A TBC that falls back to its
  own bars can never pass as our signal.**
- **Output dead:** no vertical sync on CH1. Such runs are flagged and left out
  of tolerance figures. `tools/dropout_watch.py` samples the output once a
  second over minutes to catch these episodes.
- **Loss and hunt summaries:** per S10/S11 rung, whether the gap was handled
  (no fallback, no sync loss), clean, and how (held, blanked, garbled on
  recovery). A suite gives the lost-field tolerance. Per X1 rung, the four
  windows.
- **With CH2 (two-channel runs):**
  - **Input check:** markers decoded in order, and no generator DMA restart.
    V1 must show no signal on CH2.
  - **Latency:** the same field (frame and parity) on both channels.
  - **Output line rate against the input:** 0 ppm means the TBC locks its
    output to its input. A free-running frame synchroniser shows an offset,
    a latency that drifts, and a dropped or repeated frame each time it
    wraps.
  - **Gain against the real input:** burst, chroma and gray ratios, and the
    hue shift.
  - **Output level** (sync depth in volts), to catch a sagging output.

## Scoring (proposed, not yet implemented)

A test's grade is the highest rung at which the TBC still meets every pass
threshold. The overall grade is a weighted roll-up of those rungs. The H group
weighs most, because re-timing is the TBC's core job.

| Metric | How it is measured | Proposed pass threshold |
|---|---|---|
| Residual horizontal displacement | Position of the bar edges per line vs the ideal, after removing a fixed offset | < 10 ns rms, < 35 ns peak |
| Correction ratio | Input displacement rms ÷ output displacement rms (input from CH2 when present) | Reported in dB, not thresholded |
| Output H stability | Line-period jitter of the output's own H sync | < 5 ns rms |
| Lock integrity | Lines with missing or misplaced sync; field-sequence breaks; frame-marker gaps | 0 |
| Recovery time | Lines from a step event until displacement is under threshold | ≤ 3 lines (H3), ≤ 1 field (S4, S6) |
| Hue error | Per bar vs burst, vectorscope style | < 3° |
| Saturation error | Chroma amplitude per bar vs the input (CH2) or ideal | < 5 % |
| Luma error | Per bar or step vs the input (CH2) or ideal | < 2 IRE |
| Burst | Amplitude and phase stability on the output | 40 ± 2 IRE p-p, < 1° rms |
| Fallback / dead output | Fields of the TBC's own pattern; runs with no output sync | 0; any is a failure of that rung |
| Lost-field tolerance | Largest N of S10/S11 handled | Reported (∞ above 10), not thresholded |

Rules:

- **V0 runs first in every session.** Its measured errors are the floor. If
  V0 itself fails a threshold, or (with CH2) its input check fails, the rig is
  at fault, not the TBC, and the session stops.
- **Only fields carrying our marker are measured.** A field of the TBC's own
  pattern scores as a failure, never as a pass.
- **Single-channel runs** are scored against the ideal levels in the
  manifest. Two-channel runs use CH2 for gain, since the generator's real
  output differs from nominal (see bench notes: chroma about 0.68× today).
- Thresholds and group weights are first proposals. They need checking
  against a known-good TBC.

## Grader contract

Each run gives the grader three things:

- a JSON manifest;
- the capture: CH1, plus CH2 on two-channel rigs;
- a frame marker inside the video itself.

The manifest says exactly what was sent and what the ideal output is. It is
enough to rebuild the ideal waveform without the generator.

**Frame marker.** Lines 24 (field 1) and 287 (field 2), the picture's top rows,
carry 36 cells of 1.4 µs starting 10.5 µs after the sync edge, at 7.5 or
70 IRE. The cells are:

| Cells | Content |
|---|---|
| 2 | start, "10" |
| 8 | test code |
| 4 | rung |
| 16 | frame counter |
| 4 | CRC-4 |
| 2 | stop, "01" |

It lets the grader match any captured field to its manifest entry, whatever
the TBC's delay. It sits in the picture for two reasons. TBCs commonly blank
the vertical interval; the AVT-8710 blanks lines 10–21. And the picture is
above every line the structure tests add, remove or skew. Lost lines take
priority over the marker. A decoded marker counts only if it names the run's
own test and rung, because snow occasionally passes the 4-bit CRC by chance.

**Determinism.** Every perturbation is a pure function of the absolute line
number n = frame × 525 + line index, so sequences never loop. Random jitter
uses a seeded hash (lowbias32) summed Irwin-Hall style, which is
integer-exact. Periodic tests use an integer phase increment. F2's
quantization uses exact integer arithmetic. `tools/perturb_model.py` is the
reference implementation for the grader. `tools/verify_timing.py` checks the
firmware against it in 47 configurations: shifts agree to 0.002 ns, and every
line length matches.

**Manifest example**, trimmed from a real run (H1, rung 4):

```json
{
  "schema": "tbc-test/1",
  "generator": {"firmware": "ntsc-gen 0.2.0", "sample_hz": 14318186.442,
                "nominal_sample_hz": 14318181.818, "clock_ppm_base": 0.0,
                "levels": {"sync_code": 0.0, "white_code": 73.0},
                "subsample_steps": 2, "max_ext_samples": 10},
  "test": {"id": "H1", "code": 17, "name": "random line jitter", "rung": 4, "rungs": 6,
           "params": {"rms_ns": 50}, "seed": 1, "model": "perturb/1", "phase_inc": 0,
           "chroma": "colour_under", "targets": ["line_tbc", "frame_tbc"]},
  "ideal": {"pattern": "unperturbed",
            "line_tbc": "timing corrected; vertical edges straight, picture steady",
            "frame_tbc": "timing corrected; vertical edges straight, picture steady"},
  "timing": {"samples_per_line": 910, "lines_per_frame": 525, "half_line_samples": 456,
             "loop": "none (frame counter)", "time_origin": "50% of H-sync leading edge",
             "structure": "perturb/1 frame_slots"},
  "marker": {"lines": [24, 287], "cells": 36, "cell_us": 1.4, "start_us": 10.5,
             "lo_ire": 7.5, "hi_ire": 70.0,
             "layout": "start 10, code 8, rung 4, frame 16, crc4 (poly 0x3 init 0, 28 bits MSB first), stop 01"},
  "pattern": {"name": "smpte_bars", "regions": [{"rows": [0, 323], "key": 0,
              "segments": [{"x0": 0.0, "x1": 0.142857, "y0": 76.875, "y1": 76.875, "u": 0.0, "v": 0.0}]}]},
  "capture": {"rate_hz": 50000000.0, "samples": 20000000, "volts": "raw * scale + offset",
              "channels": {"ch1": "result (end of chain, after TBC)", "ch2": "reference (generator output)"},
              "started_unix": 1792015263.6, "frames_at_start": 62, "dma_restarts_during": 0},
  "dut": {"name": "Datavideo TBC-1000", "notes": "composite in/out"},
  "generator_state": {"frames": 62, "dma_restarts": 0, "...": "the generator's info at capture time"}
}
```

The example shows one bar of one region; real manifests list every region
and segment. The `capture`, `dut` and `generator_state` blocks are added by
the host runner, which saves the manifest as `manifest.json` next to
`capture.npz`. The capture holds raw int16 samples per channel (`ch1`, and
`ch2` when captured), plus `scale`, `offset` and `rate`. A single-channel
run's `capture.channels` lists CH1 only. Positions are fractions of the
active width, and levels are IRE. The encoding is the same Y/U/V segment
model the firmware renders from.

## Generator design

- **DMA ring:** two halves of up to 600 descriptors each. Each half holds one
  frame of any length, so frames can drop fields or change line count.
- **Frame task:** an end-of-frame interrupt wakes it to rewrite the half that
  just finished, as "the other half's frame + 1", while the other half
  plays. That takes 6–9 ms of a 33 ms frame. A watchdog restarts the DMA if no
  end-of-frame arrives for 200 ms; restarts are counted, and runs that see one
  are flagged.
- **Timing faults are in the ring, not the picture.** A descriptor's length
  sets where the next line's sync falls, in 2-sample (140 ns) steps.
  Pre-rendered sub-sample-shifted copies of each line fill in the fine steps.
- **Pools:** variant copies (timing, carrier, noise and DC), snow, dropout and
  dead (0 V) lines. Memory is the binding constraint, about 189 KB free with
  everything allocated.
- **Test switches** park the ring on a standard 525-line black frame with full
  vertical sync while the new test is built. An earlier version parked on
  sync-only lines with no V-sync, which could provoke the TBC under test.

### Phases

1. **Phase 0, rig calibration (in progress):** CH2 wired 2026-10-14. Still to
   do: terminate CH1, set `levels` to 286 / 714 mV, find and correct the
   chroma roll-off, trim `ppm`.
2. **Phase 1, timing (done 2026-10-13):** descriptor-length control, frame
   task, frame marker, manifest, host capture runner. V0, H1–H5, F1.
3. **Phase 2, structure and colour (done 2026-10-13):** S1–S9, C1–C4, F2,
   F3.
4. **Phase 3, levels and noise (done 2026-10-13):** L1–L4, the suite runner
   and HTML reports.
5. **Phase 4, fault tests (done 2026-10-13/14):** S10, S11, V1 and X1, the
   dropout watch, suite comparison, CH2 input check and latency,
   single-channel mode.
6. **Next:** scoring (above), H6 velocity error, and a tape program mode
   that steps through rungs on a timed schedule for recording test tapes,
   for tape-based TBCs with no passthrough.

## Open questions

- [ ] **Genlock.** If a TBC takes a reference input, the ESP32 can output
      black burst on GPIO26 (DAC2) from the same DMA stream. That makes
      "output at nominal rate" testable. The TBC-1000 free-runs at about
      +22 ppm against its input.
- [ ] **Pass thresholds and group weights** in Scoring are first proposals.
      They need checking against a known-good TBC.
- [ ] **Generator chroma level.** CH2 reads chroma and burst at about 0.68× of
      nominal. Is it the DAC, the cable or the measurement? Pre-emphasis in
      the renderer would give single-channel rigs a nominal input.
- [ ] **TBC-1000 dead output.** Its output intermittently dies, the swing
      sagging first. A power-cycle cleared it. Trigger and cause are open
      (bench notes).
- [x] **Vertical interval:** TBCs blank it (the AVT-8710 blanks lines 10–21),
      so the marker moved to picture lines 24/287.
- [x] **Jitter model:** colour-under is the default (VHS/Betamax). Direct
      colour stays as an option.
- [x] **Grader language:** Python with NumPy, reading `capture.npz` and
      `manifest.json`, as the existing tools do.
- [x] **Capture length per rung:** slow tests capture longer automatically
      (S10/S11 1.3 s at 25 MS/s, X1 3.3 s at 12.5 MS/s; `CAPTURE_HINTS` in
      `tools/run_test.py`).
- [x] **One-channel rigs:** supported with `--channels 1`.
