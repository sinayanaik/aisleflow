#!/usr/bin/env python3
"""Build the distributable PDFs in ``docs/pdf/`` from their sources.

Three targets:

    paper   docs/latex/aisleflow.tex    -> aisleflow-paper.pdf
    deck    docs/deck/slides.html       -> aisleflow-mapf-presentation.pdf
    notes   docs/deck/slides.html       -> aisleflow-mapf-presentation-notes.pdf

Usage::

    python3 tools/build_docs.py            # same as "all"
    python3 tools/build_docs.py paper
    python3 tools/build_docs.py deck notes --page-footer

Dependencies: the Python standard library, plus a Chromium-family browser for
the two deck targets and a LaTeX installation for the paper. The browser is
found via ``$CHROME`` or a short search list; nothing is installed and nothing
is downloaded.

The Chromium CLI print path emits no PDF outline and cannot number printed
pages, so ``--page-footer`` re-enables Chromium's own footer for anyone who
wants page numbers on the deck. The paper is unaffected: LaTeX numbers its own
pages and writes its own outline.
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
OUT_DIR = DOCS / "pdf"


# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Page styling
# --------------------------------------------------------------------------

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


def build_paper(page_footer: bool) -> Path:
    """Compile docs/latex/aisleflow.tex and copy the PDF into docs/pdf/.

    LuaLaTeX rather than pdfLaTeX: the generated worked-example boxes are
    verbatim transcripts of program output and carry Greek letters and box
    drawing that only a Unicode engine with a real font can set. `page_footer`
    has no meaning here -- LaTeX numbers its own pages -- and is accepted only
    so that every target has the same signature.
    """
    latex = DOCS / "latex"
    latexmk = shutil.which("latexmk")
    if not latexmk:
        sys.exit(
            "latexmk was not found. The paper target needs a LaTeX install:\n"
            "    apt-get install texlive-latex-recommended texlive-latex-extra \\\n"
            "        texlive-fonts-recommended texlive-luatex texlive-science \\\n"
            "        latexmk fonts-dejavu\n"
            "The deck and notes targets do not need it."
        )
    result = subprocess.run(
        [latexmk, "-lualatex", "-interaction=nonstopmode", "-halt-on-error",
         "aisleflow.tex"],
        cwd=latex, capture_output=True, text=True,
    )
    built = latex / "aisleflow.pdf"
    if result.returncode != 0 or not built.exists():
        log = latex / "aisleflow.log"
        errors = ""
        if log.exists():
            errors = "\n".join(
                line for line in log.read_text(errors="replace").splitlines()
                if line.startswith("!")
            )
        sys.exit(f"latexmk failed:\n{errors or result.stdout[-2000:]}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "aisleflow-paper.pdf"
    shutil.copyfile(built, out)
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


TARGETS = {"paper": build_paper, "deck": build_deck, "notes": build_notes}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "targets", nargs="*", default=["all"],
        help="paper, deck, notes, or all (default: all)",
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
