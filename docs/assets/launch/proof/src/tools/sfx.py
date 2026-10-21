"""Sound effects, synthesised in numpy and placed on the on-screen events of proof.html.

    ~/cairn-video-audio/.venv/bin/python tools/sfx.py      # after tools/voice.py; writes assets/sfx.wav and assets/sfx.json

Event times are written in each scene's designed seconds (the same numbers as the renderers in proof.html) and mapped
to the video's clock through the scene's warp, so a sound lands on the frame where its thing appears.
"""
import json
import wave
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfilt

ROOT = Path(__file__).resolve().parent.parent
SR = 44100
T = json.loads((ROOT / "assets" / "timing.json").read_text())
N = int((T["duration"] + .5) * SR)
L, R = np.zeros(N), np.zeros(N)
rng = np.random.default_rng(5)
t_ = lambda d: np.arange(int(d * SR)) / SR


def at(sid, designed):
    s = T["scenes"][sid]
    for (x0, y0), (x1, y1) in zip(s["warp"], s["warp"][1:]):
        if designed <= y1:
            return s["start"] + x0 + (x1 - x0) * (designed - y0) / max(1e-6, y1 - y0)
    return s["end"]


def put(x, t0, gain=1.0, pan=0.0):
    i = int(t0 * SR)
    x = x[: max(0, N - i)]
    L[i:i + len(x)] += x * gain * np.sqrt(.5 * (1 - pan))
    R[i:i + len(x)] += x * gain * np.sqrt(.5 * (1 + pan))


def tone(f, d, decay, attack=.003):
    t = t_(d)
    f = np.broadcast_to(f, t.shape) if np.ndim(f) else np.full_like(t, f)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * decay) * np.minimum(1, t / attack)


def noise(d, lo=None, hi=None):
    x = rng.standard_normal(int(d * SR))
    if lo:
        x = sosfilt(butter(2, lo, "high", fs=SR, output="sos"), x)
    if hi:
        x = sosfilt(butter(2, hi, "low", fs=SR, output="sos"), x)
    return x


# ---- the sounds ----
def click():   # a UI click: a short tick of noise and a high sine
    return noise(.03, 2500) * np.exp(-t_(.03) * 260) * .5 + tone(2200, .03, 160) * .5

def pop(f=760):  # a soft bubble pop, pitch rising
    t = t_(.09)
    return tone(f + 500 * t / .09, .09, 45) * .8

def tick():
    return tone(3100, .012, 300) * .5

def chime():   # two bell partials and a soft octave
    d = 1.6
    return (tone(1318.5, d, 3.2) + .7 * tone(1975.5, d, 4.0) + .25 * tone(2637, d, 6)) * .45

def countup(d, step=.055):
    out = np.zeros(int((d + .05) * SR))
    k = 0
    while k * step < d:
        x = tick() * (.5 + .5 * k * step / d)
        i = int(k * step * SR)
        out[i:i + len(x)] += x[: len(out) - i]
        k += 1
    return out

def freeze(d=2.2):  # a soft whoosh that sweeps down and settles, with a cold shimmer
    t = t_(d)
    x = noise(d)
    bands = [sosfilt(butter(2, [max(80, c * .7), c * 1.3], "band", fs=SR, output="sos"), x) for c in (3200, 1600, 800, 400)]
    w = np.clip(t / d * 4, 0, 3.999)
    mix = sum(b * np.clip(1 - np.abs(w - k), 0, 1) for k, b in enumerate(bands))
    shimmer = tone(2093 - 600 * t / d, d, 1.6) * .25
    return (mix * .6 + shimmer) * np.sin(np.pi * np.clip(t / d, 0, 1)) ** 1.2

def unfold(d=1.4):  # a gentle release: rising filtered air and an opening major arpeggio
    t = t_(d)
    air = sosfilt(butter(2, [500, 2600], "band", fs=SR, output="sos"), noise(d)) * np.sin(np.pi * t / d) * .5
    out = air.copy()
    for k, f in enumerate((523.3, 659.3, 784.0, 1046.5)):
        x = tone(f, d - k * .07, 3.5, attack=.02) * .35
        i = int(k * .07 * SR)
        x = x[: len(out) - i]
        out[i:i + len(x)] += x
    return out

