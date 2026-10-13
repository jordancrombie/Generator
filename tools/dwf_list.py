"""List Digilent WaveForms devices visible to the SDK."""
from ctypes import cdll, c_int, create_string_buffer, byref
dwf = cdll.LoadLibrary("/Library/Frameworks/dwf.framework/dwf")
v = create_string_buffer(32); dwf.FDwfGetVersion(v); print("WaveForms SDK", v.value.decode())
n = c_int(); dwf.FDwfEnum(c_int(0), byref(n)); print("devices:", n.value)
for i in range(n.value):
    name = create_string_buffer(64); sn = create_string_buffer(32); used = c_int()
    dwf.FDwfEnumDeviceName(c_int(i), name); dwf.FDwfEnumSN(c_int(i), sn); dwf.FDwfEnumDeviceIsOpened(c_int(i), byref(used))
    print(f"  [{i}] {name.value.decode()} SN={sn.value.decode()} in_use={bool(used.value)}")
