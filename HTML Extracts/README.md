# HTML Extracts — saved Bio Discovery console pages

Browser "Save page as → Webpage, Complete" captures of the experiment console, kept because the
console's **Input Parameters** and **candidate ↔ sub-experiment** mappings are the provenance the CSV
export omits (see `../docs/schema-stress-log.md`). Captured 2026-07-16/17 as a hedge against loss of
console access.

## What's here
- **`*.html`** — the saved pages, committed **uncompressed**. This is all the scrapers need (they parse
  the DOM text only). Naming: `<Experiment> overview/results.html`, and per-sub-experiment
  `<Experiment>_<n> ov/res.html` for the swept runs.
- **`render-assets.tar.gz`** — the `_files/` sidecars (CSS/JS/images the browser saved to *render* the
  pages), archived at ~16% of raw size. Only needed to view a page rendered in a browser; **not** needed
  for scraping. Restore with:  `tar xzf render-assets.tar.gz`  (from this folder). The raw `_files/`
  folders are gitignored.

## What reads these
- `../scripts/scrape_input_parameters.py` — Overview page → Input Parameters JSON; Results page →
  candidate ↔ sub-experiment links.
- `../scripts/rebuild_rosetta_stone.py` — composes the ESM2 sub-experiment pages into candidate → property
  and self-validates against `../experiment_results/file id mapping.xlsx` (10/10).
