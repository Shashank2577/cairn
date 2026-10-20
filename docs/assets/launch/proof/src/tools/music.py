"""Synthesise the proof video's music bed (original, no samples): a warm pad, a soft plucked arpeggio and a
light pulse at 96 BPM, with lifts on the scene cuts. Deterministic: run it again and the file is identical.

    python3 tools/music.py assets/music.wav     (after tools/voice.py, which writes the timing)
"""
import sys
import wave

import json
from pathlib import Path

import numpy as np

SR = 44100
BPM = 96
BEAT = 60 / BPM
_T = json.loads((Path(__file__).resolve().parent.parent / "assets" / "timing.json").read_text())
DUR = round(_T["duration"] + 0.5, 2)
CUTS = [v[0] for k, v in sorted(_T["scenes"].items()) if k != "s1"]  # scene starts, from tools/voice.py

n = int(SR * DUR)
t = np.arange(n) / SR
mix_l = np.zeros(n)
mix_r = np.zeros(n)


def hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


# A minor - F - C - G, one chord per bar (4 beats)
CHORDS = [[57, 60, 64, 69], [53, 57, 60, 65], [48, 55, 60, 64], [55, 59, 62, 67]]
BAR = 4 * BEAT


def env(length, a, r):
    e = np.ones(length)
    ai, ri = min(int(a * SR), length // 2), min(int(r * SR), length // 2)
    e[:ai] = np.linspace(0, 1, ai)
    e[-ri:] *= np.linspace(1, 0, ri)
    return e


# Pad: detuned saws softened by a one-pole low-pass
bar = 0
while bar * BAR < DUR:
    start = int(bar * BAR * SR)
    length = min(int(BAR * SR * 1.15), n - start)
    if length <= 0:
        break
    tt = np.arange(length) / SR
    chord = CHORDS[bar % 4]
    voice = np.zeros(length)
    for m in chord:
        for det in (-0.08, 0.08):
            f = hz(m - 12) * 2 ** (det / 12)
            voice += 2 * ((tt * f) % 1.0) - 1
    y = np.zeros(length)
    a = 0.035
    acc = 0.0
    for i in range(length):  # one-pole low-pass
        acc += a * (voice[i] - acc)
        y[i] = acc
    y *= env(length, 1.2, 1.4) * 0.05
    mix_l[start:start + length] += y
    mix_r[start:start + length] += np.roll(y, 220)
    bar += 1

# Pluck arpeggio from the first cut on: chord tones in eighths, decaying sines with a soft second partial
step = BEAT / 2
k = 0
pattern = [0, 2, 1, 3, 2, 1, 3, 2]
while k * step < DUR - 2:
    st = k * step
    if st >= CUTS[0] - 0.01:
        chord = CHORDS[int(st // BAR) % 4]
        m = chord[pattern[k % 8]] + 12
        length = int(1.2 * SR)
        s0 = int(st * SR)
        length = min(length, n - s0)
        tt = np.arange(length) / SR
        y = (np.sin(2 * np.pi * hz(m) * tt) + 0.25 * np.sin(4 * np.pi * hz(m) * tt)) * np.exp(-tt * 5.5)
        y *= 0.06
        pan = 0.5 + 0.35 * np.sin(k * 0.7)
        mix_l[s0:s0 + length] += y * (1 - pan) * 2
        mix_r[s0:s0 + length] += y * pan * 2
    k += 1

# Soft pulse on each beat after the opening (sub thump), quieter in the close
b = 0
while b * BEAT < DUR - 3:
    st = b * BEAT
    if CUTS[0] <= st < CUTS[-1] + 6:
        s0 = int(st * SR)
        length = min(int(0.35 * SR), n - s0)
        tt = np.arange(length) / SR
        f = 55 + 60 * np.exp(-tt * 30)
        y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tt * 12) * (0.11 if b % 2 == 0 else 0.06)
        mix_l[s0:s0 + length] += y
        mix_r[s0:s0 + length] += y
    b += 1

# Lift on each cut: a short filtered-noise swell that lands on the cut
rng = np.random.default_rng(7)
for c in CUTS:
    length = int(0.9 * SR)
    s0 = int(c * SR) - length
    noise = rng.standard_normal(length)
    acc, y = 0.0, np.zeros(length)
    for i in range(length):
        acc += 0.06 * (noise[i] - acc)
        y[i] = acc
    y *= np.linspace(0, 1, length) ** 2 * 0.25
    mix_l[s0:s0 + length] += y
    mix_r[s0:s0 + length] += y

# Master: fade in/out, normalise to -3 dBFS peak
fade = np.ones(n)
fi, fo = int(1.0 * SR), int(3.0 * SR)
fade[:fi] = np.linspace(0, 1, fi)
fade[-fo:] = np.linspace(1, 0, fo)
stereo = np.stack([mix_l * fade, mix_r * fade], axis=1)
stereo = np.tanh(stereo * 1.4)
stereo /= np.max(np.abs(stereo)) / 0.7
pcm = (stereo * 32767).astype("<i2")
with wave.open(sys.argv[1], "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes(pcm.tobytes())
print("wrote", sys.argv[1], f"{DUR}s")
