#!/usr/bin/env python3
"""Build the distributable PDFs in ``docs/pdf/`` from their sources.

Three targets:

    guide   docs/mathematical-guide.md  -> aisleflow-mathematical-guide.pdf
    deck    docs/deck/slides.html       -> aisleflow-mapf-presentation.pdf
    notes   docs/deck/slides.html       -> aisleflow-mapf-presentation-notes.pdf

Usage::

    python3 tools/build_docs.py            # same as "all"
    python3 tools/build_docs.py guide deck
    python3 tools/build_docs.py all --page-footer

Dependencies: the Python standard library, plus a Chromium-family browser for
the HTML-to-PDF step. The browser is found via ``$CHROME`` or a short search
list; nothing is installed and nothing is downloaded. This keeps the docs build
in line with the package itself, which has no runtime dependencies at all.

Two limitations of the Chromium CLI print path, both accepted deliberately:
it emits no PDF outline/bookmarks, and CSS cannot number printed pages. The
guide therefore carries its own linked table of contents (anchor links do
survive into the PDF and stay clickable), and ``--page-footer`` is available
for anyone who wants Chromium's own page-number footer instead.

The Markdown renderer below is deliberately not a general implementation. It
covers exactly the constructs ``docs/mathematical-guide.md`` uses and raises
on anything it does not recognise, so a silently mangled page is not a
possible outcome.
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
OUT_DIR = DOCS / "pdf"

TOC_MARKER = "<!-- TOC -->"


# --------------------------------------------------------------------------
# Markdown -> HTML
# --------------------------------------------------------------------------


class MarkdownError(RuntimeError):
    """Raised when the input uses a construct this renderer does not cover."""


@dataclass
class Heading:
    level: int
    text: str
    slug: str


_ITEM_RE = re.compile(r"^(\s*)([-*+]|\d+\.)\s+(.*)$")
_HEADING_RE = re.compile(r"^(#{1,4})\s+(.*?)\s*#*$")
_FENCE_RE = re.compile(r"^(\s*)(`{3,})\s*([\w-]*)\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_HR_RE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)

# Blockquotes opening with one of these get a dedicated class, because they
# are the two editorial devices the guide leans on.
_CALLOUTS = {
    "in one sentence": "lede",
    "worked example": "worked",
}


def slugify(text: str) -> str:
    """GitHub-compatible heading anchor, so in-repo links and the PDF agree."""
    plain = re.sub(r"`([^`]*)`", r"\1", text)
    plain = re.sub(r"\*{1,2}([^*]*)\*{1,2}", r"\1", plain)
    plain = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", plain)
    slug = plain.strip().lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    return re.sub(r"[\s_]+", "-", slug).strip("-")


def render_inline(text: str) -> str:
    """Inline spans. Code spans are extracted first so nothing rewrites them."""
    spans: list[str] = []

    def stash(match: re.Match[str]) -> str:
        spans.append(html.escape(match.group(1)))
        return f"\x00{len(spans) - 1}\x00"

    text = re.sub(r"`([^`]+)`", stash, text)
    text = html.escape(text)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<em>\1</em>", text)
    text = re.sub(r"\x00(\d+)\x00", lambda m: f"<code>{spans[int(m.group(1))]}</code>", text)
    return text


# Code blocks never wrap: a wrapped ASCII diagram or a wrapped formula is
# worse than a small one. Anything too wide for the text column gets an
# inline font-size that makes it fit instead.
_TEXT_COLUMN_PT = 500.0   # A4 minus margins, minus room for a quoted block
_CODE_CAP_PT = 8.1
_CODE_FLOOR_PT = 5.4
_CHAR_ADVANCE = 0.60      # monospace advance as a fraction of font size


def _fit_style(lines: list[str]) -> str:
    widest = max((len(line) for line in lines), default=0)
    if widest == 0:
        return ""
    fits = _TEXT_COLUMN_PT / (_CHAR_ADVANCE * widest)
    if fits >= _CODE_CAP_PT:
        return ""
    return f' style="font-size:{max(fits, _CODE_FLOOR_PT):.2f}pt"'


def _dedent(lines: list[str], width: int) -> list[str]:
    out = []
    for line in lines:
        if not line.strip():
            out.append("")
        elif len(line) - len(line.lstrip(" ")) >= width:
            out.append(line[width:])
        else:
            out.append(line.lstrip(" "))
    return out


def render_blocks(lines: list[str], headings: list[Heading] | None = None) -> str:
    """Render a list of source lines to HTML. Recurses for list item bodies."""
    out: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        if not line.strip():
            i += 1
            continue

        if line.strip() == TOC_MARKER:
            out.append(TOC_MARKER)
            i += 1
            continue

        fence = _FENCE_RE.match(line)
        if fence:
            indent, ticks, lang = fence.group(1), fence.group(2), fence.group(3)
            body: list[str] = []
            i += 1
            while i < n and not re.match(rf"^\s*{ticks}\s*$", lines[i]):
                body.append(lines[i][len(indent):] if lines[i].startswith(indent) else lines[i])
                i += 1
            if i >= n:
                raise MarkdownError(f"unterminated code fence: {line!r}")
            i += 1
            cls = f' class="lang-{lang}"' if lang else ""
            out.append(
                f"<pre{cls}{_fit_style(body)}>"
                f"<code>{html.escape(chr(10).join(body))}</code></pre>"
            )
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            text = heading.group(2)
            slug = slugify(text)
            if headings is not None:
                headings.append(Heading(level, text, slug))
            out.append(f'<h{level} id="{slug}">{render_inline(text)}</h{level}>')
            i += 1
            continue

        if _HR_RE.match(line):
            out.append("<hr>")
            i += 1
            continue

        if line.lstrip().startswith(">"):
            quote: list[str] = []
            while i < n and (lines[i].lstrip().startswith(">") or
                             (quote and lines[i].strip() and not _ITEM_RE.match(lines[i]))):
                stripped = lines[i].lstrip()
                quote.append(stripped[1:].lstrip(" ") if stripped.startswith(">") else stripped)
                i += 1
            inner = render_blocks(quote)
            label = re.sub(r"<[^>]+>", "", inner).strip().lower()
            cls = ""
            for prefix, name in _CALLOUTS.items():
                if label.startswith(prefix):
                    cls = f' class="{name}"'
                    break
            out.append(f"<blockquote{cls}>{inner}</blockquote>")
            continue

        if "|" in line and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]):
            def cells(row: str) -> list[str]:
                return [c.strip() for c in row.strip().strip("|").split("|")]

            header = cells(line)
            i += 2
            rows = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append(cells(lines[i]))
                i += 1
            head = "".join(f"<th>{render_inline(c)}</th>" for c in header)
            body = "".join(
                "<tr>" + "".join(f"<td>{render_inline(c)}</td>" for c in row) + "</tr>"
                for row in rows
            )
            out.append(f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")
            continue

        item = _ITEM_RE.match(line)
        if item:
            base_indent = len(item.group(1))
            ordered = item.group(2)[0].isdigit()
            items: list[list[str]] = []
            while i < n:
                current = _ITEM_RE.match(lines[i])
                if not current or len(current.group(1)) != base_indent:
                    if lines[i].strip() and len(lines[i]) - len(lines[i].lstrip(" ")) <= base_indent:
                        break
                    if not lines[i].strip():
                        # A blank line ends the list unless the next line
                        # continues the current item's body.
                        nxt = i + 1
                        while nxt < n and not lines[nxt].strip():
                            nxt += 1
                        if nxt >= n:
                            break
                        cont = len(lines[nxt]) - len(lines[nxt].lstrip(" "))
                        if cont <= base_indent and not _ITEM_RE.match(lines[nxt]):
                            break
                        if cont <= base_indent and _ITEM_RE.match(lines[nxt]) and \
                                len(_ITEM_RE.match(lines[nxt]).group(1)) != base_indent:
                            break
                        items[-1].append("")
                        i += 1
                        continue
                    items[-1].append(lines[i])
                    i += 1
                    continue
                marker_width = len(current.group(1)) + len(current.group(2)) + 1
                items.append([" " * marker_width + current.group(3)])
                i += 1
            rendered = []
            for raw in items:
                width = len(raw[0]) - len(raw[0].lstrip(" "))
                inner = render_blocks(_dedent(raw, width))
                if inner.startswith("<p>") and inner.endswith("</p>") and "<p>" not in inner[3:]:
                    inner = inner[3:-4]
                rendered.append(f"<li>{inner}</li>")
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>{''.join(rendered)}</{tag}>")
            continue

        if line.lstrip().startswith("<"):
            raise MarkdownError(f"raw HTML block is not supported: {line!r}")

        para: list[str] = []
        while i < n and lines[i].strip() and not _HEADING_RE.match(lines[i]) \
                and not _FENCE_RE.match(lines[i]) and not _ITEM_RE.match(lines[i]) \
                and not _HR_RE.match(lines[i]) and not lines[i].lstrip().startswith(">"):
            para.append(lines[i].strip())
            i += 1
        out.append(f"<p>{render_inline(' '.join(para))}</p>")

    return "\n".join(out)


def render_markdown(text: str) -> tuple[str, list[Heading]]:
    headings: list[Heading] = []
    text = _COMMENT_RE.sub(lambda m: m.group(0) if m.group(0).strip() == TOC_MARKER else "", text)
    body = render_blocks(text.replace("\t", "    ").split("\n"), headings)
    return body, headings


def build_toc(headings: list[Heading]) -> str:
    """A two-level contents list, skipping the document title."""
    rows = []
    for h in headings:
        if h.level == 1 or h.level > 3:
            continue
        cls = "toc-1" if h.level == 2 else "toc-2"
        rows.append(f'<li class="{cls}"><a href="#{h.slug}">{render_inline(h.text)}</a></li>')
    return '<nav class="toc"><h2>Contents</h2><ul>' + "".join(rows) + "</ul></nav>"


# --------------------------------------------------------------------------
# Page styling
# --------------------------------------------------------------------------

GUIDE_CSS = """
@page { size: A4; margin: 18mm 16mm 18mm 16mm; }
:root {
  --ink: #16191d; --muted: #5b6570; --rule: #d8dde3;
  --accent: #1f5fa8; --lede-bg: #eef3f9; --worked-bg: #f6f2e8;
  --worked-rule: #c9a227; --code-bg: #f4f6f8;
}
* { box-sizing: border-box; }
body {
  margin: 0; color: var(--ink);
  font: 10.2pt/1.55 "Charter", "Georgia", "Times New Roman", serif;
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
}
h1, h2, h3, h4 {
  font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
  line-height: 1.25; margin: 0 0 .5em;
}
h1 { font-size: 24pt; letter-spacing: -.01em; }
h2 {
  font-size: 15pt; margin-top: 1.9em; padding-top: .45em;
  border-top: 2px solid var(--ink); break-after: avoid;
}
h3 { font-size: 11.5pt; margin-top: 1.5em; color: #26313c; break-after: avoid; }
h4 { font-size: 10.2pt; margin-top: 1.2em; text-transform: uppercase;
     letter-spacing: .06em; color: var(--muted); break-after: avoid; }
p { margin: 0 0 .75em; orphans: 2; widows: 2; }
a { color: var(--accent); text-decoration: none; }
code {
  font-family: "SFMono-Regular", "DejaVu Sans Mono", Menlo, Consolas, monospace;
  font-size: .88em; background: var(--code-bg); padding: .08em .3em;
  border-radius: 3px; word-break: break-word;
}
/* Never wrap: a broken ASCII diagram or a folded formula is worse than a
   small one, and _fit_style() has already sized anything over-wide. */
pre {
  background: var(--code-bg); border: 1px solid var(--rule); border-radius: 4px;
  padding: .7em .9em; margin: 0 0 1em; overflow: hidden;
  break-inside: avoid; white-space: pre;
  font-size: 8.1pt; line-height: 1.4;
}
pre code { background: none; padding: 0; font-size: inherit; }
blockquote { margin: 0 0 1em; padding: .6em .9em; border-radius: 4px;
             break-inside: avoid; }
blockquote p:last-child { margin-bottom: 0; }
blockquote.lede {
  background: var(--lede-bg); border-left: 3px solid var(--accent);
  font-size: 10.4pt; color: #1d2a38;
}
blockquote.worked {
  background: var(--worked-bg); border-left: 3px solid var(--worked-rule);
  font-size: 9.4pt;
}
blockquote.worked pre { background: #fffdf7; border-color: #e4d9b4;
                        font-size: 7.6pt; }
ul, ol { margin: 0 0 .85em; padding-left: 1.35em; }
li { margin-bottom: .3em; }
table {
  border-collapse: collapse; width: 100%; margin: 0 0 1.1em;
  font-size: 8.6pt; break-inside: avoid;
}
th, td { border: 1px solid var(--rule); padding: .34em .5em; text-align: left;
         vertical-align: top; }
th { background: #eef1f4; font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
     font-size: 8.2pt; text-transform: uppercase; letter-spacing: .04em; }
hr { border: 0; border-top: 1px solid var(--rule); margin: 1.6em 0; }
.title-block { margin-bottom: 2em; }
.title-block .sub { color: var(--muted); font-size: 11pt; margin-top: -.4em; }
.title-block .meta { color: var(--muted); font-size: 8.5pt; margin-top: 1em;
                     font-family: "Helvetica Neue", Helvetica, Arial, sans-serif; }
nav.toc { break-after: page; }
nav.toc h2 { border: 0; margin-top: 0; padding-top: 0; }
nav.toc ul { list-style: none; padding: 0; column-count: 2; column-gap: 2em;
             font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
             font-size: 8.8pt; }
nav.toc li { break-inside: avoid; margin-bottom: .18em; }
nav.toc li.toc-1 { font-weight: 600; margin-top: .7em; }
nav.toc li.toc-2 { padding-left: 1.1em; color: var(--muted); }
nav.toc li.toc-2 a { color: var(--muted); }
"""

NOTES_CSS = """
@page { size: A4 portrait; margin: 14mm 14mm 16mm; }
html, body { background: #fff !important; }
body { display: block !important; }
.slide {
  width: 1280px !important; height: 720px !important;
  transform: scale(0.5); transform-origin: top left;
  margin: 0 !important; box-shadow: none !important;
  border: 1px solid #d8dde3;
}
.slide-wrap { width: 640px; height: 362px; overflow: hidden; margin: 0 0 10mm; }
.notes-page { break-after: page; }
.notes-page:last-child { break-after: auto; }
/* The deck's own rules style for a dark slide; override them explicitly
   rather than by inheritance, or the notes come out white-on-white. */
.notes-body { max-width: 640px; }
.notes-body h2 {
  font: 600 8.5pt/1 "Helvetica Neue", Helvetica, Arial, sans-serif;
  text-transform: uppercase; letter-spacing: .1em; color: #7a828b;
  margin: 0 0 .8em; border: 0; max-width: none;
}
.notes-body p {
  font: 10.5pt/1.55 "Charter", Georgia, "Times New Roman", serif;
  color: #16191d; margin: 0 0 .7em;
}
.notes-body code, .notes-body .mono {
  font-family: "SFMono-Regular", "DejaVu Sans Mono", Menlo, monospace;
  font-size: 9.4pt; background: #f1f3f5; padding: .05em .3em; border-radius: 3px;
}
.notes-body em { color: #16191d; }
.notes-slug {
  font: 600 8.5pt/1 "Helvetica Neue", Helvetica, Arial, sans-serif;
  letter-spacing: .1em; text-transform: uppercase; color: #7a828b;
  margin: 0 0 4mm;
}
aside.notes { display: block; }
"""

GUIDE_SHELL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{title}</title>
<style>{css}</style>
</head><body>
{title_block}
{body}
</body></html>
"""


# --------------------------------------------------------------------------
# Chromium driver
# --------------------------------------------------------------------------

CHROME_CANDIDATES = (
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)


def find_chrome() -> str:
    import os

    explicit = os.environ.get("CHROME")
    if explicit:
        if Path(explicit).is_file() or shutil.which(explicit):
            return explicit
        sys.exit(f"$CHROME is set to {explicit!r}, which is not an executable.")
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
        found = shutil.which(candidate)
        if found:
            return found
    sys.exit(
        "No Chromium-family browser found. Install Chromium or Google Chrome, "
        "or point $CHROME at one:\n"
        "    CHROME=/path/to/chrome python3 tools/build_docs.py all"
    )


def print_pdf(html_path: Path, pdf_path: Path, *, page_footer: bool = False) -> None:
    chrome = find_chrome()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        chrome,
        "--headless",
        "--disable-gpu",
        "--no-sandbox",
        "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=8000",
        f"--print-to-pdf={pdf_path}",
    ]
    if not page_footer:
        argv.append("--no-pdf-header-footer")
    argv.append(html_path.resolve().as_uri())
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode != 0 or not pdf_path.exists():
        sys.exit(f"chrome failed to print {html_path.name}:\n{result.stderr.strip()}")


# --------------------------------------------------------------------------
# Targets
# --------------------------------------------------------------------------


def guide_html() -> str:
    source = (DOCS / "mathematical-guide.md").read_text(encoding="utf-8")
    body, headings = render_markdown(source)
    if TOC_MARKER in body:
        body = body.replace(TOC_MARKER, build_toc(headings))
    title = headings[0].text if headings else "Mathematical guide"
    # The rendered <h1> becomes the title block, so drop it from the flow.
    body = re.sub(r"^<h1[^>]*>.*?</h1>\s*", "", body, count=1, flags=re.S)
    title_block = (
        '<div class="title-block">'
        f"<h1>{render_inline(title)}</h1>"
        '<p class="sub">LDA-PIBT &mdash; Lifelong Aisle-Managed Priority '
        "Inheritance with Backtracking</p>"
        '<p class="meta">AisleFlow &middot; generated from '
        "docs/mathematical-guide.md by tools/build_docs.py</p>"
        "</div>"
    )
    return GUIDE_SHELL.format(
        title=html.escape(title), css=GUIDE_CSS, title_block=title_block, body=body
    )


def build_guide(page_footer: bool) -> Path:
    out = OUT_DIR / "aisleflow-mathematical-guide.pdf"
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "guide.html"
        page.write_text(guide_html(), encoding="utf-8")
        print_pdf(page, out, page_footer=page_footer)
    return out


def build_deck(page_footer: bool) -> Path:
    out = OUT_DIR / "aisleflow-mapf-presentation.pdf"
    print_pdf(DOCS / "deck" / "slides.html", out, page_footer=page_footer)
    return out


_SLIDE_RE = re.compile(r'<section class="slide(.*?)</section>', re.S)
_NOTES_RE = re.compile(r'<aside class="notes">(.*?)</aside>', re.S)
_SLUG_RE = re.compile(r'data-slug="([^"]*)"')


def notes_html() -> str:
    """Re-lay the deck as one A4 page per slide: the slide, then its notes."""
    source = (DOCS / "deck" / "slides.html").read_text(encoding="utf-8")
    head, _, rest = source.partition("<body>")
    slides = _SLIDE_RE.findall(rest)
    if not slides:
        sys.exit("no slides found in docs/deck/slides.html")

    pages = []
    for index, raw in enumerate(slides, start=1):
        section = f'<section class="slide{raw}</section>'
        notes_match = _NOTES_RE.search(section)
        notes = notes_match.group(1).strip() if notes_match else "<p><em>No notes.</em></p>"
        slide_only = _NOTES_RE.sub("", section)
        slug_match = _SLUG_RE.search(section)
        slug = slug_match.group(1) if slug_match else f"slide {index}"
        pages.append(
            '<div class="notes-page">'
            f'<p class="notes-slug">{index} &middot; {html.escape(slug)}</p>'
            f'<div class="slide-wrap">{slide_only}</div>'
            f'<div class="notes-body"><h2>Speaker notes</h2>{notes}</div>'
            "</div>"
        )
    return head + f"<style>{NOTES_CSS}</style></head><body>" + "".join(pages) + "</body></html>"


def build_notes(page_footer: bool) -> Path:
    out = OUT_DIR / "aisleflow-mapf-presentation-notes.pdf"
    # Written beside slides.html rather than in a temp dir, so that any
    # relative reference the deck grows later still resolves.
    page = DOCS / "deck" / ".notes-build.html"
    page.write_text(notes_html(), encoding="utf-8")
    try:
        print_pdf(page, out, page_footer=page_footer)
    finally:
        page.unlink(missing_ok=True)
    return out


TARGETS = {"guide": build_guide, "deck": build_deck, "notes": build_notes}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "targets", nargs="*", default=["all"],
        help="guide, deck, notes, or all (default: all)",
    )
    parser.add_argument(
        "--page-footer", action="store_true",
        help="keep Chromium's own date/URL/page-number footer",
    )
    args = parser.parse_args(argv)

    names = list(TARGETS) if "all" in args.targets or not args.targets else args.targets
    unknown = [n for n in names if n not in TARGETS]
    if unknown:
        parser.error(f"unknown target(s): {', '.join(unknown)}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name in names:
        path = TARGETS[name](args.page_footer)
        size = path.stat().st_size / 1024
        print(f"{name:6s} -> {path.relative_to(ROOT)}  ({size:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
