"""Mix the narration over the music bed and bring the result to -16 LUFS.

    python3 tools/mix.py        # reads assets/voice.wav, assets/music.wav, assets/timing.json; writes assets/mix.m4a

The bed sits 14 dB under the voice between lines and ducks to 22 dB under it while a line is spoken (0.25 s ramps).
Levels are matched on RMS over the spoken parts; a two-pass ffmpeg loudnorm then sets the whole mix to -16 LUFS.
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
UNDER_IDLE, UNDER_SPEECH, RAMP = 14.0, 22.0, 0.25


def read(path):
    with wave.open(str(path)) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64) / 32768
        return a.reshape(-1, w.getnchannels())


timing = json.loads((A / "timing.json").read_text())
voice = read(A / "voice.wav")[:, 0]
music = read(A / "music.wav")
n = int(SR * timing["duration"])
voice = np.pad(voice, (0, max(0, n - len(voice))))[:n]
music = np.pad(music, ((0, max(0, n - len(music))), (0, 0)))[:n]

speaking = np.zeros(n)
for start, end, *_ in timing["captions"]:
    speaking[int(start * SR):int(end * SR)] = 1
k = int(RAMP * SR)
speaking = np.convolve(speaking, np.ones(k) / k, mode="same")  # smooth the duck in and out

rms = lambda x: np.sqrt(np.mean(np.square(x)) + 1e-12)
v_rms = rms(voice[speaking > .5])
m_rms = rms(music.mean(axis=1)[int(5.5 * SR):])  # the bed once the pulse is in
db = UNDER_IDLE + (UNDER_SPEECH - UNDER_IDLE) * speaking
gain = (v_rms / m_rms) * 10 ** (-db / 20)
mix = music * gain[:, None] + voice[:, None]
mix /= max(1.0, np.max(np.abs(mix)) / 0.98)
tmp = A / "mix-pre.wav"
with wave.open(str(tmp), "wb") as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((mix * 32767).astype("<i2").tobytes())

first = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(tmp), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
                        "-f", "null", "-"], capture_output=True, text=True).stderr
m = json.loads(re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", first, re.S).group(0))
af = (f"loudnorm=I=-16:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
      f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp), "-af", af, "-ar", str(SR), "-c:a", "aac", "-b:a", "160k",
                str(A / "mix.m4a")], check=True)
tmp.unlink()
print("mix.m4a written; voice/music gap", UNDER_IDLE, "dB idle,", UNDER_SPEECH, "dB under speech")
