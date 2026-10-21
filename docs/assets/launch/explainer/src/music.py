"""The explainer's score, synthesised in numpy: detuned pad, soft bass, plucked arpeggio, kick and
shaker at 92 BPM, in sections that follow the story. Deterministic (seeded noise).

score(duration, W) returns a stereo float array at 44.1 kHz. W maps a time on the original 112 s
scene timeline to the narrated timeline, so sections land on the scenes they belong to:

  question   0 – W(20)    curious and sparse: minor-leaning pad, whole-note bass, a few plucks
  layers     W(20) – W(52) a pulse that builds as the question travels through the five layers
  result     W(52) – W(80) resolved and brighter: major progression, full arpeggio, steady pulse
  remember   W(80) – W(94) the same colour, lighter
  outro      W(94) – end   calm: pad and sparse plucks, a final chord with a long tail
"""
import numpy as np

SR = 44100
BPM = 92
BEAT = 60 / BPM
BAR = 4 * BEAT

MINOR = [[47, 54, 57, 61, 62], [43, 50, 54, 57, 62], [45, 50, 54, 57, 61], [45, 52, 57, 59, 64]]   # Bm9 Gmaj7 D/A A6sus
MINOR_BASS = [35, 31, 33, 33]
BRIGHT = [[50, 57, 62, 66, 69], [49, 57, 61, 64, 69], [47, 54, 57, 62, 66], [43, 50, 57, 59, 66]]  # D A/C# Bm7 Gmaj9
BRIGHT_BASS = [38, 37, 35, 31]


def hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def env(n, attack, release):
    e = np.ones(n)
    a, r = max(1, int(attack * SR)), max(1, int(release * SR))
    e[:a] = np.linspace(0, 1, a) ** 2
    e[-r:] *= np.linspace(1, 0, r) ** 2
    return e


def pad_note(f, dur, bright=1.0):
    n = int(dur * SR); t = np.arange(n) / SR; s = np.zeros(n)
    for det, ph in ((-0.14, 0.3), (0.0, 1.1), (0.13, 2.0)):
        ff = f * 2 ** (det / 12)
        for h in range(1, 8):
            s += np.sin(2 * np.pi * ff * h * t + ph * h) / h ** (2.1 - 0.4 * bright)
    return s * (1 + 0.05 * np.sin(2 * np.pi * 0.17 * t)) * env(n, 1.3, 1.8) / 10


def bass_note(f, dur):
    n = int(dur * SR); t = np.arange(n) / SR
    s = np.sin(2 * np.pi * f * t) + 0.25 * np.sin(2 * np.pi * 2 * f * t) + 0.06 * np.sin(2 * np.pi * 3 * f * t)
    return s * env(n, 0.02, min(0.25, dur * 0.4)) * (0.6 + 0.4 * np.exp(-t / 0.5))


def pluck(f, dur=1.3):
    n = int(dur * SR); t = np.arange(n) / SR
    s = np.sin(2 * np.pi * f * t) + 0.3 * np.sin(2 * np.pi * 2 * f * t) * np.exp(-t / 0.15) + 0.1 * np.sin(2 * np.pi * 3 * f * t) * np.exp(-t / 0.08)
    return s * np.exp(-t / 0.38) * env(n, 0.003, 0.05)


def kick():
    n = int(0.42 * SR); t = np.arange(n) / SR
    f = 46 + 70 * np.exp(-t / 0.03)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.15)


def shaker(rng):
    n = int(0.09 * SR); t = np.arange(n) / SR
    x = np.diff(np.concatenate([[0], rng.standard_normal(n)]))
    x = np.diff(np.concatenate([[0], x]))
    return x * np.exp(-t / 0.022) * np.minimum(1, t / 0.006)


def reverb(x, seed, seconds=3.2, decay=0.75):
    n = int(seconds * SR); t = np.arange(n) / SR
    ir = np.random.default_rng(seed).standard_normal(n) * np.exp(-t / decay); ir[0] = 0
    size = 1 << int(np.ceil(np.log2(len(x) + n)))
    y = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[: len(x)]
    return y / np.sqrt(np.sum(ir ** 2))


