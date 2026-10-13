"""ADP2230 scope capture via the WaveForms SDK.

capture() records both channels simultaneously, downloads raw 16-bit samples
in chunks (a 64 M-sample capture as doubles would need 1 GB), and returns
them with the raw -> volts scale fitted from a short double-precision read.
"""
import time
from ctypes import byref, c_byte, c_double, c_int, c_short, cdll

import numpy as np

dwf = cdll.LoadLibrary("/Library/Frameworks/dwf.framework/dwf")
CHUNK = 1 << 20


def capture(rate=50e6, seconds=0.25, vrange=5.0, settle=2.0):
    """Returns dict(raw=int16[2, n], scale=[2], offset=[2], rate=float).

    volts = raw * scale[ch] + offset[ch]
    """
    h = c_int()
    dwf.FDwfDeviceOpen(c_int(-1), byref(h))
    if h.value == 0:
        raise RuntimeError("could not open the ADP2230")
    try:
        bmin, bmax = c_int(), c_int()
        dwf.FDwfAnalogInBufferSizeInfo(h, byref(bmin), byref(bmax))
        n = min(int(rate * seconds), bmax.value)
        dwf.FDwfAnalogInFrequencySet(h, c_double(rate))
        dwf.FDwfAnalogInBufferSizeSet(h, c_int(n))
        for ch in (0, 1):
            dwf.FDwfAnalogInChannelEnableSet(h, c_int(ch), c_int(1))
            dwf.FDwfAnalogInChannelRangeSet(h, c_int(ch), c_double(vrange))
            dwf.FDwfAnalogInChannelOffsetSet(h, c_int(ch), c_double(0.0))
        dwf.FDwfAnalogInTriggerSourceSet(h, c_byte(0))  # free-running
        time.sleep(settle)  # input stage settles after a range change
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
            else:
                scale[ch], offset[ch] = vrange / 65536, v.mean()
        return {"raw": raw, "scale": scale, "offset": offset, "rate": real.value}
    finally:
        dwf.FDwfDeviceClose(h)


def volts(cap, ch):
    return cap["raw"][ch].astype(np.float32) * np.float32(cap["scale"][ch]) + np.float32(cap["offset"][ch])
