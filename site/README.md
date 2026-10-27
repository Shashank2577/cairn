# cairn.spreix.com

The public site: the landing page (`index.html`), the side-by-side field demo (`demo.html`) and the three
launch films (`media/`). Plain HTML, CSS and JS with no build step.

- `styles.css` holds the shared tokens. Colours match the product UI (`src/cairn/ui/styles/tokens.css`).
- `topo.js` draws the contour-map backgrounds.
- `_worker.js` answers byte-range requests for the MP4s, which Cloudflare Pages does not do on its own and
  Safari and iOS need before they will play a video.
- `_headers` sets cache lifetimes.

The films are copies of `docs/assets/launch/{gtm,explainer,proof}/cairn-*.mp4`. After re-rendering one,
copy it here again and redeploy.

## Preview and deploy

```sh
python3 -m http.server 8765 --directory site
wrangler pages deploy site --project-name cairn --branch main
```

The Pages project is `cairn` (`cairn-4jl.pages.dev`). `cairn.spreix.com` is a proxied CNAME to it.
