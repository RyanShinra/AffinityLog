# The project overview deck

Sixteen slides explaining AffinityLog end to end: what it ingests, the finding that drove the
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
cover  no-api  corpus  finding  layers  discriminator  lookup  query
measured  stack  checks  witness  locks  snapshot  open  close
```

Four sections: **premise** (cover), **finding** (finding), **engineering** (stack), **close** (open).

---

## The rules a slide has to follow

Each file holds exactly one `<section>` on a fixed 1920×1080 canvas, and every style is inline from
a closed subset. No classes, no `<style>` block, no `var()`, no `em`, no `margin`, no `z-index`.
Lengths in px, colors in hex. Anything outside the subset is dropped silently on read rather than
raising, so a stray property simply does nothing.

- `padding:128px` is the slide margin, leaving 1664×824 for content. Nothing shrinks to fit: content
  that does not fit overflows, so tall slides need the arithmetic done rather than guessed.
- A `<div>` is invisible until it has a `background`, `border` or `box-shadow`.
- `position:absolute` pins a child to the slide; everything else flows.
- A footer sits at `bottom:64px` and its slide takes `padding:128px 128px 160px`.
- Nothing below 24px, anywhere, including table cells and footers.

**Design.** IBM Plex Sans with JetBrains Mono for keys, SQL and GraphQL. Slate `#12212E`, off-white
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
