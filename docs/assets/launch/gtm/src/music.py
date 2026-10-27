"""The film's score, synthesized with numpy only: python3 music.py  ->  music.wav (44.1 kHz stereo).

Reads timing.json (from narration.py) so the sections land on the picture's scene changes. 96 BPM:
  problem   A minor, Am F Dm E. Dark detuned pad, a soft bass note per bar, a sparse two-note motif,
            a low pulse and a quiet shaker that both grow as the problems pile up.
  Cairn     opens on a C major downbeat when the paper wipes in. C G Am F, brighter pad, bass on
            1 and 3, a plucked eighth-note arpeggio; kick and hi-hat enter with the five layers. The
            drums drop out for the bar before the 369x reveal and come back on it.
  install   builds: four-on-the-floor kick, sixteenth shaker, driving eighth bass, the arpeggio
            doubled an octave up and the pad lifted.
  end card  a held Cadd9 with a rising arpeggio and a long reverb tail.
Deterministic: all noise is seeded.
"""
import json
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SR = 44100
BEAT = 60.0 / 96
BAR = 4 * BEAT
rng = np.random.default_rng(96)

timing = json.loads((HERE / "timing.json").read_text()) if (HERE / "timing.json").exists() else None
if timing:
    _new, _old = zip(*timing["knots"])
    DUR = timing["duration"]
    W = lambda t: float(np.interp(t, _old, _new))  # original film time -> new film time
else:
    DUR, W = 57.0, (lambda t: t)

PROBLEM_END = W(14.9)
OPEN = W(15.3)
DRUMS_IN = W(18.4)
REVEAL_PULL, REVEAL = W(32.9), W(34.0)
BUILD = W(42.8)
END = W(51.4)

N = int(DUR * SR) + 1
dry = np.zeros((N, 2))
wet = np.zeros((N, 2))  # reverb send


def hz(note):
    names = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}
    return 440.0 * 2 ** ((names[note[:-1]] + 12 * (int(note[-1]) + 1) - 69) / 12)


def place(sig, t0, gain=1.0, pan=0.0, send=0.0):
    """sig: mono or (n, 2). pan -1..1 for mono."""
    i = int(round(t0 * SR))
    if i >= N or i < 0:
        return
    if sig.ndim == 1:
        sig = np.stack([sig * np.sqrt(0.5 * (1 - pan)), sig * np.sqrt(0.5 * (1 + pan))], axis=1) * np.sqrt(2)
    sig = sig[: N - i] * gain
    dry[i : i + len(sig)] += sig
    if send:
        wet[i : i + len(sig)] += sig * send


def envelope(n, attack, release, sustain=1.0, decay=0.0):
    e = np.full(n, sustain)
    na, nd, nr = max(1, int(attack * SR)), int(decay * SR), max(1, int(release * SR))
    e[:na] = np.linspace(0, 1, na) ** 2
    if nd:
        e[na : na + nd] = np.linspace(1, sustain, len(e[na : na + nd]))
    e[-nr:] *= np.linspace(1, 0, nr) ** 1.6
    return e


