"""Sound effects, synthesized with numpy: python3 sfx.py  ->  sfx.wav (44.1 kHz stereo).

Every cue is written at the original film time of the on-screen event it belongs to (the times in
index.html's renderAt) and moved through timing.json's warp, so it stays in sync after retiming.
Deterministic: all noise is seeded.
"""
import json
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SR = 44100
rng = np.random.default_rng(404)

timing = json.loads((HERE / "timing.json").read_text())
_new, _old = zip(*timing["knots"])
DUR = timing["duration"]
W = lambda t: float(np.interp(t, _old, _new))
N = int(DUR * SR) + 1
out = np.zeros((N, 2))


def place(sig, t_old, gain=1.0, pan=0.0, at_new=None):
    t0 = W(t_old) if at_new is None else at_new
    i = int(round(t0 * SR))
    if i < 0 or i >= N:
        return
    if sig.ndim == 1:
        sig = np.stack([sig * np.sqrt(1 - pan), sig * np.sqrt(1 + pan)], axis=1)  # constant-power pan
    sig = sig[: N - i] * gain
    out[i : i + len(sig)] += sig


def lowpass(x, cutoff):
    """One-pole low-pass with a per-sample cutoff (Hz)."""
    a = np.exp(-2 * np.pi * np.broadcast_to(cutoff, x.shape) / SR)
    y = np.empty_like(x)
    acc = 0.0
    for k in range(len(x)):
        acc = (1 - a[k]) * x[k] + a[k] * acc
        y[k] = acc
    return y


def t_axis(sec):
    return np.arange(int(sec * SR)) / SR


# ---------------- the sounds ----------------
def whoosh(length=0.75):
    t = t_axis(length)
    p = t / length
    cut = 300 + 5200 * np.sin(np.pi * p) ** 2
    s = lowpass(rng.standard_normal(len(t)), cut) * np.sin(np.pi * p) ** 1.5
    pan = np.linspace(-0.7, 0.7, len(t))
    st = np.stack([s * np.sqrt(0.5 * (1 - pan)), s * np.sqrt(0.5 * (1 + pan))], axis=1)
    return st / np.max(np.abs(st))


def impact(length=1.8):
    t = t_axis(length)
    f = 34 + 60 * np.exp(-t * 14)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 2.6)
    thump = lowpass(rng.standard_normal(len(t)), 400) * np.exp(-t * 22) * 3
    s = np.tanh(1.5 * (body + thump))
    return s / np.max(np.abs(s))


def pop(freq=1400, length=0.09):
    t = t_axis(length)
    f = freq * (1 - 0.18 * np.minimum(1, t / 0.03))
    s = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 55) * np.minimum(1, t / 0.002)
    return s


def tick():
    t = t_axis(0.012)
    s = np.diff(rng.standard_normal(len(t) + 1)) * np.exp(-t * 600) * 0.5 + np.sin(2 * np.pi * 3200 * t) * np.exp(-t * 900) * 0.4
    return s * (0.75 + 0.5 * rng.random())


def thud(freq=180):
    t = t_axis(0.18)
    s = np.sin(2 * np.pi * freq * (1 - 0.3 * np.minimum(1, t / 0.08)) * t) * np.exp(-t * 28)
    return s


def error_blip():
    t = t_axis(0.11)
    s = np.sign(np.sin(2 * np.pi * 196 * t)) * 0.4 + np.sin(2 * np.pi * 207 * t)
    s = lowpass(s, 1800) * np.minimum(1, t / 0.004) * np.exp(-t * 12)
    gap = np.zeros(int(0.05 * SR))
    return np.concatenate([s, gap, s * 0.8])


def compress():
    t = t_axis(0.55)
    f = 900 * np.exp(-t * 3.2)
    s = np.sin(2 * np.pi * np.cumsum(f) / SR) * 0.6 + lowpass(rng.standard_normal(len(t)), 1200 * np.exp(-t * 3)) * 0.8
    return s * np.minimum(1, t / 0.02) * np.exp(-t * 4)


def riser(length):
    t = t_axis(length)
    p = t / length
    noise = lowpass(rng.standard_normal(len(t)), 300 + 7000 * p ** 2) * p ** 2.5
    f = 220 * 2 ** (2.2 * p)
    tone = np.sin(2 * np.pi * np.cumsum(f) / SR) * p ** 3 * 0.35
    s = noise + tone
    s[-int(0.01 * SR):] *= np.linspace(1, 0, int(0.01 * SR))
    return s / np.max(np.abs(s))


def chime(length=2.8):
    t = t_axis(length)
    s = np.zeros(len(t))
    for base, g in ((1046.5, 1.0), (1568.0, 0.7), (2637.0, 0.45)):  # C6, G6, E7
        for ratio, amp, dec in ((1, 1, 1.6), (2.0, 0.35, 3), (3.01, 0.18, 4.5), (4.2, 0.08, 7)):
            s += g * amp * np.sin(2 * np.pi * base * ratio * t) * np.exp(-t * dec)
    s *= np.minimum(1, t / 0.003)
    return s / np.max(np.abs(s))


