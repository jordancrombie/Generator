"""Talk to the generator over serial.

usage: gen_cmd.py "p staircase" ["info" ...]

The port is auto-detected (first /dev/cu.wchusbserial*), or set GEN_PORT.
Other tools import open_gen() and request() from here.
"""
import glob, os, sys, time

import serial

BAUD = 921600  # must match Serial.begin() in src/main.cpp


def port():
    p = os.environ.get("GEN_PORT") or next(iter(sorted(glob.glob("/dev/cu.wchusbserial*"))), None)
    if not p:
        sys.exit("no generator serial port found")
    return p


def open_gen():
    s = serial.Serial(port(), BAUD, timeout=0)
    time.sleep(0.2)
    s.reset_input_buffer()
    return s


def request(s, cmd, until=None, timeout=10.0, quiet=0.3):
    """Send `cmd` and return the reply text.

    Returns as soon as `until` appears in the reply, or, with until=None, once
    the line has been quiet for `quiet` seconds. Reads only what has arrived,
    so it never blocks waiting for a full buffer.
    """
    s.reset_input_buffer()
    s.write((cmd + "\n").encode())
    buf, t0, last = b"", time.time(), time.time()
    while time.time() - t0 < timeout:
        n = s.in_waiting
        if n:
            buf += s.read(n)
            last = time.time()
            if until is not None and until.encode() in buf:
                break
        elif until is None and buf and time.time() - last > quiet:
            break
        else:
            time.sleep(0.002)
    return buf.decode(errors="replace")


def dump_line(s, idx):
    """DAC codes of ring line `idx` as played (numpy float array)."""
    import numpy as np
    lines = request(s, f"dump {idx}", until="end").splitlines()
    i = next(k for k, l in enumerate(lines) if l.startswith(f"line {idx} "))
    n = int(lines[i].split("n=")[1])
    hexs = "".join(l.strip() for l in lines[i + 1:] if l.strip() != "end")[: 2 * n]
    return np.array([int(hexs[j:j + 2], 16) for j in range(0, len(hexs), 2)], float)


if __name__ == "__main__":
    s = open_gen()
    for c in sys.argv[1:]:
        print(request(s, c).strip())
