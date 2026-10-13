"""Send commands to the generator and print replies.  usage: gen_cmd.py "p staircase" ["info" ...]

The port is auto-detected (first /dev/cu.wchusbserial*), or set GEN_PORT.
"""
import glob, os, sys, time, serial

def port():
    p = os.environ.get("GEN_PORT") or next(iter(sorted(glob.glob("/dev/cu.wchusbserial*"))), None)
    if not p: sys.exit("no generator serial port found")
    return p

if __name__ == "__main__":
    s = serial.Serial(port(), 115200, timeout=0.3); time.sleep(0.2); s.reset_input_buffer()
    for c in sys.argv[1:]:
        s.write((c + "\n").encode()); time.sleep(0.6); print(s.read(8192).decode(errors="replace").strip())
