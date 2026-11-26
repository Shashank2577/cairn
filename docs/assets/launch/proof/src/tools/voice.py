"""Narration: speak each phrase with Kokoro (voice af_heart), check it, and lay the lines and scenes out on one timeline.

    ~/cairn-video-audio/.venv/bin/python tools/voice.py

Writes assets/voice/*.wav (one per line), assets/voice.wav (the whole track), assets/timing.json and timing.js.
Each line is a list of phrases, one Kokoro call each, joined by short breaths; punctuation carries the prosody.
`pause` is the silence before the line (longer before the big figures). A scene keeps at least its designed length
and grows when its lines need more room. `cue` is where the line's visual beat sits in the scene's designed
choreography; proof.html and tools/sfx.py warp each scene so that beat lands when the line starts.
"""
import json
import re
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly
import wave

ROOT = Path(__file__).resolve().parent.parent
SR, KSR = 44100, 24000
TAIL = 0.6
TARGET_RMS_DB = -20.0

# scene id, designed length (s), lines: (caption, [phrases], speed, pause before, breath between phrases, cue)
SCRIPT = [
    ("s1", 5.5, [
        ("Your agent keeps its place, and gets smarter every session.",
         ["Your agent keeps its place...", "and gets smarter, every session."], .93, .6, .25, .6)]),
    ("s2", 13.5, [
        ("Cairn's Overview page, with sample data: context served.",
         ["This is Cairn's Overview page, with sample data.", "First: context served."], 1.0, .5, .2, .4),
        ("Agents read 19× fewer tokens than opening the files behind the answers.",
         ["Agents read nineteen times fewer tokens...", "than opening the files behind the answers."], 1.0, .6, .2, 4.0),
        ("New beside it: bytes pulled raw by tools. 6.5 MB across 1,284 calls.",
         ["New, right beside it: bytes pulled raw by tools.", "Six point five megabytes,", "across twelve hundred and eighty-four calls."],
         1.0, .4, .4, 7.6),
        ("A measurement only. Nothing is trimmed or blocked.",
         ["It's a measurement only.", "Nothing is trimmed, or blocked."], 1.0, .3, .15, 11.3)]),
    ("s3", 17.0, [
        ("Deep into a long session, Claude Code compacts the conversation.",
         ["Deep into a long session...", "Claude Code compacts the conversation."], .97, .5, .2, .4),
        ("Just before, Cairn's PreCompact hook saves a resume snapshot.",
         ["Just before it does, Cairn's pre-compact hook saves a resume snapshot."], 1.0, .3, .2, 3.8),
        ("Last request, files changed, open tasks, decisions. Under 2 KB.",
         ["The last request, the files changed, open tasks, decisions...", "all in under two kilobytes."], 1.0, .5, .35, 7.2),
        ("Pointers, not content: each line names the <code>cairn_session_*</code> tool to call.",
         ["Pointers, not content.", "Each line names the cairn session tool to call."], 1.0, .3, .2, 11.1),
        ("No model call. The agent picks up right where it was.",
         ["No model call.", "The agent picks up right where it was."], .97, .3, .2, 14.6)]),
    ("s4", 13.0, [
        ("Cairn also records what not to repeat.", ["Cairn also records... what not to repeat."], 1.0, .5, .2, .4),
        ("Rejected approaches, error-then-fix pairs, retry loops, user corrections.",
         ["Rejected approaches.", "Errors, and their fixes.", "Retry loops.", "And user corrections."], 1.04, .3, .15, 3.9),
        ("Only explicit signals count, and no model is called.",
         ["Only explicit signals count,", "and no model is called."], 1.0, .3, .15, 7.6),
        ("Next session, one search finds them.", ["Next session... one search finds them."], 1.0, .3, .2, 10.4)]),
    ("s5", 12.0, [
        ("Memory ages. The Memory page, with sample data.",
         ["Memory ages, too.", "Here's the Memory page, with sample data."], .97, .5, .2, .4),
        ("A memory no agent was told for 90 days is marked Stale and ranks lower.",
         ["A memory no agent was told for ninety days is marked stale...", "and ranks lower."], 1.0, .45, .15, 3.8),
        ("Conventions and decisions never age.", ["Conventions and decisions never age."], 1.0, .3, .2, 7.2),
        ("Nothing is deleted. Using it again restarts the clock.",
         ["Nothing is deleted.", "Using it again restarts the clock."], .97, .3, .2, 9.9)]),
    ("s6", 8.0, [
        ("Cairn. Install it once, then run <code>cairn</code> in your repository.",
         ["Cairn.", "Install it once,", "then run cairn in your repository."], .93, .8, .3, 1.0)]),
]