def score(duration, W):
    N = int(duration * SR)
    L, R = np.zeros(N), np.zeros(N)
    rng = np.random.default_rng(7)

    def add(sig, start, gain, pan=0.0):
        i = int(start * SR)
        if i >= N or i < 0:
            return
        sig = sig[: N - i]
        L[i:i + len(sig)] += sig * gain * np.sqrt(0.5 * (1 - pan))
        R[i:i + len(sig)] += sig * gain * np.sqrt(0.5 * (1 + pan))

    q_end, l_start, r_start, m_start, o_start = W(20), W(20), W(52), W(80), W(94)
    third_layer, end_chord = W(35.1), W(103.8)

    def section(t):
        if t < l_start: return "question"
        if t < r_start: return "layers"
        if t < m_start: return "result"
        if t < o_start: return "remember"
        return "outro"

    # pads and bass, one chord every two bars
    bar = 0
    while bar * BAR < end_chord - 0.5:
        t0 = bar * BAR; sec = section(t0 + 0.01)
        bright = sec in ("result", "remember", "outro")
        ci = (bar // 2) % 4
        chord = (BRIGHT if bright else MINOR)[ci]
        root = (BRIGHT_BASS if bright else MINOR_BASS)[ci]
        dur = min(2 * BAR, end_chord - t0) + 1.6
        g = {"question": 0.15, "layers": 0.17, "result": 0.21, "remember": 0.19, "outro": 0.18}[sec]
        for k, m in enumerate(chord):
            add(pad_note(hz(m), dur, 1.3 if bright else 1.0), t0, g, pan=(k - 2) * 0.18)
        # bass
        for b in range(8):
            tb = t0 + b * BEAT
            if tb >= end_chord - 0.2:
                break
            s = section(tb)
            if s in ("question", "outro"):
                if b % 4 == 0: add(bass_note(hz(root), BAR * 0.95), tb, 0.30)
            elif s == "layers":
                add(bass_note(hz(root), BEAT * 0.9), tb, 0.20 if tb < third_layer else 0.26)
            else:   # result / remember: root then fifth, eighth-note push on beat 4
                add(bass_note(hz(root + (7 if b % 4 == 2 else 0)), BEAT * 0.85), tb, 0.27 if s == "result" else 0.22)
                if b % 4 == 3: add(bass_note(hz(root + 12), BEAT * 0.4), tb + BEAT / 2, 0.14)
        # arpeggio
        tones = sorted(set(m + 12 for m in chord[1:]) | set(m + 24 for m in chord[2:4]))
        for e in range(16):
            te = t0 + e * BEAT / 2
            if te >= end_chord - 0.2 or te < 1.2:
                continue
            s = section(te)
            note = tones[[0, 2, 1, 3, 2, 4, 3, 1][e % 8] % len(tones)]
            if s == "question":
                if e % 4 == 0: add(pluck(hz(note)), te, 0.07, pan=-0.3 if e % 8 else 0.3)
            elif s == "layers":
                k = (te - l_start) / max(1, r_start - l_start)        # builds through the five layers
                if e % 2 == 0 or k > 0.35: add(pluck(hz(note)), te, 0.06 + 0.06 * k, pan=0.35 if e % 2 else -0.35)
            elif s == "result":
                add(pluck(hz(note + (12 if e % 8 == 5 else 0))), te, 0.115, pan=0.35 if e % 2 else -0.35)
            elif s == "remember":
                add(pluck(hz(note)), te, 0.09, pan=0.35 if e % 2 else -0.35)
            elif e % 4 == 0:
                add(pluck(hz(note)), te, 0.06, pan=0.3)
        bar += 2

    # pulse: kick and shaker
    tb, i = l_start, 0
    while tb < o_start - 0.3:
        s = section(tb)
        on = (i % 2 == 0) if (s == "layers" and tb < third_layer) or s == "remember" else True
        if on:
            add(kick(), tb, 0.30 if s == "result" else 0.24)
        if tb >= W(29.6):
            add(shaker(rng), tb + BEAT / 2, 0.035, pan=0.25)
            add(shaker(rng), tb, 0.018, pan=0.25)
        tb += BEAT; i += 1

    # final chord with a long tail
    for k, m in enumerate(BRIGHT[0] + [74]):
        add(pad_note(hz(m), duration - end_chord + 0.3, 1.3), end_chord, 0.20, pan=(k - 3) * 0.15)
    add(bass_note(hz(38), 4.0), end_chord, 0.3)

    wl, wr = reverb(L, 1), reverb(R, 2)
    out = np.stack([L * 0.78 + wl * 0.4, R * 0.78 + wr * 0.4], 1)
    t = np.arange(N) / SR
    out *= (np.clip(t / 1.2, 0, 1) * np.clip((duration - t) / 3.0, 0, 1))[:, None]
    return out