def blip(kind):  # four distinct small sounds for the four event kinds
    if kind == "rejected":   # a falling two-note "no"
        return np.concatenate([tone(392, .09, 18), tone(311, .16, 16)]) * .6
    if kind == "error-fix":  # a rising "fixed"
        return np.concatenate([tone(392, .08, 20), tone(587, .17, 14)]) * .6
    if kind == "retry":      # three quick taps
        return np.concatenate([tone(523, .06, 40)] * 3) * .55
    t = t_(.24)              # correction: a bent note that settles
    return tone(659 - 120 * np.minimum(1, t / .12), .24, 11) * .6

def fade_tone(d=1.5):  # a quiet tone that dims and drifts down
    t = t_(d)
    return tone(880 * 2 ** (-t / d * .3), d, 2.2, attack=.06) * .5 * (1 - t / d)

def thud():
    t = t_(.5)
    return tone(48 + 60 * np.exp(-t * 25), .5, 8) * .9 + noise(.5, None, 180) * np.exp(-t * 30) * .3


# ---- the cue sheet: (scene, designed second, sound, gain, pan, label) ----
CUES = [
    ("s1", .2, pop(520), .5, -.2, "stone 1"), ("s1", .55, pop(620), .5, 0, "stone 2"), ("s1", .9, pop(720), .5, .2, "stone 3"),
    ("s2", .05, click(), .8, 0, "Overview window"), ("s2", .8, pop(), .7, .4, "sample data badge"),
    ("s2", 4.9, countup(.55), .7, 0, "19x count-up"), ("s2", 5.45, chime(), .55, 0, "19x chime"),
    ("s2", 9.0, countup(.55), .7, 0, "bytes count-up"), ("s2", 9.55, chime(), .55, 0, "bytes chime"),
    ("s3", .05, click(), .8, 0, "terminal window"), ("s3", 4.3, pop(600), .6, .4, "PreCompact chip"),
    ("s3", 3.8, freeze(2.6), .9, 0, "compaction freeze"), ("s3", 6.6, unfold(), .9, 0, "snapshot unfold"),
    ("s3", 7.0, countup(4.0, .14), .45, 0, "snapshot bytes count-up"), ("s3", 11.0, chime(), .45, 0, "722 bytes chime"),
    ("s4", 1.4, blip("rejected"), .8, -.3, "rejected approach"), ("s4", 1.95, blip("error-fix"), .8, -.1, "error then fix"),
    ("s4", 2.5, blip("retry"), .8, .1, "retry loop"), ("s4", 3.05, blip("correction"), .8, .3, "user correction"),
    ("s4", 7.4, pop(680), .55, -.3, "no-model pill"), ("s4", 10.2, countup(1.0, .07), .4, .2, "search typing"),
    ("s4", 11.3, pop(900), .6, .2, "search result"),
    ("s5", .05, click(), .8, 0, "Memory window"), ("s5", .8, pop(), .7, .4, "sample data badge"),
    ("s5", 4.7, fade_tone(), .7, .2, "Stale badge"), ("s5", 8.2, pop(560), .55, -.2, "conventions ring"),
    ("s6", .3, pop(520), .45, 0, "wordmark"), ("s6", 2.8, thud(), 1.0, 0, "install line"),
]
sheet = []
for sid, d, x, g, pan, label in CUES:
    t0 = at(sid, d)
    put(x, t0, g, pan)
    sheet.append({"t": round(t0, 3), "scene": sid, "label": label})
mix = np.stack([L, R], 1)
mix /= np.max(np.abs(mix)) / .9
with wave.open(str(ROOT / "assets" / "sfx.wav"), "wb") as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((mix * 32767).astype("<i2").tobytes())
(ROOT / "assets" / "sfx.json").write_text(json.dumps(sorted(sheet, key=lambda e: e["t"]), indent=1))
for e in sorted(sheet, key=lambda e: e["t"]):
    print(f"{e['t']:6.2f}  {e['label']}")
