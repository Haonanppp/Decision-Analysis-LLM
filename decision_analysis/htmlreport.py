"""Interactive HTML report: a designed shell around the markdown report.

The markdown renderers in report.py stay the single source of content; this
module converts our own constrained markdown subset (headings, tables,
lists, blockquotes, images, bold, code, rules) to HTML and wraps it in a
minimal, professional, fully self-contained page:

- system font stack, generous whitespace, single reading column;
- sticky table of contents with scrollspy on wide screens;
- audit appendices collapsed by default (expand on demand, and in print);
- chart SVGs inlined - the file works offline as one artifact;
- ink/hairline palette shared with the charts, so page and figures match.

No external assets, no framework; the only JavaScript is a ~15-line
scrollspy. Deterministic: content comes from the markdown verbatim.
"""

from __future__ import annotations

import html
import re
from pathlib import Path


# ---- markdown subset -> HTML ---------------------------------------------


def _inline(text: str) -> str:
    """Escape, then apply the two inline forms we emit: **bold**, `code`."""
    out = html.escape(text, quote=False)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _table_html(rows: list[str]) -> str:
    parsed = [
        [c.strip() for c in row.strip().strip("|").split("|")] for row in rows
    ]
    if len(parsed) < 2:
        return ""
    aligns = []
    for cell in parsed[1]:
        if cell.endswith(":") and cell.startswith(":"):
            aligns.append("center")
        elif cell.endswith(":"):
            aligns.append("right")
        else:
            aligns.append("left")
    def tr(cells: list[str], tag: str) -> str:
        tds = []
        for i, cell in enumerate(cells):
            align = aligns[i] if i < len(aligns) else "left"
            style = f' class="al-{align}"' if align != "left" else ""
            tds.append(f"<{tag}{style}>{_inline(cell)}</{tag}>")
        return "<tr>" + "".join(tds) + "</tr>"
    body = "".join(tr(r, "td") for r in parsed[2:])
    return (
        '<div class="table-wrap"><table><thead>'
        + tr(parsed[0], "th")
        + "</thead><tbody>"
        + body
        + "</tbody></table></div>"
    )


def markdown_to_html(md: str, inline_assets: dict[str, str] | None = None) -> str:
    """Convert the report-markdown subset to HTML body markup.

    inline_assets maps image src paths (as written in the markdown) to raw
    SVG markup to embed in place of an <img>.
    """
    assets = inline_assets or {}
    out: list[str] = []
    lines = md.split("\n")
    i = 0
    para: list[str] = []

    def flush_para() -> None:
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("|"):
            flush_para()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i])
                i += 1
            out.append(_table_html(rows))
            continue
        if stripped.startswith("- "):
            flush_para()
            out.append("<ul>")
            while i < len(lines) and lines[i].strip().startswith("- "):
                out.append("<li>" + _inline(lines[i].strip()[2:]) + "</li>")
                i += 1
            out.append("</ul>")
            continue
        if stripped.startswith("> "):
            flush_para()
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip().lstrip("> "))
                i += 1
            out.append("<blockquote><p>" + _inline(" ".join(quote)) + "</p></blockquote>")
            continue

        image = re.fullmatch(r"!\[(.*?)\]\((.*?)\)", stripped)
        if image:
            flush_para()
            alt, src = image.group(1), image.group(2)
            if src in assets:
                out.append(f'<figure aria-label="{html.escape(alt)}">{assets[src]}</figure>')
            else:
                out.append(
                    f'<figure><img src="{html.escape(src)}" alt="{html.escape(alt)}"></figure>'
                )
            i += 1
            continue
        if stripped == "---":
            flush_para()
            out.append("<hr>")
            i += 1
            continue
        heading = re.fullmatch(r"(#{1,4})\s+(.*)", stripped)
        if heading:
            flush_para()
            level = len(heading.group(1))
            text = heading.group(2)
            hid = f' id="{_slug(text)}"' if level == 2 else ""
            out.append(f"<h{level}{hid}>{_inline(text)}</h{level}>")
            i += 1
            continue
        if not stripped:
            flush_para()
            i += 1
            continue
        para.append(stripped)
        i += 1
    flush_para()
    return "\n".join(out)


# ---- sectioning: wrap h2 blocks, collapse appendices ----------------------


