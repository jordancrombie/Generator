# TODO

Open work for the generator side of the TBC test rig, roughly in priority
order. Measured facts and open issues live in
[docs/bench-notes.md](docs/bench-notes.md).

## Needs the BNC tees and 75 Ω terminator
- [ ] Wire CH2 (generator output) and terminate CH1 (`docs/wiring.svg`).
- [ ] Calibrate `levels` on CH2 to 286 mV sync / 714 mV white. Leave
      headroom below the sync tip (sync at code ~15–20), so S1 can go deeper
      than 40 IRE and L2 noise isn't clipped.
- [ ] Measure the input side of every test on CH2 and check it against the
      model (jitter actually injected, levels, burst, noise).
- [ ] Latency in frames: the frame marker on CH2 vs CH1.
- [ ] F1: confirm on the scope that the sample clock really moves by the
      commanded ppm.
- [ ] Re-baseline V0 per device once the chain is terminated.

## AVT-8710
- [x] Research the "black unit" faults and build a test for them (X1,
      2026-10-13; see bench notes).
- [ ] Run X1 on a known-good unit (a green one) and a suspect black one, to
      set what "buggy" looks like in the numbers (ghosted fields per minute,
      stuck fields without field loss, anything in the "after" window).
- [ ] A longer X1 (minutes of bad-tape stress) to provoke "ghost mode",
      which forum reports say persists until a power-cycle; plus a warm-up
      run, since some reports point at heat.
- [ ] Explain L3: with hum on the input, blanking is clamped flat but the
      gray bar reads higher (84 / 95 IRE vs 79 at 20 / 40 IRE p-p hum).
- [ ] C2: no colour killer (passes chroma with a random hue when burst is
      missing). Confirm on the monitor.
- [ ] H4 (10 µs head-switch skew): output hue wanders ±19° line to line on
      **both** the AVT-8710 and the TBC-1000, so it's probably inherent to
      colour-under timing jumps, not a fault. Confirm, then document it as
      expected.
- [ ] S1 (10 IRE sync): the gray bar reads 106.8 IRE. Picture figures are
      scaled by the output's own sync depth, so a TBC that doesn't fully
      regenerate sync inflates them. Measure absolute volts once CH1 is
      terminated, and report output sync depth.

## Housekeeping
- [ ] **Regenerate the sample report** (`docs/sample-report.html` and its
      screenshot `docs/sample-report.jpg`) whenever tests are added or
      changed, or the report layout changes. Use the command in the README's
      "Sample report" section, with any new tests added to its list. Copy
      the new file from `reports/` to `docs/sample-report.html`, then
      re-render the screenshot:
      `qlmanage -t -s 1400 -o /tmp docs/sample-report.html` and
      `sips -s format jpeg -s formatOptions 80 /tmp/sample-report.html.png --out docs/sample-report.jpg`.

## Datavideo TBC-1000
- [x] **Output dropout:** built `tools/dropout_watch.py` (2026-10-13).
- [ ] Output sags then dies (see bench notes). A power-cycle cleared it,
      and 10 suites after that (45 minutes) stayed clean. Next: a long
      S4:6 / S11:2 watch (an hour or more, unit warm) to see if it comes
      back with no test switches; check the supply rail and the unit's
      temperature if it does. Suites now record the output sync depth
      (`level` in the JSON; shown by `compare_suites.py`). Add a
      "swing trend" warning to `dropout_watch.py`.
- [ ] Run longer watches with different stimuli (S10:1–11, S11, X1 stresses)
      to pin down the trigger and the rate; run the same watch on the
      AVT-8710 for comparison.
- [ ] Re-run its suite once the chain is terminated, ideally after a check
      that the output is alive before each test. Today's tolerances varied
      between runs (8 vs 11).

## Generator
- [ ] Root-cause the intermittent DMA stop (watchdog recovers it; see
      bench notes).
- [ ] H6 velocity error: timing that drifts *within* a line (needs
      pre-stretched line copies). For line TBCs and the tape-deck phase.
- [ ] Tape program mode: step through test rungs on a timed schedule while
      recording a test tape, logging each segment's manifest; a V0 segment
      per tape.
- [ ] More variant levels for C3/L2/L3 when memory allows (now
      `subsample_steps`, 4 with SMPTE bars).

## Grader
- [ ] Scoring per the design doc (thresholds, severity ladders, per-class
      ideals). `tools/perturb_model.py` is its reference for what was sent;
      `observe_tbc.analyze()` already extracts most of the output figures.