def pad(notes, t0, dur, gain, bright=5):
    """Five detuned saw-ish voices per note, spread across the stereo field."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    out = np.zeros((n, 2))
    for note in notes:
        f = hz(note)
        for k, cents in enumerate((-14, -6, 0, 6, 14)):
            ff = f * 2 ** (cents / 1200)
            v = sum(np.sin(2 * np.pi * ff * h * t + 0.7 * h + k) / h ** 1.7 for h in range(1, bright + 1))
            p = (k - 2) / 2.5
            out[:, 0] += v * np.sqrt(0.5 * (1 - p))
            out[:, 1] += v * np.sqrt(0.5 * (1 + p))
    out *= envelope(n, min(0.9, dur / 3), min(1.3, dur / 2.5))[:, None] / (len(notes) * 5)
    place(out, t0, gain, send=0.45)


def bass(note, t0, dur, gain):
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = hz(note)
    s = np.tanh(1.6 * (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t))) / 1.3
    place(s * envelope(n, 0.012, min(0.18, dur / 3), sustain=0.65, decay=0.25), t0, gain, send=0.05)


def pluck(note, t0, gain, pan=0.0, length=1.1):
    n = int(length * SR)
    t = np.arange(n) / SR
    f = hz(note)
    s = sum(np.sin(2 * np.pi * f * h * t) * np.exp(-t * (4.5 + 3.2 * h)) / h for h in range(1, 7))
    s *= np.minimum(1, t / 0.003)
    place(s, t0, gain, pan=pan, send=0.35)


def kick(t0, gain):
    n = int(0.5 * SR)
    t = np.arange(n) / SR
    f = 46 + 70 * np.exp(-t * 30)
    s = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 8)
    place(s, t0, gain)


def hat(t0, gain, pan=0.25, length=0.035):
    n = int(length * SR)
    t = np.arange(n) / SR
    s = np.diff(rng.standard_normal(n + 2), 2) * np.exp(-t / (length / 4))
    place(s / 3, t0, gain, pan=pan, send=0.1)


def shaker(t0, gain, pan=-0.3):
    n = int(0.08 * SR)
    t = np.arange(n) / SR
    s = np.diff(rng.standard_normal(n + 1)) * np.sin(np.pi * t / t[-1]) ** 2
    place(s / 2, t0, gain, pan=pan, send=0.1)


# ---------------- problem: A minor, sparse and tense ----------------
prob = [(["A2", "C3", "E3"], "A1"), (["F2", "A2", "C3"], "F1"), (["D2", "F2", "A2"], "D1"), (["E2", "G#2", "B2"], "E1")]
t0, i = 0.0, 0
while t0 < PROBLEM_END - 0.2:
    chord, root = prob[(i // 2) % 4]
    dur = min(BAR, PROBLEM_END - t0)
    pad(chord, t0, dur + 1.0, gain=0.55, bright=4)
    bass(root, t0, min(BAR * 0.9, dur), gain=0.30)
    if i % 2 == 0:  # the two-note motif
        pluck("E5", t0 + BEAT * 1.5, 0.08, pan=0.4, length=1.6)
        pluck("C5", t0 + BEAT * 2.5, 0.07, pan=-0.4, length=1.6)
    for b in range(4):
        tb = t0 + b * BEAT
        if tb >= W(5.3) and tb < PROBLEM_END:  # pulse grows with the problems
            grow = (tb - W(5.3)) / max(0.1, PROBLEM_END - W(5.3))
            bass(root.replace("1", "2"), tb, 0.12, gain=0.10 + 0.12 * grow)
        if tb >= W(7.7) and tb < PROBLEM_END:
            for h in (0, 1):
                shaker(tb + h * BEAT / 2, 0.05 + 0.08 * (tb - W(7.7)) / max(0.1, PROBLEM_END - W(7.7)), pan=-0.3 if h else 0.3)
    t0 += BAR
    i += 1

# ---------------- Cairn: C G Am F, opening up, then building ----------------
prog = [(["C3", "G3", "E4"], "C2", ["C4", "E4", "G4", "C5"]),
        (["G2", "D3", "B3"], "G1", ["B3", "D4", "G4", "B4"]),
        (["A2", "E3", "C4"], "A1", ["A3", "C4", "E4", "A4"]),
        (["F2", "C3", "A3"], "F1", ["A3", "C4", "F4", "A4"])]
ARP = [0, 1, 2, 3, 2, 1, 2, 3]
t0, i = OPEN, 0
kick(OPEN, 0.55)
while t0 < END - 0.1:
    chord, root, arp = prog[i % 4]
    building = t0 >= BUILD - 0.01
    lift = 1.0 + (0.45 * min(1, (t0 - BUILD) / max(0.1, END - BUILD)) if building else 0)
    pad(chord, t0, BAR + 1.0, gain=0.62 * lift, bright=7)
    for b in range(4):
        tb = t0 + b * BEAT
        if tb >= END - 0.02:
            break
        drums = tb >= DRUMS_IN and not (REVEAL_PULL <= tb < REVEAL)
        if building:
            for e in (0, 1):  # driving eighth bass
                bass(root, tb + e * BEAT / 2, BEAT / 2 * 0.85, gain=0.30)
            kick(tb, 0.50)
            for s in range(4):
                shaker(tb + s * BEAT / 4, 0.07 if s % 2 else 0.11, pan=0.3 if s % 2 else -0.3)
        else:
            if b in (0, 2):
                bass(root, tb, BEAT * 1.8, gain=0.34)
            if b == 3:
                bass(root.replace("1", "2").replace("C2", "C3"), tb + BEAT / 2, BEAT / 2 * 0.8, gain=0.18)
            if drums and b in (0, 2):
                kick(tb, 0.42)
        if drums:
            hat(tb + BEAT / 2, 0.20, pan=0.3)
        for e in (0, 1):  # arpeggio
            k = b * 2 + e
            te = tb + e * BEAT / 2
            if te < END - 0.02:
                g = 0.10 if t0 < DRUMS_IN else 0.13
                pluck(arp[ARP[k]], te, g, pan=0.3 if k % 2 else -0.3)
                if building and k % 2 == 0:
                    pluck(arp[ARP[k]][:-1] + str(int(arp[ARP[k]][-1]) + 1), te, 0.06, pan=-0.5)
    t0 += BAR
    i += 1
kick(REVEAL, 0.5)

# ---------------- end card: Cadd9, rising arpeggio, long tail ----------------
pad(["C3", "G3", "D4", "E4"], END, DUR - END, gain=0.75, bright=6)
bass("C2", END, min(4.0, DUR - END - 0.5), gain=0.32)
kick(END, 0.55)
for k, note in enumerate(["C4", "E4", "G4", "C5", "D5", "E5"]):
    pluck(note, END + 0.2 + k * BEAT / 2, 0.11, pan=(k - 2.5) * 0.15, length=2.2)

# ---------------- reverb send, master ----------------
def ir(seed):
    r = np.random.default_rng(seed)
    n = int(2.4 * SR)
    x = r.standard_normal(n) * np.exp(-np.arange(n) / SR * 2.6)
    x = np.convolve(x, np.ones(6) / 6, "same")  # darker tail
    return x / np.sqrt(np.sum(x ** 2))


def convolve(x, h):
    size = 1 << (len(x) + len(h) - 1).bit_length()
    return np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(h, size), size)[: len(x)]


rev = np.stack([convolve(wet[:, 0], ir(1)), convolve(wet[:, 1], ir(2))], axis=1)
mix = dry + 0.55 * rev
mix = np.tanh(mix * 0.9)
n_tail = int(1.8 * SR)
mix[-n_tail:] *= np.linspace(1, 0, n_tail)[:, None] ** 1.5
mix[: int(0.25 * SR)] *= np.linspace(0, 1, int(0.25 * SR))[:, None]
mix *= 0.89 / np.max(np.abs(mix))
with wave.open(str(HERE / "music.wav"), "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes((mix * 32767).astype("<i2").tobytes())
print(f"music.wav {DUR:.2f} s")
