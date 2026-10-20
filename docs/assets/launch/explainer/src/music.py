"""Synthesise the explainer's music bed: soft pads, a plucked arpeggio, a light pulse and one bell per
layer reveal, all from code. Deterministic (seeded noise). Needs only numpy.

    python music.py out.wav [timing.json]

With timing.json (written by narrate.py), the accents follow the narrated timing: every event time
below is written on the original 112 s timeline and mapped through the same warp the picture uses.
"""
import json
import sys
import wave

import numpy as np

SR = 44100
TIMING = json.load(open(sys.argv[2])) if len(sys.argv) > 2 else None
DUR = TIMING["duration"] if TIMING else 112.0
_ANCH = TIMING["warp"] if TIMING else [[0, 0], [112, 112]]


def W(old):
    """Map a time on the original 112 s timeline to the narrated timeline."""
    for (a0, b0), (a1, b1) in zip(_ANCH, _ANCH[1:]):
        if old <= a1:
            return b0 + (old - a0) * (b1 - b0) / (a1 - a0) if a1 > a0 else b0
    return _ANCH[-1][1] + old - _ANCH[-1][0]

BPM = 96
BEAT = 60 / BPM
BAR = 4 * BEAT
N = int(SR * DUR)
rng = np.random.default_rng(7)

L = np.zeros(N)
R = np.zeros(N)


def hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def add(sig, start, gain=1.0, pan=0.0):
    i = int(start * SR)
    if i >= N:
        return
    sig = sig[: N - i]
    L[i : i + len(sig)] += sig * gain * np.sqrt(0.5 * (1 - pan))
    R[i : i + len(sig)] += sig * gain * np.sqrt(0.5 * (1 + pan))


def env(n, attack, release):
    e = np.ones(n)
    a = max(1, int(attack * SR))
    r = max(1, int(release * SR))
    e[:a] = np.linspace(0, 1, a) ** 2
    e[-r:] *= np.linspace(1, 0, r) ** 2
    return e


def pad_note(f, dur):
    n = int(dur * SR)
    t = np.arange(n) / SR
    s = np.zeros(n)
    for det in (-0.12, 0.0, 0.11):          # three detuned voices
        ff = f * 2 ** (det / 12)
        for h in range(1, 7):               # soft saw: few harmonics, steep roll-off
            s += np.sin(2 * np.pi * ff * h * t + det * h) / h ** 1.8
    trem = 1 + 0.06 * np.sin(2 * np.pi * 0.21 * t)
    return s * trem * env(n, 1.1, 1.6) / 9


def pluck(f, dur=1.4):
    n = int(dur * SR)
    t = np.arange(n) / SR
    s = np.sin(2 * np.pi * f * t) + 0.28 * np.sin(2 * np.pi * 2 * f * t) + 0.08 * np.sin(2 * np.pi * 3 * f * t)
    return s * np.exp(-t / 0.42) * env(n, 0.004, 0.05)


def bell(f, dur=3.0):
    n = int(dur * SR)
    t = np.arange(n) / SR
    s = np.zeros(n)
    for ratio, amp, dec in ((1, 1, 1.4), (2.76, 0.35, 0.6), (5.4, 0.15, 0.3), (2.0, 0.25, 1.0)):
        s += amp * np.sin(2 * np.pi * f * ratio * t) * np.exp(-t / dec)
    return s * env(n, 0.003, 0.2)


def kick():
    n = int(0.45 * SR)
    t = np.arange(n) / SR
    f = 44 + 80 * np.exp(-t / 0.035)
    ph = 2 * np.pi * np.cumsum(f) / SR
    return np.sin(ph) * np.exp(-t / 0.16)


def tick():
    n = int(0.08 * SR)
    t = np.arange(n) / SR
    x = rng.standard_normal(n)
    x = np.diff(np.concatenate([[0], x]))   # crude high-pass
    return x * np.exp(-t / 0.018)


# D major colour: Dmaj9, Bm9, Gmaj9, A6sus — two bars each.
CHORDS = [
    [50, 57, 61, 64, 66],   # D A C# E F#
    [47, 54, 57, 61, 62],   # B F# A C# D
    [43, 50, 54, 57, 62],   # G D F# A D
    [45, 52, 57, 59, 64],   # A E A B E
]
ARP = [
    [74, 69, 73, 76, 78, 76, 73, 69],
    [71, 66, 69, 73, 74, 73, 69, 66],
    [67, 74, 78, 81, 79, 78, 74, 71],
    [69, 76, 81, 83, 81, 76, 73, 71],
]

nbars = int(DUR / BAR) + 1
for b in range(0, nbars, 2):
    start = b * BAR
    if start > DUR - 3:
        break
    ci = (b // 2) % 4
    for m in CHORDS[ci]:
        add(pad_note(hz(m), 2 * BAR + 1.2), start, 0.20, pan=((m % 5) - 2) * 0.15)
    for half in range(2):
        for k, m in enumerate(ARP[ci]):
            tt = start + half * BAR + k * (BEAT / 2)
            if tt < W(9.6) or tt > W(103.5):
                continue                     # arpeggio runs from the problem beat to the close
            g = 0.11 if W(20) <= tt < W(94) else 0.07
            add(pluck(hz(m)), tt, g, pan=0.35 if k % 2 else -0.35)

# light pulse while the question travels and the agent works
beat_t = W(20.0)
while beat_t < W(93.8):
    add(kick(), beat_t, 0.32)
    add(tick(), beat_t + BEAT / 2, 0.05, pan=0.2)
    beat_t += BEAT

# a bell for each layer the question reaches, and soft accents on the big moments
for tt, m in ((24.1, 74), (29.6, 76), (35.1, 78), (40.6, 81), (46.1, 83)):
    add(bell(hz(m)), W(tt), 0.16)
for tt, m in ((52.3, 86), (80.3, 81), (104.3, 74)):
    add(bell(hz(m), 4.0), W(tt), 0.14)
    add(bell(hz(m - 12), 4.0), W(tt), 0.10)

# final chord swell
for m in CHORDS[0] + [74]:
    add(pad_note(hz(m), DUR - W(103.8) + 0.5), W(103.8), 0.22)


def reverb(x, seed, seconds=2.6):
    n = int(seconds * SR)
    t = np.arange(n) / SR
    ir = np.random.default_rng(seed).standard_normal(n) * np.exp(-t / 0.55)
    ir[0] = 0
    size = 1 << int(np.ceil(np.log2(len(x) + n)))
    y = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[: len(x)]
    return y / np.sqrt(np.sum(ir ** 2))


wetL, wetR = reverb(L, 1), reverb(R, 2)
outL = L * 0.8 + wetL * 0.35
outR = R * 0.8 + wetR * 0.35

t = np.arange(N) / SR
master = np.clip(t / 1.5, 0, 1) * np.clip((DUR - t) / 2.5, 0, 1)
outL *= master
outR *= master
peak = max(np.abs(outL).max(), np.abs(outR).max())
scale = 0.7 / peak                          # about -3 dBFS peak; music sits under the captions
stereo = np.stack([outL * scale, outR * scale], axis=1)
pcm = (np.clip(stereo, -1, 1) * 32767).astype("<i2")

with wave.open(sys.argv[1] if len(sys.argv) > 1 else "music.wav", "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes(pcm.tobytes())
