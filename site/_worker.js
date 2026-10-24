// Cloudflare Pages serves static assets without byte ranges; Safari and iOS will not play an MP4
// unless the server answers Range requests with 206. Everything else passes straight through.
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const range = request.headers.get("Range");
    if (!range || !url.pathname.startsWith("/media/") || !url.pathname.endsWith(".mp4")) {
      return env.ASSETS.fetch(request);
    }
    const asset = await env.ASSETS.fetch(new Request(url, { method: "GET" }));
    if (!asset.ok) return asset;
    const body = await asset.arrayBuffer();
    const size = body.byteLength;
    const m = /^bytes=(\d*)-(\d*)$/.exec(range.trim());
    let start = m && m[1] !== "" ? +m[1] : NaN;
    let end = m && m[2] !== "" ? +m[2] : size - 1;
    if (m && m[1] === "" && m[2] !== "") { start = Math.max(size - +m[2], 0); end = size - 1; }
    if (!m || Number.isNaN(start) || start >= size || end < start) {
      return new Response(null, { status: 416, headers: { "Content-Range": `bytes */${size}` } });
    }
    end = Math.min(end, size - 1);
    const headers = new Headers(asset.headers);
    headers.set("Content-Range", `bytes ${start}-${end}/${size}`);
    headers.set("Content-Length", String(end - start + 1));
    headers.set("Accept-Ranges", "bytes");
    headers.set("Content-Type", "video/mp4");
    headers.set("Cache-Control", "public, max-age=604800");
    return new Response(body.slice(start, end + 1), { status: 206, headers });
  },
};
