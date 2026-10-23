"""Voice the narration, retime the video around it, and mix voice, score and effects.

    PYTHON narrate.py WORKDIR        (PYTHON needs kokoro and numpy; ffmpeg and ffprobe on PATH)

1. Voice: each line of narration.js ([text, speed, pause]) goes to Kokoro (voice af_heart), one call
   per line; the 24 kHz chunks are joined and resampled to 44.1 kHz. Every clip is checked: its
   length against a words-per-second estimate, and its peak and RMS. A clip that clips, is silent
   or is far shorter than expected is rendered again at a slightly different speed.
2. Timing: each line starts its pause after the previous one ends (or the original gap, whichever
   is longer) and its caption lasts at least as long as before. Writes timing.js (caption windows,
   the old→new warp the page uses, the duration) and WORKDIR/timing.json.
3. Mix: score (music.py) ducked 8 dB while the voice speaks, effects (sfx.py) on their on-screen
   events, voice on top. Gain to -16 LUFS integrated, then a light limiter for a true peak under
   -1.5 dBFS. Writes WORKDIR/mix.wav and prints the measurements.
"""
import json
import re
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import music  # noqa: E402
import sfx    # noqa: E402

WORK = Path(sys.argv[1]).resolve()
WORK.mkdir(parents=True, exist_ok=True)
SR = 44100
ORIGINAL = 112.0


def js_array(path, name):
    src = (HERE / path).read_text()
    body = re.search(rf"window\.{name}\s*=\s*(\[.*?\n\]);", src, re.S).group(1)
    return json.loads(re.sub(r",\s*\]", "]", body))


def run(*cmd):
    return subprocess.run([str(c) for c in cmd], check=True, capture_output=True, text=True)


def write_wav(path, x):
    x = x if x.ndim == 2 else x[:, None]
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(x.shape[1]); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())


def read_wav(path):
    with wave.open(str(path)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float64) / 32768
        return x.reshape(-1, w.getnchannels())


def ebur(path):
    out = run("ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-").stderr
    tail = out[out.rindex("Summary:"):]
    return float(re.search(r"I:\s+(-?[\d.]+) LUFS", tail).group(1)), float(re.search(r"Peak:\s+(-?[\d.inf]+) dBFS", tail).group(1))


captions = js_array("captions.js", "CAPTIONS")
lines = js_array("narration.js", "NARRATION")
assert len(captions) == len(lines), "one narration line per caption"

# 1. voice
from kokoro import KPipeline  # noqa: E402
pipe = KPipeline(lang_code="a")


