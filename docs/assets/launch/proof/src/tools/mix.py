"""Mix voice, score and sound effects, then master to -16 LUFS with true peak under -1.5 dBFS (loudnorm targets -2.2 so the AAC encode stays under).

    ~/cairn-video-audio/.venv/bin/python tools/mix.py   # reads assets/voice.wav, score.wav, sfx.wav, timing.json
                                                        # writes assets/mix.m4a and prints per-scene levels

Levels are set against the voice's RMS over its spoken parts:
  score  12 dB under the voice between lines, ducked a further 8 dB while a line is spoken (0.3 s ramps)
  sfx    peaks 6 dB under the voice's peak level between lines, a further 6 dB down while a line is spoken
Then a light limiter (ffmpeg alimiter) and a two-pass linear loudnorm to -16 LUFS, TP -2.2.
"""
import json
import re
import subprocess
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
A = ROOT / "assets"
SR = 44100
SCORE_UNDER, DUCK, SFX_UNDER, SFX_DUCK, RAMP = 12.0, 8.0, 6.0, 6.0, 0.3


def read(path, n):
    with wave.open(str(path)) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64) / 32768
        a = a.reshape(-1, w.getnchannels())
    if a.shape[1] == 1:
        a = np.repeat(a, 2, 1)
    return np.pad(a, ((0, max(0, n - len(a))), (0, 0)))[:n]


T = json.loads((A / "timing.json").read_text())
n = int(SR * T["duration"])
voice, score, sfx = read(A / "voice.wav", n), read(A / "score.wav", n), read(A / "sfx.wav", n)

speaking = np.zeros(n)
for start, end, *_ in T["captions"]:
    speaking[int(start * SR):int(end * SR)] = 1
k = int(RAMP * SR)
speaking = np.clip(np.convolve(speaking, np.ones(k) / k, mode="same"), 0, 1)

rms = lambda x: np.sqrt(np.mean(np.square(x)) + 1e-12)
v = voice[:, 0]
v_rms = rms(v[np.abs(v) > .01])
s_rms = rms(score.mean(1)[int(5.5 * SR):])
score_gain = v_rms / s_rms * 10 ** (-(SCORE_UNDER + DUCK * speaking) / 20)
sfx_gain = np.percentile(np.abs(v[np.abs(v) > .01]), 99.5) / np.max(np.abs(sfx)) * 10 ** (-(SFX_UNDER + SFX_DUCK * speaking) / 20)
mix = voice + score * score_gain[:, None] + sfx * sfx_gain[:, None]
mix /= max(1.0, np.max(np.abs(mix)) / .98)
pre = A / "mix-pre.wav"
with wave.open(str(pre), "wb") as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((mix * 32767).astype("<i2").tobytes())

limit = "alimiter=limit=0.6:attack=5:release=60:level=false"
first = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(pre), "-af", f"{limit},loudnorm=I=-16:TP=-2.2:LRA=11:print_format=json",
                        "-f", "null", "-"], capture_output=True, text=True).stderr
m = json.loads(re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", first, re.S).group(0))
af = (f"{limit},loudnorm=I=-16:TP=-2.2:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
      f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true,aresample={SR}")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(pre), "-af", af, "-c:a", "aac", "-b:a", "192k", str(A / "mix.m4a")], check=True)
pre.unlink()

# report: per-scene RMS of each stem as it sits in the mix (before mastering gain)
db = lambda x: 20 * np.log10(rms(x))
print("scene   voice   score   sfx    (dBFS RMS in the pre-master mix)")
for sid, s in T["scenes"].items():
    a, b = int(s["start"] * SR), int(s["end"] * SR)
    print(f"{sid}    {db(voice[a:b, 0]):6.1f}  {db((score * score_gain[:, None])[a:b, 0]):6.1f}  {db((sfx * sfx_gain[:, None])[a:b, 0]):6.1f}")
