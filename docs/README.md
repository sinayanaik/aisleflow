# docs/

Everything here is either a source you edit or an artefact generated from one.
The artefacts are committed on purpose: the PDFs, the figures, the animations
and the dashboard all open straight from GitHub or from a download, with no
clone, no LaTeX, no Python and no build step.

## Read these

| document | what it is |
|---|---|
| [`pdf/aisleflow-paper.pdf`](pdf/aisleflow-paper.pdf) | **The paper.** 48 pages: the problem stated formally, the four prior methods and how each fails here, what LDA-PIBT changes and why, the whole system in full detail, and what the measurements said. Source: [`latex/`](latex/). |
| [`dashboard.html`](dashboard.html) | **The results, interactively.** Pick a map, pick a metric, hover a bar: mean, interval, p-value, and a sentence saying what that combination means. Download it and open it; it needs nothing. |
| [`gifs/`](gifs/) | **Five side-by-side animations**, each a claim shown rather than asserted. Start with [the Token Passing gridlock](gifs/01-token-passing-gridlock.gif). |
| [`figures/`](figures/) | The eight result figures, as SVG (renders inline on GitHub) and PDF (what the paper embeds). |
| [`pdf/aisleflow-mapf-presentation.pdf`](pdf/aisleflow-mapf-presentation.pdf) | The project-review deck, 37 slides. Also [with speaker notes](pdf/aisleflow-mapf-presentation-notes.pdf). Source: [`deck/slides.html`](deck/slides.html). |
| [`implementation-notes.md`](implementation-notes.md) | The (external) spec's numbered sections mapped to the functions that implement them. A lookup table, so it stays Markdown. |

## Where the numbers come from

`docs/data/` is one committed dataset — five suites, five seeds, 400 timesteps,
four maps — written by `experiments/run_all.py`, with a provenance header on
each file recording the seeds, the scenarios, the git SHA and the date. The
paper's tables, every figure and the dashboard are all generated from it, so
they cannot disagree with each other.

```bash
python3 experiments/run_all.py --seeds 5      # ~35 min; rewrites docs/data/
python3 experiments/run_all.py --quick        # ~1 min smoke test, not a result
```

## Rebuilding

```bash
python3 tools/make_figures.py --dashboard   # docs/figures/*, docs/dashboard.html
python3 tools/make_gifs.py                  # docs/gifs/*
python3 tools/build_docs.py                 # docs/pdf/* (paper, deck, notes)
python3 tools/build_docs.py paper           # or one at a time
```

The figures and animations need `matplotlib` and `pillow`
(`pip install -e ".[viz]"`). The deck targets need any Chromium-family browser,
found automatically or via `$CHROME`. The paper needs LaTeX:

```bash
apt-get install texlive-latex-recommended texlive-latex-extra \
    texlive-fonts-recommended texlive-luatex texlive-science latexmk fonts-dejavu
```

It is built with **LuaLaTeX**, not pdfLaTeX: the worked-example boxes are
verbatim transcripts of program output and carry Greek letters and box-drawing
characters that only a Unicode engine with a real font can typeset.

## The worked examples are generated

Every number printed inside a "Worked example" box in the paper is computed by
[`tools/worked_examples.py`](../tools/worked_examples.py), which imports the
same `scoring`, `congestion`, `aisle_manager`, `assignment` and `priority`
modules the simulator runs and reads a fixed, seeded scenario. Nothing in those
boxes is typed by hand.

`tests/test_worked_examples.py` regenerates them and fails if the paper and the
code have drifted apart. When it does:

```bash
python3 tools/worked_examples.py --write     # then rebuild the PDF
```

Do not hand-edit between a `% worked-example: name` marker and its
`% /worked-example`.

## Editing the paper

`latex/aisleflow.tex` is the preamble and the `\input` list; `latex/sections/`
holds one file per section. Part I (`00`–`02`) is the argument, Part II
(`10`–`26`) is the system in full, Part III (`30`) is the results. Numbered
equations, `algorithm` environments and `\Cref` cross-references throughout — a
section can move without any reference going stale.

Three house rules:

- every measured number says what map, how many seeds and over how many
  timesteps, and comes from `docs/data/`;
- a claim that is not significant says so, in the sentence that makes it;
- the losing cases are reported at the same size as the winning ones.

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
