"""Voiceover, retiming and the final mix. Run with a Python that has `kokoro` installed:

    ~/cairn-video-audio/.venv/bin/python narration.py

1. Speaks one phrase per caption with Kokoro (voice af_heart, 24 kHz), checks each clip's length,
   RMS and peak, and re-renders a clip that fails.
2. Retimes the film around the voice: each caption window is stretched so its line fits, and the
   silence between lines follows PAUSE (longer before the big number and the closing line). The
   result is timing.json: new caption times plus time-warp knots (new time -> original time) that
   index.html, music.py and sfx.py all use, so picture, score and effects stay in sync.
3. Runs music.py and sfx.py, ducks the music (-8 dB) and the effects (-4 dB) under the voice with a
   sidechain-style envelope, and masters to -16 LUFS with a true-peak limiter (ffmpeg alimiter).
   Writes mix.wav, which render.mjs muxes.
"""
import json
import re
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SR = 44100
LEAD = 0.05   # the voice starts this long after its caption appears
TAIL = 0.10   # the caption stays this long after its line ends (minimum)
ORIG_DURATION = 57.0
MUSIC_UNDER_VOICE_DB = 10   # music bed level below the voice before ducking
MUSIC_DUCK_DB = 8
SFX_UNDER_VOICE_DB = 7
SFX_DUCK_DB = 4

# (spoken text, speed, silence before this line in seconds). One per caption in index.html.
LINES = [
    ("Your coding agents are capable.", 0.92, 0.0),
    ("But every session... starts from zero.", 0.92, 0.45),
    ("They re-read the same files.", 1.05, 0.2),
    ("They retry the fix your team already reverted.", 1.05, 0.2),
    ("After compaction, they lose their place.", 1.05, 0.2),
    ("And every re-read? You pay for it... in tokens.", 1.0, 0.25),
    ("This is Cairn: institutional memory, for coding agents.", 1.0, 0.55),
    ("Five layers: map, specs, timeline, memory, and sessions.", 1.0, 0.35),
    ("Before an edit, your agent asks Cairn: impact, why, context.", 1.0, 0.35),
    ("And it gets a cited answer, inside a token budget.", 1.0, 0.3),
    ("On Cairn's own repo, one answer took... three hundred and ninety-one tokens.", 0.96, 0.55),
    ("Not a hundred and forty-four thousand, two hundred and twenty-eight tokens, across thirty-three files.", 1.0, 0.35),
    ("Three hundred and sixty-nine times fewer.", 0.92, 0.6),
    ("And after compaction, the next session picks up... where it left off.", 1.0, 0.45),
    ("One command, in any git repo. Local. No API key.", 1.0, 0.4),
    ("Works with Claude Code, Codex, Cursor, Gemini, Copilot, and Open Code.", 1.0, 0.3),
    ("Cairn. Memory your agents can cite.", 0.92, 0.6),
]


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def captions():
    src = (HERE / "index.html").read_text(encoding="utf-8")
    start = src.index("window.CAPTIONS = [")
    block = src[start: src.index("];", start)]
    return [(float(a), float(b), t) for a, b, t in re.findall(r'\[([\d.]+), ([\d.]+), "([^"]+)"\]', block)]


def read_wav(path):
    with wave.open(str(path)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64) / 32768
        return x.reshape(-1, w.getnchannels())


def write_wav(path, x, sr=SR):
    x = np.clip(x, -1, 1)
    if x.ndim == 1:
        x = x[:, None]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(x.shape[1])
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((x * 32767).astype("<i2").tobytes())


def loudness(path):
    """Integrated loudness and true peak, from ffmpeg's loudnorm analysis."""
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "loudnorm=print_format=json", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    j = json.loads(err[err.rindex("{"): err.rindex("}") + 1])
    return float(j["input_i"]), float(j["input_tp"])


def db(x):
    return 20 * np.log10(max(float(x), 1e-9))


def speak():
    import os
    os.environ.setdefault("HF_HUB_OFFLINE", "1")  # use the locally cached model; never download
    from kokoro import KPipeline  # imported here so the rest of the module needs only numpy
    pipe = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
    vdir = HERE / "voice"
    vdir.mkdir(exist_ok=True)
    clips = []
    for i, (text, speed, _) in enumerate(LINES):
        words = len(re.findall(r"[A-Za-z']+", text))
        expected = words / 2.9 / speed  # seconds, a conservative floor for this voice
        for attempt in range(3):
            audio = np.concatenate([np.asarray(r.audio, dtype=np.float64) for r in pipe(text, voice="af_heart", speed=speed)])
            rms, peak, length = np.sqrt(np.mean(audio ** 2)), np.max(np.abs(audio)), len(audio) / 24000
            ok = rms > 0.02 and peak < 0.99 and length > 0.6 * expected
            if ok:
                break
            speed *= 0.97
            print(f"  re-render line {i + 1}: rms {rms:.3f} peak {peak:.2f} {length:.2f}s vs {expected:.2f}s")
        if not ok:
            raise SystemExit(f"line {i + 1} failed its checks: {text}")
        # trim leading/trailing near-silence so the pauses are ours, then resample to 44.1 kHz
        idx = np.nonzero(np.abs(audio) > 0.01)[0]
        audio = audio[max(0, idx[0] - 240): idx[-1] + 2400]
        raw = vdir / f"{i + 1:02d}-24k.wav"
        wav = vdir / f"{i + 1:02d}.wav"
        write_wav(raw, audio * 0.95 / max(peak, 1e-6) if peak > 0.95 else audio, sr=24000)
        run("ffmpeg", "-y", "-v", "error", "-i", str(raw), "-ar", str(SR), str(wav))
        x = read_wav(wav)[:, 0]
        clips.append((x, len(x) / SR))
        print(f"line {i + 1:2d}  {len(x) / SR:5.2f}s  rms {db(rms):6.1f} dB  peak {db(peak):5.1f} dB  speed {speed:.2f}")
    return clips


