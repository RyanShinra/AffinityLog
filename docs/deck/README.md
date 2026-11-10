# The project overview deck

Nineteen slides explaining AffinityLog end to end: what it ingests, the finding that drove the
schema, and how the API came to serve it. Built 2026-09-20, after the ScoreEntry chapter merged.

The deck is published as a private Artifact:
**<https://claude.ai/artifact/71smZjwSgUvCMujvT6anDc>** — only people it has been shared with can
open that link.

---

## Git is the source of record

**These files are the deck. The published copy is a rendering of them.**

When the published deck is edited on its page, read the changed slides back and apply that diff
*here*, then republish from here. The direction is always:

```
page edit  ->  this folder  ->  republish
```

Never the reverse. Republishing from a stale copy of this folder discards page edits; the publish
API refuses that, but the refusal is the last line of defense, not the plan.

### The page rewrites slides on its own, so read a diff carefully

Placing a comment on a slide **pins its elements**: flow children become `position:absolute` with
hard-coded `top` and `height`, and the page assigns them generated ids. That is the editor giving
the comment something to anchor to, not a design change anyone made.

When a slide comes back in that shape with no wording change, restore the flow layout but **keep the
generated ids** — an existing comment anchor points at them. A pinned heading has a fixed `top`, so
leaving it that way makes the heading hop by 20–60px as a reader pages between content slides, which
is the one thing the shared layout exists to prevent.

---

## Layout

| path | what |
|---|---|
| `project/deck.json` | the index: title, slide order, the four sections, and the two typefaces |
| `project/slides/<id>.html` | one file per slide; the file name is the slide id |

Slide order, which `deck.json` also states:

```
cover  no-api  terms  corpus  anatomy  humanization  finding  layers
discriminator  lookup  query  measured  stack  checks  witness  locks
snapshot  open  close
```

Four sections: **premise** (cover), **finding** (finding), **engineering** (stack), **close** (open).

---

## The rules a slide has to follow

Each file holds exactly one `<section>` on a fixed 1920×1080 canvas, and every style is inline from
a closed subset. No classes, no `<style>` block, no `var()`, no `em`, no `margin`, no `z-index`.
Lengths in px, colors in hex. Anything outside the subset is dropped silently on read rather than
raising, so a stray property simply does nothing.

- `padding:96px` is the slide margin, leaving 1728×888 for content. Nothing shrinks to fit: content
  that does not fit overflows, so tall slides need the arithmetic done rather than guessed.
- A `<div>` is invisible until it has a `background`, `border` or `box-shadow`.
- `position:absolute` pins a child to the slide; everything else flows.
- A footer sits at `bottom:64px` and its slide takes `padding:96px 96px 160px`, leaving 1728×824.
  **A pinned footer's own `left` and `width` do not follow the padding**, so they are set to
  `left:96px; width:1728px` by hand. Move the margin and those have to move with it.
- Nothing below 24px, anywhere, including table cells and footers.
- **`gap` takes ONE value.** The two-value row/column shorthand is outside the subset, so
  `gap:18px 40px` is dropped whole and the element lands with no gap on either axis. That is
  how the *terms* slide shipped with its two columns touching. To widen the gutter without
  widening the rows, put `padding:0 24px 0 0` on the cells and keep the single `gap`.

**How wide text actually runs.** The slide format's reference says to budget about 0.6 × font-size
per character. That figure is for sizing a box to its longest *word*, where the worst case is what
matters. Running prose averages nearer **0.45 × font-size**, measured off a rendered slide: the HER2
entry on *terms* sets 74 characters of 24px Archivo in a 796px column. Budget a paragraph at 0.45 and
a box that must not break a word at 0.6. Using 0.6 for prose costs about 40% of the line, which is
how the *terms* definitions got trimmed harder than they needed to be.

**Design.** Archivo with JetBrains Mono for keys, SQL and GraphQL. Slate `#12212E`, off-white
`#F7F6F3`, warm accent `#B24A22`, teal `#1F6459`. Type scale 140 / 64 / 34 / 30 / 26 / 24. Content
slides share one layout — eyebrow and heading in a header block at the top margin — so the heading
lands at the same height on every one.

**Spelling is American**, matching the rest of the repo. See the commit that swept it.

---

## What is on the slides