def _sectionize(body: str) -> tuple[str, list[tuple[str, str]]]:
    """Wrap each h2 block in <section>; appendix blocks in <details>.
    Returns (markup, toc) where toc is [(id, title), ...]."""
    parts = re.split(r"(<h2 id=\"[^\"]+\">.*?</h2>)", body)
    out: list[str] = [parts[0]]
    toc: list[tuple[str, str]] = []
    for j in range(1, len(parts), 2):
        h2 = parts[j]
        content = parts[j + 1] if j + 1 < len(parts) else ""
        hid = re.search(r'id="([^"]+)"', h2).group(1)
        title = re.sub(r"<[^>]+>", "", h2)
        toc.append((hid, title))
        if title.startswith("Appendix"):
            summary = h2.replace("<h2", "<h2 class=\"inline\"").strip()
            out.append(
                f'<section id="s-{hid}"><details><summary>{summary}</summary>'
                f"{content}</details></section>"
            )
        else:
            out.append(f'<section id="s-{hid}">{h2}{content}</section>')
    return "".join(out), toc


_CSS = """
:root {
  --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --hairline: #e1e0d9; --surface: #fcfcfb; --page: #f9f9f7;
  --accent: #2a78d6;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--page); color: var(--ink);
  font: 16px/1.65 system-ui, -apple-system, "Segoe UI", sans-serif;
  -webkit-font-smoothing: antialiased;
}
.layout { display: flex; max-width: 1140px; margin: 0 auto; gap: 48px; padding: 0 24px; }
nav.toc {
  position: sticky; top: 0; align-self: flex-start; flex: 0 0 220px;
  height: 100vh; overflow-y: auto; padding: 56px 0 32px;
  font-size: 13px;
}
nav.toc a {
  display: block; color: var(--muted); text-decoration: none;
  padding: 5px 12px; border-left: 2px solid var(--hairline);
  transition: color .15s;
}
nav.toc a:hover { color: var(--ink); }
nav.toc a.active { color: var(--ink); border-left-color: var(--ink); font-weight: 600; }
main { flex: 1; min-width: 0; max-width: 720px; padding: 56px 0 96px; }
h1 { font-size: 30px; line-height: 1.25; letter-spacing: -0.02em; margin: 0 0 8px; }
.meta { color: var(--muted); font-size: 13px; margin: 0 0 40px; }
h2 {
  font-size: 20px; letter-spacing: -0.01em; margin: 56px 0 16px;
  padding-top: 24px; border-top: 1px solid var(--hairline);
}
h2.inline { display: inline; border: 0; padding: 0; margin: 0; font-size: 20px; }
h3 { font-size: 15px; margin: 28px 0 8px; }
h4 { font-size: 14px; margin: 20px 0 6px; }
p, ul { margin: 0 0 14px; }
ul { padding-left: 22px; }
li { margin-bottom: 4px; }
strong { font-weight: 600; }
code {
  font: 13px/1 ui-monospace, "Cascadia Code", Consolas, monospace;
  background: #f0efec; padding: 2px 5px; border-radius: 4px;
}
blockquote {
  margin: 0 0 14px; padding: 10px 18px; border-left: 3px solid var(--hairline);
  color: var(--ink-2); font-style: italic;
}
hr { border: 0; border-top: 1px solid var(--hairline); margin: 28px 0; }
.table-wrap { overflow-x: auto; margin: 0 0 18px; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th {
  text-align: left; font-weight: 600; color: var(--ink-2);
  border-bottom: 1px solid var(--ink); padding: 7px 12px 7px 0;
  white-space: nowrap;
}
td { border-bottom: 1px solid var(--hairline); padding: 7px 12px 7px 0; vertical-align: top; }
tbody tr:hover td { background: #f4f3f0; }
.al-right { text-align: right; }
.al-center { text-align: center; }
figure { margin: 6px 0 22px; }
figure svg { max-width: 100%; height: auto; display: block; }
details > summary {
  cursor: pointer; list-style: none; margin: 56px 0 16px;
  padding-top: 24px; border-top: 1px solid var(--hairline);
}
details > summary::before {
  content: "+"; display: inline-block; width: 22px; color: var(--muted);
  font-weight: 600;
}
details[open] > summary::before { content: "\\2212"; }
details > summary::-webkit-details-marker { display: none; }
@media (max-width: 900px) { nav.toc { display: none; } }
@media print {
  nav.toc { display: none; }
  body { background: #fff; }
  details { open: true; }
  details > summary::before { display: none; }
  tbody tr:hover td { background: transparent; }
}
"""

