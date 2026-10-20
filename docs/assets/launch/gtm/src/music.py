"""Synthesizes the film's music bed with numpy only: python3 music.py  ->  music.wav (57 s, 44.1 kHz stereo).

Two halves that follow the picture: 0-15 s is the problem (A minor pad, a low pulse, no melody),
a noise riser lands on the cut to paper at 15.3 s, then C-G-Am-F with an arpeggio, soft kick and
shaker until the end card, which holds a C major chord and fades out. Deterministic (seeded noise).
"""
import wave
import numpy as np

SR = 44100
DUR = 57.0
BPM = 96.0
BEAT = 60.0 / BPM
N = int(SR * DUR)
rng = np.random.default_rng(7)
L = np.zeros(N)
R = np.zeros(N)


def hz(note):
    names = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}
    return 440.0 * 2 ** ((names[note[:-1]] + 12 * (int(note[-1]) + 1) - 69) / 12)


def add(sig, t0, gain=1.0, pan=0.0, delay_r=0.0):
    i = int(t0 * SR)
    if i >= N:
        return
    sig = sig[: N - i] * gain
    L[i : i + len(sig)] += sig * (1 - max(pan, 0))
    j = i + int(delay_r * SR)
    if j < N:
        s2 = sig[: N - j]
        R[j : j + len(s2)] += s2 * (1 + min(pan, 0))


def env_adsr(n, a, r):
    e = np.ones(n)
    na, nr = int(a * SR), int(r * SR)
    e[:na] = np.linspace(0, 1, na) ** 2
    e[-nr:] *= np.linspace(1, 0, nr) ** 1.5
    return e


def pad(notes, t0, dur, gain=0.05, bright=6):
    n = int(dur * SR)
    t = np.arange(n) / SR
    s = np.zeros(n)
    for note in notes:
        f = hz(note)
        for det in (-0.12, 0.0, 0.12):  # three slightly detuned voices
            ff = f * 2 ** (det / 12)
            for h in range(1, bright + 1):
                s += np.sin(2 * np.pi * ff * h * t + h) / h ** 1.6
    s *= env_adsr(n, min(1.2, dur / 3), min(1.4, dur / 3))
    add(s, t0, gain / len(notes), delay_r=0.013)


def pluck(note, t0, gain=0.12, pan=0.0):
    f = hz(note)
    n = int(0.9 * SR)
    t = np.arange(n) / SR
    s = (np.sin(2 * np.pi * f * t) + 0.35 * np.sin(4 * np.pi * f * t) + 0.12 * np.sin(6 * np.pi * f * t)) * np.exp(-t * 6.5)
    s *= np.minimum(1, t / 0.004)
    add(s, t0, gain, pan=pan)


def kick(t0, gain=0.5):
    n = int(0.45 * SR)
    t = np.arange(n) / SR
    f = 45 + 75 * np.exp(-t * 28)
    s = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9)
    add(s, t0, gain)


def pulse(t0, gain=0.22):
    n = int(0.6 * SR)
    t = np.arange(n) / SR
    s = np.sin(2 * np.pi * 55 * t) * np.exp(-t * 6) * np.minimum(1, t / 0.01)
    add(s, t0, gain)


def shaker(t0, gain=0.035, pan=0.3):
    n = int(0.09 * SR)
    t = np.arange(n) / SR
    s = np.diff(rng.standard_normal(n + 1)) * np.exp(-t * 55)
    add(s, t0, gain, pan=pan)


# ---- part 1: the problem (0 - 15 s), A minor, sparse ----
for t0, ch in [(0.0, ["A2", "E3", "C4"]), (5.0, ["F2", "C3", "A3"]), (10.0, ["E2", "B2", "G#3"])]:
    pad(ch, t0, 5.6, gain=0.11, bright=4)
b = 0.0
while b < 14.8:
    pulse(b, 0.20 if int(round(b / BEAT)) % 2 == 0 else 0.11)
    b += BEAT
for k, t0 in enumerate(np.arange(7.5, 14.8, BEAT / 2)):  # a quiet ticking tension from the reverted fix onward
    shaker(t0, 0.018 + 0.012 * (k / 24), pan=-0.4 if k % 2 else 0.4)

# riser into the cut, then a soft landing
n = int(1.4 * SR)
t = np.arange(n) / SR
riser = rng.standard_normal(n) * (t / 1.4) ** 3 * 0.10
add(riser - np.convolve(riser, np.ones(30) / 30, "same"), 13.9, 1.0)
kick(15.3, 0.6)

# ---- part 2: Cairn (15.3 - 51.5 s), C G Am F ----
prog = [["C3", "G3", "E4"], ["G2", "D3", "B3"], ["A2", "E3", "C4"], ["F2", "C3", "A3"]]
arps = [["C4", "E4", "G4", "C5"], ["B3", "D4", "G4", "B4"], ["A3", "C4", "E4", "A4"], ["A3", "C4", "F4", "A4"]]
bar = 4 * BEAT
start = 15.3
i = 0
while start + i * bar < 51.4:
    t0 = start + i * bar
    c = i % 4
    pad(prog[c], t0, bar + 0.8, gain=0.12)
    for s_ in range(8):  # eighth-note arpeggio
        tt = t0 + s_ * BEAT / 2
        if tt < 51.4:
            seq = [0, 1, 2, 3, 2, 1, 2, 3]
            pluck(arps[c][seq[s_]], tt, gain=0.075 if t0 < 23 else 0.095, pan=0.25 if s_ % 2 else -0.25)
    for beat in range(4):
        tb = t0 + beat * BEAT
        if tb < 51.4 and t0 >= 18.4:  # drums enter with the five layers
            if beat in (0, 2):
                kick(tb, 0.42)
            shaker(tb + BEAT / 2, 0.03, pan=0.35)
    i += 1

# ---- end card: hold C major, fade out ----
pad(["C3", "G3", "C4", "E4"], 51.4, 5.6, gain=0.15)
for k, note in enumerate(["C4", "E4", "G4", "C5"]):
    pluck(note, 51.5 + k * 0.18, gain=0.09, pan=(k - 1.5) * 0.2)
kick(51.5, 0.45)

# ---- small room reverb (exponential noise tail), mix, master ----
ir_n = int(1.1 * SR)
ir = rng.standard_normal(ir_n) * np.exp(-np.arange(ir_n) / SR * 5.0)
ir /= np.sqrt(np.sum(ir ** 2))


def verb(x):
    m = len(x) + ir_n
    size = 1 << (m - 1).bit_length()
    y = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[: len(x)]
    return x + 0.22 * y


L, R = verb(L), verb(R)
mix = np.stack([L, R], axis=1)
mix = np.tanh(mix * 1.4) / 1.4
fade = np.ones(N)
fi, fo = int(0.3 * SR), int(3.0 * SR)
fade[:fi] = np.linspace(0, 1, fi)
fade[-fo:] = np.linspace(1, 0, fo) ** 1.3
mix *= fade[:, None]
mix *= 0.85 / np.max(np.abs(mix))
pcm = (mix * 32767).astype("<i2")
with wave.open(__file__.replace("music.py", "music.wav"), "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes(pcm.tobytes())
print("wrote music.wav", DUR, "s")