Every figure comes from this repository, not from memory. The `CASE` on the *discriminator* slide is
the real one in [`sql/candidate_summary.sql`](../../sql/candidate_summary.sql); the interface-kind
strings are `InterfaceKind`'s actual values; the corpus counts are the loaded corpus. If a number
here and a number in the repo disagree, the repo is right and the slide is stale.

The *measured* slide reports 0 unresolved keys and 0 parse failures, and then says why those are not
the reassurance they look like — the catalog was derived from this corpus, so both zeroes are
artifacts of how it was built. Keep that caveat if the slide is ever rewritten; without it the slide
overclaims.

---

## Open editorial items

Not defects — judgment calls the deck has not made yet. Raised during the 2026-11 review pass.

### The title pass — agreed, not yet applied (2026-11-10)

Every content slide's title was reviewed together as a table of contents, which is the only way the
pattern shows. The old set announced what was on the slide ("What the words mean", "What one run
gives you", "The stack, briefly"); six opened "What ..." or "Where ...". Below is the agreed
replacement set, merged from two passes — Claude's, which reached for claims and rhythm, and the
owner's, which reached for flat documentation labels. **Recorded, not landed.** Three slides are
held deliberately; see the vocabulary gap below.

| # | slide | current | agreed |
|---|---|---|---|
| 1 | cover | AffinityLog | unchanged |
| 2 | no-api | The source has no API | Bio Discovery gives you a CSV and nothing else |
| 3 | terms | What the words mean | Key terminology |
| 4 | models | The models in the recipes | Each module outputs its own columns |
| 5 | corpus | What I actually ran | Eleven runs, nine that produced data |
| 6 | anatomy | What one run gives you | Sample output from run 8 |
| 7 | humanization | What humanization changed | Humanization traded binding for safety |
| 8 | finding | One column, multiple meanings | ipTM means three different things |
| 9 | layers | Three layers | Three layers to pin down one meaning |
| 10 | discriminator | Telling the three apart | Chain composition decides the meaning |
| 11 | lookup | When the header isn't enough | Resolving ambiguous header names |
| 12 | predicted-property | The same disease, a different cure | One key, five properties, decoded elsewhere |
| 13 | query | The query this was built for | One key, three display names |
| 14 | measured | Measured against the corpus | Of course they're zero — I made them that way |
| 15 | stack | The stack, briefly | Two heads on one app, on purpose |
| 16 | checks | Checks that covered less than they claimed | HELD — whole slide needs rework |
| 17 | witness | I planned stage four on the belief ... | HELD — "stage four" arrives cold |
| 18 | locks | One lock per invariant | HELD — "invariant" arrives cold |
| 19 | snapshot | A code-first schema needs a snapshot | The snapshot that catches contract drift |
| 20 | open | What is open, and why it's written down | Every number here is predicted |
| 21 | close | Where it ended up | Flexibility breeds complexity |

Knock-on edit when this lands: *stack*'s closing paragraph currently opens "Two heads on one app, on
purpose", which would restate its own new title six inches below itself. Trim it to start at "REST
takes the CSV upload, because ...".

**The register rule this produced.** The two passes disagreed about voice, and the disagreement is
the useful part. Flat documentation labels suit the inventory slides (3, 6, 11, 15); a voiced,
first-person title suits the slides that carry a finding (14, 21). What does not work is
alternating without a rule. Decide per slide which kind it is, then write the title to match.

**Do not lead with the limit.** Claude's pass turned four empty titles into claims and, in doing so,
doubled the number of titles built on an absence (four to eight), because in this deck the
take-aways mostly *are* limits. The owner's pass fixed it instinctively on slides 2 and 5 by
leading with purpose and letting the limit land in the body. That is the general repair, and it
applies to the closing-caveat item below as well — the two are one habit, not two.

**Two corrections made during the pass, worth not reintroducing.** "Predicted binding probability"
was proposed as a plain-English name for ipTM on slide 8; it is the exact overclaim removed from
`README.md`, `CLAUDE.md` and three code comments on 2026-11-05 — ipTM is confidence that chains were
*placed* correctly, not a probability of binding. And "Building schema snapshots after every build"
describes the opposite of what `scripts/dump_schema.py` plus `tests/test_schema_snapshot.py` do:
the snapshot is regenerated deliberately when the contract changes, and CI fails when it drifted.

- **The vocabulary gap on 16, 17 and 18 (2026-11-10).** `stage` appears on slide 17 and nowhere
  else in the deck; `invariant` appears on slide 18 and nowhere else. Both arrive with no
  introduction, which is the same failure that made *lookup* unreadable before it was reframed —
  a reader meets "stage four" and asks what happened to one through three. Slide 16's wording needs
  work beyond its title. These three slides are held out of the title pass because the title
  problem is downstream of the body problem.
- **"Flexibility breeds complexity" may be the thesis, not the conclusion.** It is currently the
  last line of the last slide, where it reads as a lesson learned. The JSONB bag is flexible — any
  recipe, no migration — and the catalog exists because that flexibility removed any way to know
  what a column means. Slides 8 through 14 are that sentence being paid for. Worth deciding whether
  it belongs on the cover as a thesis the deck then proves. Open.
- **Every content slide ends on a qualifying sentence.** Each caveat is individually correct; it is
  the metronome of nine slides sharing one rhythm (4, 7, 8, 11, 12, 14, 16, 17, 20; plus 2 and 10
  ending on a "so therefore" conclusion, a cousin of the same shape) that reads as generated.
  Hardest of these to fix, because the fix is structural rather than lexical. Related to the
  "do not lead with the limit" note above: both are the same habit in different places.
- **Resolved by the table above:** *measured* ("Measured against the corpus") read as a
  continuation of the slide before it. The agreed replacement makes it a claim of its own.
- **Closed 2026-11-06:** the fourth card on *stack* was labelled "Gates" beside API, Data and Tests
  — three flat nouns and then a metaphor, which is variety in the one place a label row should not
  vary. It is "CI" now. Noted as unfinished: "CI" slightly under-describes ruff and black, which
  also run in the pre-commit hook, and no single short word covers linting and CI together
  ("quality gates" is the term of art, which is where "Gates" came from). "Checks" was rejected
  because the next slide but one is titled "Checks that covered less than they claimed".

## Regenerating the structure renders

The finding slide shows three real predicted structures rather than a drawing. `renders/` holds
the PNGs, the page that produced them, and the little server that saves them.

No database and no Docker are involved. The page loads a PDB straight off disk with the viewer
already vendored at `app/static/vendor/3Dmol-min.js`, and uses the same scheme `/demo` uses,
read from `app/templates/demo.html`: heavy `#58a6ff`, light `#3fb950`, target `#8b949e`,
predicted epitope `#f85149`. Running `/demo` itself would not work here anyway, because
`experiment_results/` is not bind-mounted in compose, so `/demo/pdb/{id}` 404s in Docker.

```bash
cp app/static/vendor/3Dmol-min.js docs/deck/renders/
cp experiment_results/*/9d9d1d35be0340a6b9b4d79bccf3db7a_boltz2.pdb docs/deck/renders/single.pdb
cp experiment_results/*/a6d1540b89d047f9a65f73cff490a2f8_boltz2.pdb docs/deck/renders/pair.pdb
cp experiment_results/*/8981aae116524c7683f6e3e310f32e43_boltz2.pdb docs/deck/renders/complex.pdb
python docs/deck/renders/serve.py     # serves render.html as index on 127.0.0.1:8138
```

Then open each of these and call `save('<file>.png')` from the console once `window.ready` is true:

| slide panel | url | candidate |
|---|---|---|
| single chain | `?s=single&zoom=0.52` | `9d9d1d35…`, chains H |
| heavy-light pair | `?s=pair&roty=62&zoom=0.60` | `a6d1540b…`, chains H/L |
| antibody and target | `?s=complex&surface=1&roty=30&zoom=0.92` | `8981aae1…`, chains H/L/T, run 8 |

Two things about those numbers. The zoom factors are deliberately **not** "fit each one to the
frame": they hold the heavy chain at the same apparent size across all three, so the panels
compare honestly instead of making a lone chain look as big as a whole complex. And the target
gets a molecular surface rather than a cartoon, because its cartoon is mostly coil and reads as
thin wire at slide size; the epitope residues are painted onto that surface.

The epitope list in `render.html` comes from `structure_analysis_boltz2.epitope_residues` in the
run 8 export, not from the database.

The PNGs here are the source of record. The deck references uploaded copies of them, so
replacing a render means re-uploading it and updating that slide's image reference.
