"""The proof video's score, synthesised in numpy (no samples): 100 BPM, detuned pad, soft bass, plucked arpeggio,
quiet shaker and a soft kick, in sections that follow the story. Deterministic.

    ~/cairn-video-audio/.venv/bin/python tools/score.py      # after tools/voice.py; writes assets/score.wav

Sections (scene windows from assets/timing.json):
  s1 intro      pad swell on C, the arpeggio steps in under the title
  s2 overview   confident: C G/B Am F, bass on the beat, 8th arpeggio, shaker, soft kick on 1 and 3
  s3 compaction hush: dark pad, long bass; the bed dips while the conversation compacts and opens again with the snapshot
  s4 not-repeat driving: Am F C G, 8th bass, 16th arpeggio and shaker, kick on every beat
  s5 memory     calm and warm: Fmaj7 Cmaj7/E Dm7 Cmaj7, half-note bass, slow arpeggio, no drums
  s6 install    a build (rising arpeggio, opening filter) into one clean C chord on the install line, then a tail
"""
import json
import wave
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfilt

ROOT = Path(__file__).resolve().parent.parent
SR = 44100
BPM = 100
BEAT = 60 / BPM
BAR = 4 * BEAT
T = json.loads((ROOT / "assets" / "timing.json").read_text())
DUR = T["duration"] + 0.3
N = int(DUR * SR)
L = np.zeros(N)
R = np.zeros(N)
rng = np.random.default_rng(3)


def scene(sid):
    s = T["scenes"][sid]
    return s["start"], s["end"]


def global_at(sid, designed):
    """Inverse of the scene's clock warp: the global time at which a designed-local moment is shown."""
    s = T["scenes"][sid]
    pts = s["warp"]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if designed <= y1:
            return s["start"] + x0 + (x1 - x0) * (designed - y0) / max(1e-6, y1 - y0)
    return s["end"]


def hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def add(sig, t0, gain=1.0, pan=0.0):
    i = int(t0 * SR)
    if i >= N:
        return
    sig = sig[: N - i]
    L[i:i + len(sig)] += sig * gain * np.sqrt(0.5 * (1 - pan))
    R[i:i + len(sig)] += sig * gain * np.sqrt(0.5 * (1 + pan))


def env(n, a, d, s, r, sus_len):
    """ADSR in seconds; sus_len is the held time before release."""
    out = np.concatenate([np.linspace(0, 1, max(1, int(a * SR))),
                          np.linspace(1, s, max(1, int(d * SR))),
                          np.full(max(0, int(sus_len * SR)), s),
                          np.linspace(s, 0, max(1, int(r * SR)))])
    return out[:n] if len(out) >= n else np.pad(out, (0, n - len(out)))


def saw(f, n, phase=0.0):
    t = np.arange(n) / SR
    return 2 * ((t * f + phase) % 1.0) - 1


def lowpass(x, cutoff, order=2):
    return sosfilt(butter(order, min(cutoff, SR / 2 - 100), "low", fs=SR, output="sos"), x)


def pad(notes, t0, dur, cutoff, vel):
    n = int((dur + 1.2) * SR)
    left, right = np.zeros(n), np.zeros(n)
    for m in notes:
        f = hz(m)
        left += saw(f * 2 ** (-7 / 1200), n) + saw(f * 2 ** (4 / 1200), n, .3)
        right += saw(f * 2 ** (7 / 1200), n, .6) + saw(f * 2 ** (-3 / 1200), n, .1)
    e = env(n, .9, .6, .8, 1.2, max(0, dur - 1.5))
    sub = np.sin(2 * np.pi * hz(notes[0] - 12) * np.arange(n) / SR) * .6
    left = lowpass(left, cutoff) * e + sub * e
    right = lowpass(right, cutoff) * e + sub * e
    i = int(t0 * SR)
    if i >= N:
        return
    k = min(n, N - i)
    g = vel / len(notes) * .16
    L[i:i + k] += left[:k] * g
    R[i:i + k] += right[:k] * g


def bass(m, t0, dur, vel=1.0):
    n = int((dur + .15) * SR)
    t = np.arange(n) / SR
    f = hz(m)
    x = np.sin(2 * np.pi * f * t) + .25 * lowpass(saw(f, n), 500)
    add(x * env(n, .008, .15, .7, .12, max(0, dur - .2)), t0, .22 * vel)


def pluck(m, t0, vel=1.0, pan=0.0, decay=4.5):
    n = int(1.4 * SR)
    t = np.arange(n) / SR
    f = hz(m)
    x = (np.sin(2 * np.pi * f * t) + .35 * np.sin(4 * np.pi * f * t) + .12 * np.sin(6 * np.pi * f * t)) * np.exp(-t * decay)
    x *= np.minimum(1, t / .004)
    add(x, t0, .075 * vel, pan)


def shaker(t0, vel=1.0):
    n = int(.09 * SR)
    t = np.arange(n) / SR
    x = sosfilt(butter(2, 6500, "high", fs=SR, output="sos"), rng.standard_normal(n)) * np.exp(-t * 55)
    add(x, t0, .035 * vel, .3)


