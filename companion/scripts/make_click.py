"""A short confirmation click for a filed card.

Our own asset rather than a sourced one: no licence to track, and it can
be exactly as long as it needs to be. Scanning a box means hearing this
several hundred times in a row, so it is short, quiet, and low enough not
to be piercing -- a soft wood-block tap rather than a camera shutter.
"""
import math
import pathlib
import struct
import wave

RATE = 44100
# 38 ms. Long enough to register, short enough that back-to-back cards do
# not overlap into a rattle.
MS = 38
# Two partials a fifth apart read as "one deliberate tap" rather than a
# beep; a single sine at this length sounds like an error tone.
PARTIALS = ((880.0, 1.0), (1320.0, 0.45))
PEAK = 0.22


def main() -> None:
    n = int(RATE * MS / 1000)
    frames = bytearray()
    for i in range(n):
        t = i / RATE
        # Percussive: instant attack, exponential decay. A linear fade
        # out sounds like a fading beep, not like something being struck.
        env = math.exp(-t * 95.0)
        # A couple of milliseconds of ramp-in kills the DC click that an
        # instant start puts through a phone speaker.
        ramp = min(1.0, t / 0.002)
        sample = sum(math.sin(2 * math.pi * f * t) * a for f, a in PARTIALS)
        sample *= env * ramp * PEAK / sum(a for _, a in PARTIALS)
        frames += struct.pack('<h', int(max(-1.0, min(1.0, sample)) * 32767))

    out = pathlib.Path('assets/filed.wav')
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(frames))
    print(f'{out}: {out.stat().st_size} bytes, {MS} ms')


if __name__ == '__main__':
    main()
