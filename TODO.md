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
- [ ] **Design a test to search for the AVT-8710's bug(s).** Needs a
      description of the symptom (what goes wrong, when, how often), so the
      test can provoke it repeatably and the report can catch it.
- [ ] Explain L3: with hum on the input, blanking is clamped flat but the
      gray bar reads higher (84 / 95 IRE vs 79 at 20 / 40 IRE p-p hum).
- [ ] C2: no colour killer (passes chroma with a random hue when burst is
      missing). Confirm on the monitor.
- [ ] H4 (10 µs head-switch skew): timing fully corrected, but output hue
      wanders ±18.7° line to line (±1° elsewhere). Look at chroma phase
      across the head switch.
- [ ] S1 (10 IRE sync): the gray bar reads 106.8 IRE. Picture figures are
      scaled by the output's own sync depth, so a TBC that doesn't fully
      regenerate sync inflates them. Measure absolute volts once CH1 is
      terminated, and report output sync depth.

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