_JS = """
document.querySelectorAll('details').forEach(d => {
  if (window.matchMedia('print').matches) d.open = true;
});
window.addEventListener('beforeprint', () => {
  document.querySelectorAll('details').forEach(d => d.open = true);
});
const links = Array.from(document.querySelectorAll('nav.toc a'));
const byId = new Map(links.map(a => [a.getAttribute('href').slice(1), a]));
const observer = new IntersectionObserver(entries => {
  entries.forEach(e => {
    if (e.isIntersecting) {
      links.forEach(a => a.classList.remove('active'));
      const link = byId.get(e.target.id.replace(/^s-/, ''));
      if (link) link.classList.add('active');
    }
  });
}, { rootMargin: '-10% 0px -75% 0px' });
document.querySelectorAll('main section[id]').forEach(s => observer.observe(s));
"""


def render_html_page(title: str, meta_line: str, md: str,
                     inline_assets: dict[str, str] | None = None) -> str:
    body = markdown_to_html(md, inline_assets)
    # The markdown starts with the h1 title; drop it - the shell renders it.
    body = re.sub(r"^<h1>.*?</h1>\n?", "", body)
    body, toc = _sectionize(body)
    toc_html = "".join(
        f'<a href="#{hid}">{html.escape(t)}</a>' for hid, t in toc
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="layout">
<nav class="toc" aria-label="Contents">{toc_html}</nav>
<main>
<h1>{html.escape(title)}</h1>
<p class="meta">{html.escape(meta_line)}</p>
{body}
</main>
</div>
<script>{_JS}</script>
</body>
</html>
"""


_TRANSCRIPT_CSS = """
body { margin: 0; background: #fff; color: #0b0b0b;
  font: 14px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 720px; margin: 0 auto; padding: 40px 24px 60px; }
h1 { font-size: 22px; margin: 0 0 4px; }
.meta { color: #898781; font-size: 12px; margin: 0 0 28px; }
.phase { margin: 26px 0 8px; font-size: 11px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.08em; color: #52514e;
  border-top: 1px solid #e1e0d9; padding-top: 12px; }
.out { margin: 2px 0; white-space: pre-wrap; }
.q { margin: 14px 0 6px; padding: 8px 12px; background: #fcfcfb;
  border-left: 3px solid #0b0b0b; font-weight: 600; }
.ans { margin: 8px 0 14px auto; max-width: 85%; width: fit-content;
  background: #0b0b0b; color: #fff; padding: 6px 12px;
  border-radius: 8px 8px 2px 8px; white-space: pre-wrap; }
@media print { body { font-size: 12px; } }
"""


def render_transcript(title: str, meta_line: str, events: list[dict]) -> str:
    """Print-ready HTML of the full session interaction (server-side record
    of every output, question, and user answer, in order)."""
    body: list[str] = []
    for ev in events:
        kind = ev.get("type")
        if kind == "phase":
            body.append(f'<div class="phase">{html.escape(ev.get("name", ""))}</div>')
        elif kind == "output":
            text = ev.get("text", "")
            if text.strip():
                body.append(f'<div class="out">{html.escape(text)}</div>')
        elif kind == "prompt":
            prompt = ev.get("prompt", "").rstrip(" >")
            if prompt.strip():
                body.append(f'<div class="q">{html.escape(prompt)}</div>')
        elif kind == "answer":
            value = ev.get("value", "")
            body.append(f'<div class="ans">{html.escape(value) or "(skip)"}</div>')
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Session transcript - {html.escape(title)}</title>
<style>{_TRANSCRIPT_CSS}</style></head>
<body><main>
<h1>Session transcript</h1>
<p class="meta">{html.escape(title)} · {html.escape(meta_line)}</p>
{"".join(body)}
</main></body></html>
"""


def save_transcript(case, events: list[dict], case_dir: Path) -> Path:
    """Write the print-ready session transcript into the case folder."""
    meta = f"{case.decision_type} · {case.completed_at or case.created_at or ''}"
    html_path = case_dir / "transcript.html"
    html_path.write_text(
        render_transcript(case.statement, meta, events), encoding="utf-8"
    )
    return html_path


def write_html_report(case, md_text: str, case_dir: Path) -> Path:
    """Convert the markdown report to the HTML artifact inside the case's
    own folder, inlining any chart SVGs from its charts/ subfolder."""
    charts_dir = case_dir / "charts"
    assets: dict[str, str] = {}
    if charts_dir.is_dir():
        for svg_path in charts_dir.glob("*.svg"):
            assets[f"charts/{svg_path.name}"] = svg_path.read_text(
                encoding="utf-8"
            )
    meta = (
        f"{case.decision_type} · stakes: {case.stakes} · "
        f"{(case.completed_at or case.created_at or '')[:10]} · "
        f"decision maker: {case.decision_maker}"
    )
    page = render_html_page(case.statement, meta, md_text, assets)
    out_path = case_dir / "report.html"
    out_path.write_text(page, encoding="utf-8")
    return out_path
