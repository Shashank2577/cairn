"""Narration: speak each line with macOS `say`, measure it, and lay the lines and scenes out on one timeline.

    python3 tools/voice.py          # writes assets/voice/*.aiff, assets/voice.wav and timing.js

Each scene keeps at least its designed length and grows when its lines need more room: a lead before the first
line, GAP seconds of air between lines, a tail after the last. timing.js carries the scene windows and the caption
times (one caption per spoken line) to proof.html, tools/music.py and tools/mix.py.
"""
import json
import subprocess
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
VOICE, RATE, SR = "Samantha", 175, 44100
GAP, LEAD, TAIL = 0.3, 0.5, 0.6

# scene id, designed length (s), [(caption on screen, words spoken)]
SCRIPT = [
    ("s1", 5.5, [("Your agent keeps its place, and gets smarter every session.",
                  "Your agent keeps its place, and gets smarter every session.")]),
    ("s2", 13.5, [
        ("Cairn's Overview page, with sample data: context served.",
         "Cairn's Overview page, with sample data. First, context served."),
        ("Agents read 19× fewer tokens than opening the files behind the answers.",
         "Agents read nineteen times fewer tokens than opening the files behind the answers."),
        ("New beside it: bytes pulled raw by tools. 6.5 MB across 1,284 calls.",
         "New beside it: bytes pulled raw by tools. Six point five megabytes, across twelve hundred and eighty-four calls."),
        ("A measurement only. Nothing is trimmed or blocked.",
         "It's a measurement only. Nothing is trimmed or blocked.")]),
    ("s3", 17.0, [
        ("Deep into a long session, Claude Code compacts the conversation.",
         "Deep into a long session, Claude Code compacts the conversation."),
        ("Just before, Cairn's PreCompact hook saves a resume snapshot.",
         "Just before that, Cairn's pre-compact hook saves a resume snapshot."),
        ("Last request, files changed, open tasks, decisions. Under 2 KB.",
         "The last request, files changed, open tasks and decisions, in under two kilobytes."),
        ("Pointers, not content: each line names the <code>cairn_session_*</code> tool to call.",
         "Pointers, not content. Each line names the cairn session tool to call."),
        ("No model call. The agent picks up where it was.",
         "No model call. The agent picks up where it was.")]),
    ("s4", 13.0, [
        ("Cairn also records what not to repeat.", "Cairn also records what not to repeat."),
        ("Rejected approaches, error-then-fix pairs, retry loops, user corrections.",
         "Rejected approaches, errors and their fixes, retry loops, and user corrections."),
        ("Only explicit signals count, and no model is called.", "Only explicit signals count, and no model is called."),
        ("Next session, one search finds them.", "Next session, one search finds them.")]),
    ("s5", 12.0, [
        ("Memory ages. The Memory page, with sample data.", "Memory ages, too. Here is the Memory page, with sample data."),
        ("A memory no agent was told for 90 days is marked Stale and ranks lower.",
         "A memory no agent was told for ninety days is marked stale, and ranks lower."),
        ("Conventions and decisions never age.", "Conventions and decisions never age."),
        ("Nothing is deleted. Being used again restarts the clock.",
         "Nothing is deleted. Being used again restarts the clock.")]),
    ("s6", 8.0, [("Cairn. Install it once, then run <code>cairn</code> in your repository.",
                   "Cairn. Install it once, then run cairn in your repository.")]),
]


def read_wav(path):
    with wave.open(str(path)) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
        return a.reshape(-1, w.getnchannels()).mean(axis=1)


def main():
    out = ROOT / "assets" / "voice"
    out.mkdir(parents=True, exist_ok=True)
    clips, scenes, captions = [], {}, []
    t, n = 0.0, 0
    for sid, designed, lines in SCRIPT:
        start = t
        cur = start + (0.6 if sid == "s1" else LEAD)
        for cap, spoken in lines:
            n += 1
            aiff, wav = out / f"{n:02d}.aiff", out / f"{n:02d}.wav"
            subprocess.run(["say", "-v", VOICE, "-r", str(RATE), "-o", str(aiff), spoken], check=True)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(aiff), "-ac", "1", "-ar", str(SR), str(wav)], check=True)
            data = read_wav(wav)
            dur = len(data) / SR
            clips.append((cur, data))
            captions.append([round(cur, 3), round(cur + dur, 3), cap, spoken])
            cur += dur + GAP
        end = max(start + designed, cur - GAP + TAIL)
        scenes[sid] = [round(start, 3), round(end, 3), designed]
        t = end
    total = round(t, 3)
    track = np.zeros(int(SR * (total + 1)))
    for at, data in clips:
        i = int(at * SR)
        track[i:i + len(data)] += data
    pcm = (np.clip(track, -1, 1) * 32767).astype("<i2")
    with wave.open(str(ROOT / "assets" / "voice.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())
    timing = {"duration": total, "scenes": scenes, "captions": captions}
    (ROOT / "timing.js").write_text("// generated by tools/voice.py\nwindow.TIMING = " + json.dumps(timing, indent=1) + ";\n",
                                    encoding="utf-8")
    (ROOT / "assets" / "timing.json").write_text(json.dumps(timing, indent=1), encoding="utf-8")
    for sid, (a, b, d) in scenes.items():
        print(f"{sid}: {a:6.2f} -> {b:6.2f} ({b - a:5.2f}s, designed {d})")
    print("total", total)


if __name__ == "__main__":
    main()