def speak(text, speed, path):
    audio = np.concatenate([np.asarray(chunk.audio if hasattr(chunk, "audio") else chunk[2], dtype=np.float32)
                            for chunk in pipe(text, voice="af_heart", speed=speed)])
    raw = path.with_suffix(".24k.wav")
    write_wav_24 = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(str(raw), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(write_wav_24.tobytes())
    run("ffmpeg", "-y", "-v", "error", "-i", raw, "-ar", SR, path)
    x = read_wav(path)[:, 0]
    # trim leading/trailing near-silence so the pauses are the ones we set
    idx = np.where(np.abs(x) > 0.01)[0]
    x = x[max(0, idx[0] - int(0.03 * SR)): idx[-1] + int(0.08 * SR)]
    write_wav(path, x)
    d = float(run("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path).stdout)
    return x, d


clips, report = [], []
for i, (text, speed, pause) in enumerate(lines):
    words = len(re.findall(r"[A-Za-z']+", text))
    expected = words / (2.7 * speed)                       # af_heart speaks about 2.7 words a second
    for attempt in range(3):
        x, d = speak(text, speed * (1 - 0.02 * attempt), WORK / f"vo{i:02d}.wav")
        peak, rms = float(np.max(np.abs(x))), float(np.sqrt(np.mean(x ** 2)))
        ok = peak < 0.99 and rms > 0.01 and d > 0.6 * expected
        if ok:
            break
    report.append({"line": i + 1, "seconds": round(d, 2), "expected": round(expected, 2),
                   "peak_db": round(20 * np.log10(peak), 1), "rms_db": round(20 * np.log10(rms), 1), "ok": ok})
    clips.append((x, d, pause))

# 2. timing
new, warp = [], [[0.0, 0.0]]
prev_old_end = prev_new_end = None
for (a, b, text), (_, d, pause) in zip(captions, clips):
    A = a if prev_new_end is None else prev_new_end + max(pause, a - prev_old_end)
    B = A + max(b - a, d + 0.2)
    new.append([round(A, 3), round(B, 3), text, round(d, 3)])
    warp += [[a, round(A, 3)], [b, round(B, 3)]]
    prev_old_end, prev_new_end = b, B
duration = round(prev_new_end + 3.2, 3)                  # end card holds while the final chord rings
warp.append([ORIGINAL, duration])
assert duration < 140, duration

(HERE / "timing.js").write_text(
    "// Generated by narrate.py from captions.js, narration.js and the measured voice clips; do not edit.\n"
    "// VO: [start, end, caption, voice seconds] on the narrated timeline. WARP: [old, new] anchors that map\n"
    "// the narrated time back to the original 112 s timeline the scenes are written on.\n"
    f"window.VO = {json.dumps(new)};\nwindow.WARP = {json.dumps(warp)};\nwindow.DURATION = {duration};\n")
(WORK / "timing.json").write_text(json.dumps({"duration": duration, "warp": warp, "vo": new}))


def W(old):
    for (a0, b0), (a1, b1) in zip(warp, warp[1:]):
        if old <= a1:
            return b0 + (old - a0) * (b1 - b0) / (a1 - a0) if a1 > a0 else b0
    return warp[-1][1] + old - warp[-1][0]


# 3. mix
N = int(duration * SR)
voice = np.zeros(N); speaking = np.zeros(N)
for (x, d, _), (A, B, _, _) in zip(clips, new):
    i = int((A + 0.05) * SR); x = x[: N - i]
    voice[i:i + len(x)] += x
    speaking[max(0, i - int(0.12 * SR)): min(N, i + len(x) + int(0.2 * SR))] = 1
score = music.score(duration, W)[:N]
effects = sfx.track(duration, W)[:N]

write_wav(WORK / "voice.wav", voice); write_wav(WORK / "score.wav", score * 0.5); write_wav(WORK / "sfx.wav", effects * 0.3)
lv, _ = ebur(WORK / "voice.wav"); lm, _ = ebur(WORK / "score.wav"); _, _ = ebur(WORK / "sfx.wav")

# levels: score 12 dB under the voice between lines, ducked a further 8 dB while it speaks;
# effects peak about 8 dB under the voice's speech peaks
k = int(0.2 * SR)
duck = 10 ** (-8 * np.convolve(speaking, np.ones(k) / k, mode="same") / 20)
score_gain = 10 ** ((lv - 12 - lm) / 20) * 0.5
voice_peak = np.percentile(np.abs(voice[np.abs(voice) > 1e-3]), 99.5)
sfx_gain = voice_peak * 10 ** (-8 / 20) / max(1e-9, np.max(np.abs(effects)))
mix = score * (score_gain * duck)[:, None] + (voice + effects * sfx_gain)[:, None]
write_wav(WORK / "premix.wav", mix * 0.5)
li, _ = ebur(WORK / "premix.wav")
gain_db = -16.0 - li
run("ffmpeg", "-y", "-v", "error", "-i", WORK / "premix.wav", "-af",
    f"volume={gain_db:.2f}dB,alimiter=limit=0.79:attack=3:release=60:level=disabled", "-ar", SR, WORK / "mix.wav")
mi, mp = ebur(WORK / "mix.wav")

# per-section RMS of the final mix
mixed = read_wav(WORK / "mix.wav").mean(1)
sections = {"question": (0, W(20)), "layers": (W(20), W(52)), "pack+edit": (W(52), W(80)),
            "remember": (W(80), W(94)), "local": (W(94), W(104)), "end": (W(104), duration)}
rms = {k: round(20 * np.log10(np.sqrt(np.mean(mixed[int(a * SR):int(b * SR)] ** 2)) + 1e-12), 1) for k, (a, b) in sections.items()}
gaps = np.concatenate([mixed[int((B + 0.4) * SR):int((n2[0] - 0.1) * SR)] for (_, B, _, _), n2 in zip(new, new[1:]) if n2[0] - B > 0.6])
speech = mixed[speaking[:len(mixed)] > 0]
print(json.dumps({"duration": duration, "clips": report, "voice_lufs": lv, "score_lufs_raw": lm,
                  "mix_lufs": mi, "mix_true_peak_db": mp, "section_rms_db": rms,
                  "speech_rms_db": round(20 * np.log10(np.sqrt(np.mean(speech ** 2))), 1),
                  "between_lines_rms_db": round(20 * np.log10(np.sqrt(np.mean(gaps ** 2))), 1) if len(gaps) else None}, indent=1))