def release(length=1.9):
    t = t_axis(length)
    air = lowpass(rng.standard_normal(len(t)), 2400 * np.exp(-t * 1.8) + 200) * np.sin(np.pi * np.minimum(1, t / length)) ** 1.2
    dyad = (np.sin(2 * np.pi * 659.3 * t) + np.sin(2 * np.pi * 784.0 * t) + 0.5 * np.sin(2 * np.pi * 1046.5 * t))
    dyad *= np.minimum(1, t / 0.35) ** 2 * np.exp(-t * 1.6)
    s = air * 0.8 + dyad * 0.35
    return s / np.max(np.abs(s))


# ---------------- cues, at original film times (see index.html renderAt) ----------------
# scene transitions
for t in (14.75, 18.3, 23.0, 29.9, 37.7, 42.7, 51.25):
    place(whoosh(), t, 0.55)
# title and final logo
place(impact(), 16.35, 0.9)
place(impact(), 52.25, 1.0)
# cairn stones landing (title and end card)
for k, t in enumerate((15.85, 16.15, 16.45)):
    place(thud(160 + 30 * k), t, 0.5)
for k, t in enumerate((52.05, 52.3, 52.55)):
    place(thud(160 + 30 * k), t, 0.5)
# terminal lines appearing, problem scenes
for k, t in enumerate((0.35, 0.85, 1.35, 1.85, 5.35, 5.95, 6.55, 11.2, 12.8, 13.4)):
    place(pop(900 + 60 * (k % 3), 0.07), t, 0.35, pan=-0.3)
place(pop(1200), 7.75, 0.35, pan=0.4)            # git log card
place(error_blip(), 7.8, 0.45, pan=-0.2)          # the reverted edit
place(compress(), 10.25, 0.5)                     # context compacted
for k in range(9):                                # tokens spent re-reading
    place(pop(2200 + 140 * (k % 4), 0.06), 12.9 + k * 0.2 + 0.12, 0.25, pan=0.5)
# five layers
for k, t in enumerate((18.7, 19.5, 20.3, 21.1, 21.9)):
    place(pop(700 + 110 * k), t + 0.12, 0.5, pan=-0.4)
# the agent's call: typing, chips, the answer pack
call = 'cairn_impact("src/cairn/router.py")'
for k in range(len(call)):
    place(tick(), 23.5 + k / 30, 0.35, pan=-0.2)
for t in (24.0, 25.0, 25.8):
    place(pop(1500, 0.06), t, 0.3)
place(pop(600), 26.6, 0.45)
for k in range(7):
    place(pop(1100 + 70 * k, 0.07), 26.9 + k * 0.22 + 0.1, 0.32, pan=-0.3)
place(pop(800), 27.6, 0.35, pan=0.3)
# proof: files, the answer square, riser, reveal chime
for k in range(0, 33, 3):
    place(pop(1800 + 40 * (k % 5), 0.05), 30.3 + k * 0.04 + 0.1, 0.18, pan=-0.4)
place(pop(1300), 31.65, 0.45, pan=0.3)
rise_to = W(34.0)
rise_from = max(W(32.95), rise_to - 1.2)  # short, so it sits in the pause before the reveal line
place(riser(rise_to - rise_from), 0, 0.5, at_new=rise_from)
place(chime(), 34.0, 0.6)
# resume: sections, and the release when it resolves
for k in range(5):
    place(pop(900 + 90 * k, 0.07), 38.3 + k * 0.4 + 0.1, 0.32, pan=-0.3)
place(release(), 39.15, 0.6)
# install: typing, check lines, facts, agents
for start, text in ((42.95, "uv tool install --python 3.12 cairn-brain"), (44.7, "cd your-repo"), (45.4, "cairn")):
    for k in range(len(text)):
        place(tick(), start + 0.05 + k / 34, 0.35, pan=-0.2)
for k, t in enumerate((46.0, 46.25, 46.5, 46.75)):
    place(pop(1400 + 100 * k, 0.06), t, 0.28, pan=-0.2)
for k, t in enumerate((43.4, 44.2, 45.0, 45.8)):
    place(pop(1000, 0.07), t, 0.25, pan=0.5)
for k in range(6):
    place(pop(800 + 90 * k), 47.3 + k * 0.32 + 0.15, 0.4, pan=-0.5 + k * 0.2)

peak = np.max(np.abs(out))
out *= 0.89 / peak
with wave.open(str(HERE / "sfx.wav"), "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes((out * 32767).astype("<i2").tobytes())
print(f"sfx.wav {DUR:.2f} s")
