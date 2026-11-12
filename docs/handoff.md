# Handoff — where the work is, and what is next

**A living doc.** It says what is in flight and what is next, so the next session (Mac, PC or
cloud) doesn't have to rebuild that from chat scrollback. Last updated **2026-11-12**.

## How to keep this file honest

- **Update it at the end of every session**, in the same commit as the last change, or in its own
  commit if the session made no change.
- **Finished items get deleted, not ticked.** `git log` records what got done. A done list here would
  be one more copy that goes stale.
- **Decisions move to where they belong.** Once something is decided, record it in its home doc (the
  deck's go in `docs/deck/README.md`) and shrink the item here to a pointer, or delete it.
  This file carries state and next actions. It is not where the reasoning lives.
- **If this file and a home doc disagree, the home doc wins**, the same rule as README vs history.
- Each item says **where it is recorded**, **what is blocking it**, and **who moves next**.

---

## Now: the deck's editorial pass

Branch `portfolio-deck`. Deck: <https://claude.ai/artifact/71smZjwSgUvCMujvT6anDc>.
Git is the source of record; see `docs/deck/README.md` for the page-edit → folder → republish loop.
On 2026-11-12 the published copy and `docs/deck/project/` matched exactly.

| # | item | recorded at | state | next move |
|---|---|---|---|---|
| 3 | Conclusion slide (*close*) title | deck README, thesis item: "the complexity was worth it", wording open | Draft: **"The complexity paid for itself"**. Catch: none of the slide's three points says *why*, so the body may need a fourth point, or point 02 reworded, to back up the title. | Owner picks wording |
| 4 | Cover paragraph | deck README, "Still to tune on the cover" | Draft (adds a fourth line at 34px; the cover has room): *"I ran eleven antibody design experiments on Amazon Bio Discovery, then built a backend to store and interpret what came out. Storing it flexibly was easy: any pipeline's columns, no migrations. Interpreting it was the hard part, because the same score column means something different depending on what went into the run."* | Owner picks wording |
| 5 | Vocabulary gap on 16, 17, 18 | deck README, "The vocabulary gap on 16, 17 and 18" | Not started. *checks* needs body work; "stage four" (17) and "invariant" (18) come up with no introduction. Titles for all three are held until the bodies are fixed. | Discuss before drafting |
| 6 | Every slide ends on a caveat | deck README, "Every content slide ends on a qualifying sentence" | Not started. A structural fix, not a word swap; the same habit as "do not lead with the limit". | Discuss before drafting |
| 7 | Boltz-2 reruns | `docs/boltz2-rerun-plan.md` | Not started. **Needs the PC's GPU.** Its result could replace the headline 0.09 on *corpus* and *humanization*, so do it before the deck is called finished. | Owner, on the PC |

### Raised during the title pass, not yet decided

The title pass itself is done (commit `57a8487`). It turned up three follow-ups:

- **Twelve or sixteen?** The 2026-11-12 to-do said "the twelve titles"; the deck README marks sixteen
  as agreed, and all sixteen were applied. If four were meant to wait, they need reverting.
- **Slide 7, *humanization*: "Humanization traded binding for safety"** claims two things the body
  denies. Neither run measures humanness, so "safety" isn't shown. There is no antibody binding
  number, so "binding" is standing in for ipTM. That's the same overclaim as "predicted binding
  probability", which the deck README records as corrected. Suggested replacement: **"What
  humanization cost in placement confidence"**.
- **Slide 20, *open*: "Every number here is predicted"** nearly repeats the heading of its own fourth
  card ("Everything here is predicted"), the same knock-on *stack* had. It also names a four-card
  slide after one card, and it opens on a limit. Either the card or the title has to change.

---

## Elsewhere: open threads, by pointer

Not active right now. Listed so a cold session knows they exist. Each home doc has the detail.

| thread | home | state |
|---|---|---|
| Request isolation (READ COMMITTED vs REPEATABLE READ) | GitHub issue #17; `docs/scoreentry-plan.md` | Open question |
| Get catalog identity fields into an object at the SQL boundary | `docs/bundle-at-the-boundary.md` | Agreed, not done |
| Test-review pass | `docs/test-review-plan.md` | Collecting items |
| Ingest published external antibody data | `docs/published-data-goal.md` | Stretch goal, not started |
| Load the ESM2 pair (runs 9, 10) | `experiment_results/load_experiment_results.py` docstring; *predicted-property* slide | Deliberately "not yet" |
| Rebuild tombstoned files | `CLAUDE.md`, "Tombstoned" | Not started |

---

## Machine notes

- **Cloud sessions** can do all of the deck work. There is no Docker there, so no tests against
  Postgres.
- **PC** is the only machine for item 7 (GPU).
- Never start or stop Docker on the owner's behalf (see `CLAUDE.md`).
