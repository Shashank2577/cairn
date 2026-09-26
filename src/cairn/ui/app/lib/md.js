// Markdown rendering for spec documents, wiki pages and reports: marked for parsing, DOMPurify so
// nothing a document contains can run script in the page.
import { marked } from "marked";
import DOMPurify from "dompurify";
import { h } from "preact";
import { useMemo } from "preact/hooks";

marked.setOptions({ gfm: true, breaks: false });
const EMOJI = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]️?\s?/gu;

// While one document renders, the page it belongs to can turn its relative links into page links.
let relLink = null;
DOMPurify.addHook("afterSanitizeAttributes", node => {
  if (node.tagName === "A") {
    const href = node.getAttribute("href") || "";
    const to = relLink && href && !/^([a-z][a-z0-9+.-]*:|#|\/)/i.test(href) ? relLink(href) : null;
    if (/^https?:/i.test(href)) { node.setAttribute("target", "_blank"); node.setAttribute("rel", "noopener noreferrer"); }
    else if (to) node.setAttribute("href", to);
    else if (!href.startsWith("#")) { node.removeAttribute("href"); node.setAttribute("title", href); }
  }
  if (node.tagName === "INPUT") node.setAttribute("disabled", "");
});

/** `links` maps a relative link (as written, e.g. "./plan.md#x") to a page link, or returns null to leave it inert. */
export function mdToHtml(src, links) {
  const html = marked.parse(String(src || "").replace(EMOJI, ""));
  relLink = links || null;
  let clean;
  try { clean = DOMPurify.sanitize(html, { USE_PROFILES: { html: true } }); } finally { relLink = null; }
  // Mark requirement and task ids so the spec reader can colour them.
  return clean.replace(/\b((?:FR|SC|NFR)-\d{3})\b(?![^<]*>)/g, '<span class="req-id">$1</span>')
    .replace(/(<li>(?:<input[^>]*>)?\s*)(T\d{3})\b/g, '$1<span class="task-id">$2</span>');
}

export function Markdown({ src, class: cls = "prose", links, linksKey }) {
  const html = useMemo(() => mdToHtml(src, links), [src, linksKey]);
  return h("div", { class: cls, dangerouslySetInnerHTML: { __html: html } });
}
