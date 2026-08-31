# docs/

Two documents, each with an editable source and a distributable PDF. The PDFs
are committed, so reading them needs no clone, no PowerPoint and no Python —
GitHub renders them in the browser, and they attach to an email as they are.

## The documents

| read this | edit this |
|---|---|
| [`pdf/aisleflow-mathematical-guide.pdf`](pdf/aisleflow-mathematical-guide.pdf) — every algorithm, formula and symbol in the codebase, with a contents page and five worked examples | [`mathematical-guide.md`](mathematical-guide.md) |
| [`pdf/aisleflow-mapf-presentation.pdf`](pdf/aisleflow-mapf-presentation.pdf) — the project-review deck, 37 slides | [`deck/slides.html`](deck/slides.html) |
| [`pdf/aisleflow-mapf-presentation-notes.pdf`](pdf/aisleflow-mapf-presentation-notes.pdf) — the same deck, one slide per page with its speaker notes | the same file |

[`implementation-notes.md`](implementation-notes.md) maps the (external) spec's
numbered sections to the functions that implement them. It has no PDF: it is a
lookup table, and the Markdown renders fine on GitHub.

## Rebuilding

```
python3 tools/build_docs.py            # all three PDFs, into docs/pdf/
python3 tools/build_docs.py guide      # or one at a time: guide, deck, notes
```

Standard library only, plus a Chromium-family browser for the HTML-to-PDF step.
The build finds Chromium, Chrome or Edge automatically; point `$CHROME` at one
if it is somewhere unusual:

```
CHROME=/path/to/chrome python3 tools/build_docs.py all
```

Two limitations of the Chromium CLI print path are accepted rather than worked
around: it writes no PDF bookmarks, and CSS cannot number printed pages. The
guide therefore carries its own linked contents page — the links survive into
the PDF and stay clickable — and `--page-footer` re-enables Chromium's own
page-number footer for anyone who prefers it.

## The worked examples are generated

Every number printed inside a "Worked example" box in the mathematical guide is
computed by [`tools/worked_examples.py`](../tools/worked_examples.py), which
imports the same `scoring`, `congestion`, `aisle_manager`, `assignment` and
`priority` modules the simulator runs and reads a fixed, seeded scenario.
Nothing in those boxes is typed in by hand.

`tests/test_worked_examples.py` regenerates them and fails if the guide and the
code have drifted apart. When it does:

```
python3 tools/worked_examples.py --write     # then rebuild the PDF
```

## Editing the deck

`deck/slides.html` is one self-contained file — inline CSS, inline SVG, no
scripts, no external fonts. One `<section class="slide">` is one 16:9 page, and
each carries an `<aside class="notes">` that appears only in the notes build.
Open it in a browser to work on it; nothing needs to be running.

Three house rules keep it a deck rather than a document:

- one idea per slide — if a slide needs a second paragraph of argument, it needs
  a second slide, or the argument belongs in its speaker notes;
- every measured number stays exactly as measured, and says what map, how many
  seeds, and over how many timesteps;
- no external resources, so the file opens correctly offline, forever.