def retime(caps, clips):
    new_caps, knots = [], [[0.0, 0.0]]
    for i, ((a, b, text), (_, d), (_, _, pause)) in enumerate(zip(caps, clips, LINES)):
        start = caps[0][0] if i == 0 else new_caps[-1][1] + max(pause - LEAD - TAIL, 0.0)
        end = start + max(b - a, LEAD + d + TAIL)
        new_caps.append([round(start, 3), round(end, 3), text])
        knots += [[round(start, 4), a], [round(end, 4), b]]
    duration = round(new_caps[-1][1] + (ORIG_DURATION - caps[-1][1]), 3)
    knots.append([duration, ORIG_DURATION])
    return {"duration": duration, "captions": new_caps, "knots": knots,
            "voice": [[round(c[0] + LEAD, 3), round(d, 3)] for c, (_, d) in zip(new_caps, clips)]}


def duck_envelope(voice, depth_db, attack=0.04, release=0.35):
    """Sidechain-style gain: depth_db of reduction while the voice is above a gate, smoothed."""
    hop = SR // 100
    frames = len(voice) // hop + 1
    rms = np.array([np.sqrt(np.mean(voice[k * hop:(k + 1) * hop] ** 2)) if k * hop < len(voice) else 0 for k in range(frames)])
    target = (rms > 0.006).astype(float)
    a_up, a_dn = np.exp(-0.01 / attack), np.exp(-0.01 / release)
    env, g = np.zeros(frames), 0.0
    for k in range(frames):
        coef = a_up if target[k] > g else a_dn
        g = coef * g + (1 - coef) * target[k]
        env[k] = g
    env = np.interp(np.arange(len(voice)) / hop, np.arange(frames), env)
    return 10 ** (-depth_db * env / 20)


def short_term_max(x, win=0.4):
    m = x.mean(axis=1) if x.ndim > 1 else x
    n = int(win * SR)
    c = np.convolve(m ** 2, np.ones(n) / n, "valid")
    return np.sqrt(np.max(c))


def main():
    caps = captions()
    assert len(caps) == len(LINES), (len(caps), len(LINES))
    clips = speak()
    timing = retime(caps, clips)
    (HERE / "timing.json").write_text(json.dumps(timing, indent=1))
    duration = timing["duration"]
    n = int(round(duration * SR))

    voice = np.zeros(n + SR)
    for (start, _), (x, _) in zip(timing["voice"], clips):
        i = int(start * SR)
        voice[i:i + len(x)] += x
    voice = voice[:n]
    write_wav(HERE / "voice.wav", voice)

    run(sys.executable, str(HERE / "music.py"))
    run(sys.executable, str(HERE / "sfx.py"))
    music = read_wav(HERE / "music.wav")[:n]
    sfx = read_wav(HERE / "sfx.wav")[:n]
    music = np.pad(music, ((0, n - len(music)), (0, 0)))
    sfx = np.pad(sfx, ((0, n - len(sfx)), (0, 0)))

    lv, _ = loudness(HERE / "voice.wav")
    lm, _ = loudness(HERE / "music.wav")
    music *= 10 ** (((lv - MUSIC_UNDER_VOICE_DB) - lm) / 20)
    sfx *= 10 ** ((db(short_term_max(voice)) - SFX_UNDER_VOICE_DB - db(short_term_max(sfx))) / 20)
    music *= duck_envelope(voice, MUSIC_DUCK_DB)[:, None]
    sfx *= duck_envelope(voice, SFX_DUCK_DB, attack=0.02, release=0.2)[:, None]
    premix = voice[:, None] + music + sfx
    write_wav(HERE / "premix.wav", premix * 0.5)  # headroom; the master stage sets the level

    gain = -16 - loudness(HERE / "premix.wav")[0]
    for _ in range(4):
        run("ffmpeg", "-y", "-v", "error", "-i", str(HERE / "premix.wav"), "-af",
            f"volume={gain:.2f}dB,alimiter=limit=0.79:attack=4:release=80:level=disabled,aresample=44100",
            "-c:a", "pcm_s16le", str(HERE / "mix.wav"))
        li, tp = loudness(HERE / "mix.wav")
        if abs(li + 16) <= 0.3 and tp <= -1.5:
            break
        gain += -16 - li
        if tp > -1.5:
            gain -= 0.3
    (HERE / "premix.wav").unlink()

    # report: per-caption RMS (voice scenes) against the music-only stretches between lines
    mix = read_wav(HERE / "mix.wav")[:, 0]
    rep = []
    for (s, d), c in zip(timing["voice"], timing["captions"]):
        seg = mix[int(s * SR): int((s + d) * SR)]
        rep.append((c[2][:44], db(np.sqrt(np.mean(seg ** 2)))))
    gaps = []
    for (s0, d0), (s1, _) in zip(timing["voice"], timing["voice"][1:]):
        if s1 - (s0 + d0) > 0.8:
            seg = mix[int((s0 + d0 + 0.3) * SR): int((s1 - 0.1) * SR)]
            gaps.append(db(np.sqrt(np.mean(seg ** 2))))
    for text, r in rep:
        print(f"  voice  {r:6.1f} dB  {text}")
    print(f"  music-only stretches: {', '.join(f'{g:.1f}' for g in gaps)} dB")
    print(json.dumps({"duration": duration, "voice_lufs": lv, "mix_lufs": li, "true_peak": tp}))


if __name__ == "__main__":
    main()
