# NTSC Test-Signal Generator (ESP32)

The generator side of a TBC test rig: an ESP32 makes NTSC composite video, the
signal passes through the time-base corrector under test, and an ADP2230
captures the result for a grader to compare against what was sent.

## How it works

- Classic ESP32 (D0WD). I2S0 runs in LCD/DAC mode and streams DMA straight to
  the internal 8-bit DAC on **GPIO25** at 4×fsc = 14.318182 MHz, clocked from
  the APLL.
- A static ring of 1050 DMA descriptors (two frames, the full 4-field colour
  sequence) points into a small pool of de-duplicated, pre-rendered line
  buffers. Once it's running the CPU does no work.
- Lines are rendered from a model (sync pulses, burst, active segments in
  Y/U/V IRE) with band-limited raised-cosine edges. Edges can sit at
  fractional sample positions.
- Timing follows SMPTE 170M: 4.7 µs H-sync, 2.3 µs equalizing pulses,
  serrated broad pulses, 9-cycle burst at 5.3 µs, 7.5 IRE setup, interlace
  half-lines at 263/283.

## Wiring (first light)

| ESP32  | Monitor composite in | ADP2230 |
|--------|----------------------|---------|
| GPIO25 | centre pin           | CH1 (1 MΩ, in parallel; let the monitor provide the 75 Ω termination) |
| GND    | shield               | GND     |

The default levels (`sync=0`, `white=73`) assume GPIO25 drives the monitor
directly. Measure the real terminated levels on the ADP2230 and adjust with
`levels`. A buffer amplifier and reconstruction low-pass filter will come
later for a cleaner signal.

## Build / flash

Use PlatformIO's own Python (Homebrew's `pio` runs on Python 3.14, which the
platform rejects):

```
~/.platformio/penv/bin/pio run -t upload --upload-port /dev/cu.wchusbserial210
```

## Serial commands (115200)

| command | |
|---|---|
| `list` | list patterns |
| `p <n\|name>` | select pattern: `smpte_bars`, `eia_bars_75`, `staircase`, `flat`, `crosshatch`, `black` |
| `flat <ire>` | flat field at the given IRE level |
| `levels <sync> <white>` | DAC codes for −40 and 100 IRE |
| `ppm <x>` | trim the sample clock (1.8 ppm steps), e.g. to correct crystal error |
| `info` | current state as JSON |
| `dump <i>` | DAC codes of chain line *i* as played |

## Tools

- `tools/check_lines.py` pulls lines back from the running generator and
  decodes them: sync and VBI pulse timing, burst amplitude and phase, and
  per-bar luma, chroma amplitude and hue.
- `tools/dwf_list.py` and `tools/dwf_capture.py` list the ADP2230 and capture
  from it through the WaveForms SDK.
