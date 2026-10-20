"""Voiceover: python3 narration.py   (macOS `say`, ffmpeg, ffprobe, numpy)

1. Speaks one line per caption with `say -v Samantha -r 175` into voice/NN.aiff.
2. Retimes the film around the voice: each caption window is stretched so its line ends at least
   0.3 s before the window closes, with at least 0.3 s between windows. The result is timing.json:
   new caption times plus time-warp knots (new time -> original time) that index.html applies, so
   every scene's animation stretches with its line.
3. Builds voice.wav, regenerates music.wav to the new length (music.py reads timing.json), ducks
   the music under the voice, and writes mix.wav at about -16 LUFS. render.mjs muxes mix.wav.
"""
import json
import re
import subprocess
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SR = 44100
VOICE, RATE = "Samantha", "175"
LEAD = 0.05   # the voice starts this long after its caption appears
AIR = 0.3     # minimum silence after a line before its caption closes, and between windows
ORIG_DURATION = 57.0

# Spoken versions of the 16 captions, same facts, numbers read naturally.
SPOKEN = [
    "Your coding agents are capable.",
    "But every session starts from zero.",
    "They re-read the same files.",
    "They retry the fix your team reverted.",
    "After compaction, they lose their place.",
    "And you pay for every re-read, in tokens.",
    "Cairn is institutional memory for coding agents.",
    "Five layers: map, specs, timeline, memory, and sessions.",
    "Before an edit, an agent asks Cairn: impact, why, context.",
    "It gets a cited answer, inside a token budget.",
    "On Cairn's own repo, one answer took three hundred ninety-one tokens,",
    "instead of a hundred forty-four thousand, two hundred twenty-eight tokens, across thirty-three files.",
    "After compaction, the next session picks up where it left off.",
    "One command, in any git repo. Local. No A P I key.",
    "Works with Claude Code, Codex, Cursor, Gemini, Copilot, and Open Code.",
    "Cairn. Memory your agents can cite.",
]


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def captions():
    src = (HERE / "index.html").read_text(encoding="utf-8")
    block = src[src.index("window.CAPTIONS = ["): src.index("];", src.index("window.CAPTIONS = ["))]
    return [(float(a), float(b), t) for a, b, t in re.findall(r'\[([\d.]+), ([\d.]+), "([^"]+)"\]', block)]


def read_wav(path):
    with wave.open(str(path)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64) / 32768
        return x.reshape(-1, w.getnchannels()).mean(axis=1) if w.getnchannels() > 1 else x


def write_wav(path, x):
    x = np.clip(x, -1, 1)
    pcm = (x * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1 if x.ndim == 1 else x.shape[1])
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def lufs(path):
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", out)[-1])


def main():
    caps = captions()
    assert len(caps) == len(SPOKEN), (len(caps), len(SPOKEN))
    vdir = HERE / "voice"
    vdir.mkdir(exist_ok=True)
    clips = []
    for i, line in enumerate(SPOKEN):
        aiff, wav = vdir / f"{i + 1:02d}.aiff", vdir / f"{i + 1:02d}.wav"
        run("say", "-v", VOICE, "-r", RATE, "-o", str(aiff), line)
        run("ffmpeg", "-y", "-v", "error", "-i", str(aiff), "-ar", str(SR), "-ac", "1", str(wav))
        d = float(run("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(wav)))
        clips.append((wav, d))

    # retime: new caption windows, plus warp knots new -> old
    new_caps, knots = [], [[0.0, 0.0]]
    t_new = caps[0][0]
    for i, ((a, b, text), (_, d)) in enumerate(zip(caps, clips)):
        if i:
            gap = max(a - caps[i - 1][1], AIR)
            t_new = new_caps[-1][1] + gap
        start = t_new
        end = start + max(b - a, LEAD + d + AIR)
        new_caps.append([round(start, 3), round(end, 3), text])
        knots += [[start, a], [end, b]]
    duration = round(new_caps[-1][1] + (ORIG_DURATION - caps[-1][1]), 3)
    knots.append([duration, ORIG_DURATION])
    timing = {"duration": duration, "captions": new_caps, "knots": [[round(n, 4), o] for n, o in knots],
              "voice": [[c[0] + LEAD, round(d, 3)] for c, (_, d) in zip(new_caps, clips)]}
    (HERE / "timing.json").write_text(json.dumps(timing, indent=1))

    # voice track
    voice = np.zeros(int(duration * SR) + SR)
    for (start, _, _), (wav, _) in zip(new_caps, clips):
        x = read_wav(wav)
        i = int((start + LEAD) * SR)
        voice[i:i + len(x)] += x
    voice = voice[: int(duration * SR)]
    write_wav(HERE / "voice.wav", voice)

    # music to the new length, then duck it under the voice
    run("python3", str(HERE / "music.py"))
    with wave.open(str(HERE / "music.wav")) as w:
        ms = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64).reshape(-1, 2) / 32768
    n = min(len(ms), len(voice))
    ms, voice = ms[:n], voice[:n]
    lv, lm = lufs(HERE / "voice.wav"), lufs(HERE / "music.wav")
    bed = 10 ** (((lv - 22) - lm) / 20)          # music bed sits about 22 dB under the voice
    # ducking envelope: 1 between lines, -6 dB more while the voice speaks, 150 ms ramps
    duck = np.ones(n)
    for start, d in timing["voice"]:
        duck[int((start - 0.15) * SR): int((start + d + 0.1) * SR)] = 10 ** (-6 / 20)
    k = int(0.15 * SR)
    duck = np.convolve(duck, np.ones(k) / k, "same")
    mix = ms * (bed * duck)[:, None] + voice[:, None]
    write_wav(HERE / "mix-pre.wav", mix)
    gain = 10 ** ((-16 - lufs(HERE / "mix-pre.wav")) / 20)
    mix *= gain
    peak = np.max(np.abs(mix))
    if peak > 0.84:  # about -1.5 dBFS: soft-limit only the rare peaks
        mix = np.where(np.abs(mix) > 0.7, np.sign(mix) * (0.7 + 0.14 * np.tanh((np.abs(mix) - 0.7) / 0.14)), mix)
    write_wav(HERE / "mix.wav", mix)
    (HERE / "mix-pre.wav").unlink()
    print(json.dumps({"duration": duration, "voice_lufs": lv, "music_lufs_raw": lm, "mix_lufs": lufs(HERE / "mix.wav")}))


if __name__ == "__main__":
    main()