def kick(t0, vel=1.0):
    n = int(.4 * SR)
    t = np.arange(n) / SR
    f = 48 + 70 * np.exp(-t * 28)
    x = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9)
    add(x, t0, .30 * vel)


# chords as MIDI notes (pad voicing), bass root, arpeggio tones
C, G_B, Am, F = [60, 64, 67, 71], [59, 62, 67, 74], [57, 60, 64, 69], [53, 57, 60, 65]
Dm7, Cmaj7_E, Fmaj7, Cmaj7 = [62, 65, 69, 72], [64, 67, 71, 72], [53, 57, 60, 64], [60, 64, 67, 71]
ROOT_OF = {id(C): 36, id(G_B): 35, id(Am): 33, id(F): 29, id(Dm7): 38, id(Cmaj7_E): 40, id(Fmaj7): 29, id(Cmaj7): 36}


def section(t0, t1, prog, cutoff, vel, bass_step, arp_step, arp_vel, shaker_step=None, kick_every=None, arp_oct=12):
    bar_i = 0
    t = t0
    while t < t1 - .05:
        chord = prog[bar_i % len(prog)]
        dur = min(BAR, t1 - t)
        pad(chord, t, dur, cutoff, vel)
        root = ROOT_OF[id(chord)]
        if bass_step:
            b = t
            while b < t + dur - .01:
                bass(root, b, min(bass_step, t + dur - b) * .9)
                b += bass_step
        if arp_step:
            k, a = 0, t
            tones = sorted(chord) + [chord[0] + 12]
            pattern = [0, 2, 1, 3, 4, 3, 1, 2]
            while a < t + dur - .01:
                pluck(tones[pattern[k % 8] % len(tones)] + arp_oct, a, arp_vel * (1 if k % 2 == 0 else .75), pan=.4 * np.sin(k * .9))
                a += arp_step
                k += 1
        if shaker_step:
            s = t
            k = 0
            while s < t + dur - .01:
                shaker(s, 1 if k % 2 else .6)
                s += shaker_step
                k += 1
        if kick_every:
            kk = t
            while kk < t + dur - .01:
                kick(kk)
                kk += kick_every
        t += BAR
        bar_i += 1


s1, s2, s3, s4, s5, s6 = (scene(f"s{i}") for i in range(1, 7))
# s1: intro swell; the arpeggio steps in under the title
pad(C, 0.0, s1[1] + .3, 1400, .9)
for k, m in enumerate([72, 76, 79, 83, 79, 76]):
    pluck(m, 2.2 + k * BEAT / 2, .7, .3 * np.sin(k))
# s2: confident
section(s2[0], s2[1], [C, G_B, Am, F], 2600, 1.0, BEAT, BEAT / 2, .9, shaker_step=BEAT / 2, kick_every=2 * BEAT)
# s3: hush; dips while the conversation compacts, opens again with the snapshot
freeze, unfold = global_at("s3", 3.8), global_at("s3", 6.6)
section(s3[0], freeze, [Am, F], 900, .85, 2 * BEAT, None, 0)
pad(Am, freeze, unfold - freeze, 500, .45)
section(unfold, s3[1], [F, C, G_B, Am], 1500, .85, 2 * BEAT, BEAT, .55)
# s4: driving
section(s4[0], s4[1], [Am, F, C, G_B], 3200, 1.0, BEAT / 2, BEAT / 4, .7, shaker_step=BEAT / 4, kick_every=BEAT)
# s5: calm and warm
section(s5[0], s5[1], [Fmaj7, Cmaj7_E, Dm7, Cmaj7], 1800, .95, 2 * BEAT, BEAT, .6)
# s6: build into one clean C chord on the install line
hit = global_at("s6", 2.8)
section(s6[0], hit, [F, G_B], 2200, 1.0, BEAT / 2, BEAT / 4, .8, shaker_step=BEAT / 4)
pad(C, hit, s6[1] - hit - .8, 2400, 1.1)
bass(36, hit, 2.5, 1.2)
kick(hit, 1.2)
for k, m in enumerate([60, 64, 67, 72, 76, 79]):
    pluck(m + 12, hit + k * .09, .8, -.5 + k * .2, decay=2.2)

# master: gentle fades, light saturation, peak at -3 dBFS
fade = np.ones(N)
fade[: int(.8 * SR)] = np.linspace(0, 1, int(.8 * SR))
tail = int(2.0 * SR)
fade[-tail:] = np.linspace(1, 0, tail) ** 1.5
mix = np.stack([L, R], 1) * fade[:, None]
mix = np.tanh(mix * 1.2)
mix /= np.max(np.abs(mix)) / 0.7
with wave.open(str(ROOT / "assets" / "score.wav"), "wb") as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((mix * 32767).astype("<i2").tobytes())
print(f"score.wav {DUR:.1f}s; freeze {freeze:.2f}, unfold {unfold:.2f}, install hit {hit:.2f}")
