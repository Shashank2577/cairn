# Explainer video source

Plain code, no video framework: `index.html` draws every frame from `seek(t)`, `render.mjs` drives
the headless Chrome on the machine over the DevTools protocol and pipes the screenshots to ffmpeg,
`music.py` synthesises the music bed with numpy, and `make_captions.mjs` writes the SRT and VTT
from `captions.js` (the same lines the page burns in). `narrate.py` voices `narration.js` with
Kokoro (af_heart), retimes the captions around the measured clips into `timing.js` (the page
stretches each scene to its line through a time warp), and mixes the score (`music.py`), the
effects (`sfx.py`, placed on on-screen events through the same warp) and the voice to -16 LUFS.

```sh
python narrate.py work/                        # Kokoro voice, retiming, score, effects, mix; needs kokoro + numpy
node render.mjs --crf 23 --out ../cairn-explainer.mp4 --audio work/mix.wav
node render.mjs --stills 7,44,64 --dir stills  # check single frames
node make_captions.mjs ..
```

Open `index.html#t=44` in a browser to see one moment, or `index.html#play` to watch it run.

## Where the facts come from

- The pack, risk line and every cited line are from real `cairn_context` and `cairn_impact` answers
  about `src/cairn/engines/graph/extract.py` in this repository (task: "change how
  graph/extract.py records source locations", budget 1,200; budget used 1,192).
- 8,739 lines and 406,492 bytes are `wc` on that file; "about 101,600 tokens" uses the 4
  characters per token estimate in `docs/mcp.md`.
- Tool names and arguments: `docs/mcp.md`. Layers: `README.md`. Tiers: `docs/models-and-cost.md`.
- The code diff in the Edit scene is labelled as an illustrative sketch; the `cairn_remember` text
  is an example of what an agent would save after that edit.
- `ui/*.png` are screenshots of `src/cairn/ui` opened with `?mock=1` (sample data, labelled so).
- Fonts: Overpass and Overpass Mono from `src/cairn/ui/fonts` (SIL OFL, licences in `fonts/`).
