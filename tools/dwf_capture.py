"""Capture both ADP2230 scope channels to a .npy file (free-running, no trigger).

usage: dwf_capture.py OUT.npy [rate_hz] [samples]
"""
import sys, time
import numpy as np
from ctypes import cdll, c_int, c_double, c_byte, byref

dwf = cdll.LoadLibrary("/Library/Frameworks/dwf.framework/dwf")
out = sys.argv[1]
rate = float(sys.argv[2]) if len(sys.argv) > 2 else 50e6
nreq = int(sys.argv[3]) if len(sys.argv) > 3 else 1 << 20

h = c_int()
dwf.FDwfDeviceOpen(c_int(-1), byref(h))
if h.value == 0:
    sys.exit("could not open device")
try:
    bmax = c_int(); dwf.FDwfAnalogInBufferSizeInfo(h, 0, byref(bmax))
    n = min(nreq, bmax.value)
    dwf.FDwfAnalogInFrequencySet(h, c_double(rate))
    dwf.FDwfAnalogInBufferSizeSet(h, c_int(n))
    for ch in (0, 1):
        dwf.FDwfAnalogInChannelEnableSet(h, c_int(ch), c_int(1))
        dwf.FDwfAnalogInChannelRangeSet(h, c_int(ch), c_double(5.0))
        dwf.FDwfAnalogInChannelOffsetSet(h, c_int(ch), c_double(0.0))
    dwf.FDwfAnalogInTriggerSourceSet(h, c_byte(0))  # none
    time.sleep(0.3)
    dwf.FDwfAnalogInConfigure(h, c_int(1), c_int(1))
    st = c_byte()
    while True:
        dwf.FDwfAnalogInStatus(h, c_int(1), byref(st))
        if st.value == 2:  # done
            break
        time.sleep(0.01)
    real = c_double(); dwf.FDwfAnalogInFrequencyGet(h, byref(real))
    data = np.zeros((2, n))
    for ch in (0, 1):
        buf = (c_double * n)()
        dwf.FDwfAnalogInStatusData(h, c_int(ch), buf, c_int(n))
        data[ch] = np.frombuffer(buf, dtype=np.float64)
    np.save(out, data)
    print(f"captured {n} samples x 2 ch at {real.value/1e6:.3f} MS/s -> {out}")
finally:
    dwf.FDwfDeviceClose(h)