def trim(x, thresh=0.01, pad=0.04):
    idx = np.where(np.abs(x) > thresh)[0]
    if len(idx) == 0:
        return x
    a, b = max(0, idx[0] - int(pad * KSR)), min(len(x), idx[-1] + int(pad * KSR))
    return x[a:b]


def speak(pipe, text, speed):
    chunks = [np.asarray(a, dtype=np.float32).reshape(-1) for _, _, a in pipe(text, voice="af_heart", speed=speed)]
    return trim(np.concatenate(chunks)) if chunks else np.zeros(1, np.float32)


def expected(text, speed):
    words = len(re.findall(r"[A-Za-z']+", text))
    return words / (2.7 * speed)  # ~160 words a minute at speed 1


def main():
    from kokoro import KPipeline
    pipe = KPipeline(lang_code="a")
    out = ROOT / "assets" / "voice"
    out.mkdir(parents=True, exist_ok=True)
    clips, scenes, captions, report = [], {}, [], []
    t, n = 0.0, 0
    for sid, designed, lines in SCRIPT:
        start = t
        cur = start
        warp = [[0.0, 0.0]]
        for cap, phrases, speed, pause, breath, cue in lines:
            n += 1
            cur += pause
            parts = []
            for i, ph in enumerate(phrases):
                for attempt in range(3):
                    a = speak(pipe, ph, speed)
                    dur, peak = len(a) / KSR, float(np.max(np.abs(a)))
                    if dur >= .6 * expected(ph, speed) and peak < .999:
                        break
                    report.append(f"retry {n}.{i} ({dur:.2f}s, peak {peak:.3f}): {ph}")
                if i:
                    parts.append(np.zeros(int(breath * KSR), np.float32))
                parts.append(a)
            line = np.concatenate(parts).astype(np.float64)
            rms = np.sqrt(np.mean(line[np.abs(line) > .01] ** 2))
            line *= 10 ** (TARGET_RMS_DB / 20) / rms
            line = resample_poly(line, 147, 80)  # 24 kHz -> 44.1 kHz
            line /= max(1.0, np.max(np.abs(line)) / .95)
            dur = len(line) / SR
            with wave.open(str(out / f"{n:02d}.wav"), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
                w.writeframes((line * 32767).astype("<i2").tobytes())
            clips.append((cur, line))
            captions.append([round(cur, 3), round(cur + dur, 3), cap, " ".join(phrases), sid])
            warp.append([round(cur - start, 3), cue])
            report.append(f"{n:02d} {sid} {dur:5.2f}s speed {speed} peak {np.max(np.abs(line)):.2f}  {' '.join(phrases)}")
            cur += dur
        end = max(start + designed, cur + TAIL)
        warp.append([round(end - start, 3), designed])
        scenes[sid] = {"start": round(start, 3), "end": round(end, 3), "designed": designed, "warp": warp}
        t = end
    total = round(t, 3)
    track = np.zeros(int(SR * (total + 1)))
    for at, data in clips:
        i = int(at * SR)
        track[i:i + len(data)] += data
    with wave.open(str(ROOT / "assets" / "voice.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(track, -1, 1) * 32767).astype("<i2").tobytes())
    timing = {"duration": total, "scenes": scenes, "captions": captions}
    (ROOT / "assets" / "timing.json").write_text(json.dumps(timing, indent=1), encoding="utf-8")
    (ROOT / "timing.js").write_text("// generated by tools/voice.py\nwindow.TIMING = " + json.dumps(timing) + ";\n", encoding="utf-8")
    print("\n".join(report))
    for sid, s in scenes.items():
        print(f"{sid}: {s['start']:6.2f} -> {s['end']:6.2f} ({s['end'] - s['start']:5.2f}s, designed {s['designed']})")
    print("total", total)


if __name__ == "__main__":
    main()
