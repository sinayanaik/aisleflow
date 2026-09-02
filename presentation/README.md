# The corporate-review deck

Not to be confused with [`docs/deck/slides.html`](../docs/deck/slides.html),
which is the technical deck -- how the planner decides, worked through on a
real floor. This one is the commercial framing of the same study, and it is a
PowerPoint file rather than a web page.

`Aisleflow-Research-Review.pptx` at the repository root: 21 slides presenting
the study as a corporate research review — the measurement first, then what it
is commercially worth, what a pilot would have to establish, and what stands
between the simulator and a warehouse floor.

Two rules shape it, and they are the reason it is built rather than written:

**Every number comes out of `docs/data/`.** `extract_data.py` writes
`deck_data.json`, and `build_deck.js` reads nothing else. The tables the
documents already generate — the featured comparison, the ablation ladder, the
sensitivity study, the map structure — are lifted verbatim out of their
`<!-- generated:NAME -->` blocks, so the deck quotes exactly what the documents
quote, guarded by the same test that stops those blocks drifting from the data.
Every figure the deck states in prose is re-derived and checked in
`cross_check()`, so a regenerated dataset that moves a number breaks the build
instead of leaving a stale claim on a slide.

**The only images are assets the repository already had.**
`docs/figures/01-vs-baselines.svg`, `docs/figures/05-the-maps.svg` and
`docs/gifs/01-aisleflow-bottleneck.gif`. Nothing here draws a new chart: the
two measurements that have no committed figure — planner runtime per timestep,
and throughput against fleet size — are tables and stat callouts instead.

## Rebuilding

```bash
cd presentation && npm install && cd ..     # pptxgenjs
pip install matplotlib                      # only for render_assets.py

python3 presentation/render_assets.py       # figures -> assets/*.png at 300 dpi
python3 presentation/extract_data.py        # docs/data + docs/*.md -> deck_data.json
node     presentation/build_deck.js         # -> Aisleflow-Research-Review.pptx
```

`assets/*.png` are committed, so a rebuild that only changes wording needs
neither matplotlib nor step one. `render_assets.py` imports the `FIGURES`
registry from `tools/make_figures.py` and calls the same builders that wrote
the committed SVGs — the raster and the SVG are one figure from one dataset.

## What each slide traces to

| Slides | Source |
| --- | --- |
| 2, 7, 9 | `docs/data/baselines.json`, and the `generated:featured` block of `docs/05-results.md` |
| 6, 21 | `docs/figures/05-the-maps.svg`, `generated:maps` in `docs/06-the-maps.md` |
| 8 | `docs/gifs/01-aisleflow-bottleneck.gif` and its README |
| 10 | `mean_runtime_ms_per_step` in `docs/data/baselines.json` — recorded by every run, tabulated nowhere else |
| 11 | `docs/data/density.json` — this sweep has no figure in the documentation |
| 12 | `generated:ladder` in `docs/05-results.md` |
| 13 | the before/after table and `generated:sensitivity` in `docs/05-results.md` |
| 3–5, 14–20 | `docs/01-how-it-works.md`, the results page's caveats, and the reading of them argued in the deck |

Slides 14–20 are the commercial half. They carry no cost or currency figure,
because the study contains none: the levers are measured, and slide 16 says
what a pilot would have to establish before any of them can be priced.

## QA

```bash
python3 <pptx-skill>/scripts/office/validate.py Aisleflow-Research-Review.pptx
python3 <pptx-skill>/scripts/office/soffice.py --headless --convert-to pdf \
        Aisleflow-Research-Review.pptx      # then render the pages and look
```

Fix anything QA finds in `build_deck.js`, never in the packed XML.
