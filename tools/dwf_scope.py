"""ADP2230 scope capture via the WaveForms SDK.

Scope keeps the device open across captures. Its inputs need about 2 s to
settle after a range or offset change (see docs/bench-notes.md), so the
settle clock starts when Scope is opened and only the first capture waits for
it; callers can do other work (such as letting a TBC lock) in the meantime.

Captures record both channels simultaneously and download raw 16-bit samples
in chunks (a 64 M-sample capture as doubles would need 1 GB), with the
raw -> volts scale fitted from a short double-precision read.
"""
import time
from ctypes import byref, c_byte, c_double, c_int, c_short, cdll

import numpy as np

dwf = cdll.LoadLibrary("/Library/Frameworks/dwf.framework/dwf")
CHUNK = 1 << 20
SETTLE_S = 2.0  # input stage settling after a range/offset change


class Scope:
    def __init__(self, rate=50e6, seconds=0.25, vrange=5.0, settle=SETTLE_S):
        self.h = c_int()
        dwf.FDwfDeviceOpen(c_int(-1), byref(self.h))
        if self.h.value == 0:
            raise RuntimeError("could not open the ADP2230")
        bmin, bmax = c_int(), c_int()
        dwf.FDwfAnalogInBufferSizeInfo(self.h, byref(bmin), byref(bmax))
        self.max_samples = bmax.value
        self.vrange = vrange
        for ch in (0, 1):
            dwf.FDwfAnalogInChannelEnableSet(self.h, c_int(ch), c_int(1))
            dwf.FDwfAnalogInChannelRangeSet(self.h, c_int(ch), c_double(vrange))
            dwf.FDwfAnalogInChannelOffsetSet(self.h, c_int(ch), c_double(0.0))
        dwf.FDwfAnalogInTriggerSourceSet(self.h, c_byte(0))  # free-running
        self.ready_at = time.time() + settle
        self.set_timebase(rate, seconds)

    def set_timebase(self, rate, seconds):
        """Rate and length can change without re-settling the inputs."""
        self.n = min(int(rate * seconds), self.max_samples)
        dwf.FDwfAnalogInFrequencySet(self.h, c_double(rate))
        dwf.FDwfAnalogInBufferSizeSet(self.h, c_int(self.n))

    def capture(self):
        """Returns dict(raw=int16[2, n], scale=[2], offset=[2], rate=float).

        volts = raw * scale[ch] + offset[ch]
        """
        wait = self.ready_at - time.time()
        if wait > 0:
            time.sleep(wait)
        h, n = self.h, self.n
        dwf.FDwfAnalogInConfigure(h, c_int(1), c_int(1))
        st = c_byte()
        while True:
            dwf.FDwfAnalogInStatus(h, c_int(1), byref(st))
            if st.value == 2:
                break
            time.sleep(0.01)
        real = c_double()
        dwf.FDwfAnalogInFrequencyGet(h, byref(real))

        raw = np.empty((2, n), np.int16)
        scale, offset = np.zeros(2), np.zeros(2)
        for ch in (0, 1):
            for i in range(0, n, CHUNK):
                m = min(CHUNK, n - i)
                buf = (c_short * m)()
                dwf.FDwfAnalogInStatusData16(h, c_int(ch), buf, c_int(i), c_int(m))
                raw[ch, i:i + m] = np.frombuffer(buf, np.int16)
            m = min(65536, n)
            dbuf = (c_double * m)()
            dwf.FDwfAnalogInStatusData(h, c_int(ch), dbuf, c_int(m))
            v = np.frombuffer(dbuf, np.float64)
            r = raw[ch, :m].astype(float)
            if np.ptp(r) > 0:
                scale[ch], offset[ch] = np.polyfit(r, v, 1)
            else:  # flat input: no slope to fit
                scale[ch], offset[ch] = self.vrange / 65536, v.mean()
        return {"raw": raw, "scale": scale, "offset": offset, "rate": real.value}

    def close(self):
        if self.h.value:
            dwf.FDwfDeviceClose(self.h)
            self.h = c_int()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def capture(rate=50e6, seconds=0.25, vrange=5.0, settle=SETTLE_S):
    """One-shot capture (opens, settles, captures, closes)."""
    with Scope(rate, seconds, vrange, settle) as s:
        return s.capture()


def volts(cap, ch):
    return cap["raw"][ch].astype(np.float32) * np.float32(cap["scale"][ch]) + np.float32(cap["offset"][ch])
