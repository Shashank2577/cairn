"""Sound effects for the explainer, synthesised in numpy and placed on on-screen events.

EVENTS lists (time on the original 112 s scene timeline, kind, parameter); index.html draws the
matching moment at that time. track(duration, W) maps each time through W (original → narrated) and
returns a mono float array at 44.1 kHz with every effect at unit level; narrate.py sets its gain.
"""
import numpy as np

SR = 44100
LAYER_AT = [24.1, 29.6, 35.1, 40.6, 46.1]          # when each layer lights up (index.html LAYERS[].at)


def _ease(x):
    return 1 - (1 - x) ** 3


def _count_ticks(a, b, steps):
    """Times at which an ease-out counter running from a to b crosses each of `steps` even steps."""
    ts = np.linspace(a, b, 2000)
    eased = [(_ease(x) if x < .5 else 1 - (-2 * x + 2) ** 3 / 2) for x in np.linspace(0, 1, 2000)]   # eio, as on the page
    out, k = [], 1
    for t, v in zip(ts, eased):
        if v >= k / steps:
            out.append(t); k += 1
    return out


EVENTS = (
    [(t, "whoosh", 0.5) for t in (9.75, 19.75, 51.75, 65.75, 79.75, 93.75)]                 # scene changes
    + [(14.9, "whoosh", 0.35)]                                                                 # the MCP call slides in
    + [(t, "tick", 0) for t in _count_ticks(10.5, 13.2, 22)]                                   # 8,739 lines counting up
    + [(20.6, "pop", 0)]                                                                       # agent → MCP
    + [(at - 0.6, "whoosh", 0.6) for at in LAYER_AT]                                           # the question travels
    + [(at, "ping", i) for i, at in enumerate(LAYER_AT)]                                       # each layer lights up
    + [(52.35, "land", 0)]                                                                     # the pack lands
    + [(t, "tick", 0) for t in _count_ticks(57.8, 59.4, 18)]                                   # 1,192 counting up
    + [(72.55, "chime", 0)]                                                                    # cairn_impact result
    + [(76.6 + i * 0.7, "pop", 0) for i in range(3)]                                           # dependents checked
    + [(83.6, "stone", 0)]                                                                     # memory stone settles
    + [(85.1, "whoosh", 0.35), (89.6, "pop", 0)]                                              # brief, search
    + [(94.6, "click", 0), (97.4, "click", 0), (100.6, "click", 0)]                            # UI screenshots
    + [(104.3 + i * 0.25, "stone", 1) for i in range(3)]                                       # logo stones
    + [(108.7, "thud", 0)]                                                                     # install line
)

# relative level of each kind against the others (dB); narrate.py sets the overall SFX level
LEVEL = {"whoosh": -4, "tick": -13, "pop": -6, "ping": 0, "land": 0, "chime": 0, "click": -6, "stone": -4, "thud": -1}


def _onepole(x, cut_start, cut_end):
    """One-pole low-pass whose cutoff glides from cut_start to cut_end (Hz)."""
    cuts = np.geomspace(cut_start, cut_end, len(x))
    a = 1 - np.exp(-2 * np.pi * cuts / SR)
    y = np.empty_like(x); acc = 0.0
    for i in range(len(x)):
        acc += a[i] * (x[i] - acc); y[i] = acc
    return y


def whoosh(dur, rng):
    n = int(dur * SR); t = np.linspace(0, 1, n)
    x = _onepole(rng.standard_normal(n), 300, 3800)
    e = np.sin(np.pi * t ** 0.7) ** 2
    return x * e


def ping(i):
    f = 440 * 2 ** ((76 + [0, 2, 4, 7, 9][i] - 69) / 12)            # E5 F#5 G#5 B5 C#6, rising
    n = int(1.2 * SR); t = np.arange(n) / SR
    s = np.sin(2 * np.pi * f * t) + 0.3 * np.sin(2 * np.pi * 2 * f * t) * np.exp(-t / 0.2) + 0.08 * np.sin(2 * np.pi * 3.01 * f * t) * np.exp(-t / 0.1)
    return s * np.exp(-t / 0.35) * np.minimum(1, t / 0.002)


def tick(rng):
    n = int(0.012 * SR); t = np.arange(n) / SR
    return (np.sin(2 * np.pi * 3200 * t) * 0.7 + rng.standard_normal(n) * 0.3) * np.exp(-t / 0.0025)


def pop():
    n = int(0.09 * SR); t = np.arange(n) / SR
    f = 520 + 420 * np.minimum(1, t / 0.03)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.025) * np.minimum(1, t / 0.002)


def click(rng):
    n = int(0.03 * SR); t = np.arange(n) / SR
    return (np.sin(2 * np.pi * 1800 * t) * 0.6 + np.diff(np.concatenate([[0], rng.standard_normal(n)])) * 0.4) * np.exp(-t / 0.004)


def land(rng):
    """Click, then a soft settle: low thump and a second, quieter click."""
    n = int(0.6 * SR); t = np.arange(n) / SR
    s = np.zeros(n)
    c = click(rng); s[: len(c)] += c
    th = np.sin(2 * np.pi * (90 + 60 * np.exp(-t / 0.02)) * t) * np.exp(-t / 0.09)
    s += th * 0.9
    c2 = click(rng) * 0.35; k = int(0.085 * SR); s[k:k + len(c2)] += c2
    return s


def chime():
    n = int(2.4 * SR); t = np.arange(n) / SR
    s = np.zeros(n)
    for m, a, d in ((81, 1.0, 0.9), (85, 0.7, 0.8), (88, 0.5, 0.7), (93, 0.18, 0.4)):   # A5 C#6 E6 A6
        f = 440 * 2 ** ((m - 69) / 12)
        s += a * np.sin(2 * np.pi * f * t) * np.exp(-t / d)
    return s * np.minimum(1, t / 0.003)


def stone(variant):
    n = int(0.35 * SR); t = np.arange(n) / SR
    f = 180 if variant == 0 else 260
    return (np.sin(2 * np.pi * f * t) + 0.4 * np.sin(2 * np.pi * 2.7 * f * t) * np.exp(-t / 0.03)) * np.exp(-t / 0.07)


def thud():
    n = int(0.7 * SR); t = np.arange(n) / SR
    return np.sin(2 * np.pi * (55 + 50 * np.exp(-t / 0.025)) * t) * np.exp(-t / 0.16) * np.minimum(1, t / 0.003)


def render(kind, p, rng):
    return {"whoosh": lambda: whoosh(p, rng), "tick": lambda: tick(rng), "pop": pop, "ping": lambda: ping(p),
            "land": lambda: land(rng), "chime": chime, "click": lambda: click(rng), "stone": lambda: stone(p),
            "thud": thud}[kind]()


def track(duration, W):
    rng = np.random.default_rng(11)
    N = int(duration * SR); out = np.zeros(N)
    for t, kind, p in EVENTS:
        s = render(kind, p, rng)
        s = s / (np.max(np.abs(s)) + 1e-9) * 10 ** (LEVEL[kind] / 20)
        i = int(W(t) * SR)
        if 0 <= i < N:
            s = s[: N - i]; out[i:i + len(s)] += s
    return out
