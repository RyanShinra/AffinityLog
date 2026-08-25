# docs/ — what is in here and what to trust

Most of these were written *during* the work rather than after it, so several are handoff notes or
lab logs rather than reference documentation. That is useful — the reasoning is preserved — but it
means some describe a state the project has since moved past.

**Read `../README.md` first.** It is current by construction: every factual claim in it was verified
against the running database before it was written.

## Current — safe to rely on

| doc | what it is |
|---|---|
| [`schema-stress-log.md`](schema-stress-log.md) | **Start here after the README.** A running log of what each of the 11 experiments revealed about the schema, newest findings at the bottom of each section. The 2026-07-31 entry is the ipTM discovery the whole design turns on. A lab notebook, not a tutorial — dense, but nothing else in the repo explains *why* the schema looks like this. |
| [`demo-biology.md`](demo-biology.md) | The biology behind the `/demo` page, written for a software engineer with no biology background. Includes the humanization trade-off the featured structures illustrate, a glossary of the score names, and explicit caveats about what these numbers are not. |
| [`recipe-topology-note.md`](recipe-topology-note.md) | Why recipe DAG *edges* are not stored yet, and what storing them would require. Still an open decision. |
| [`metric-heading-normalization.md`](metric-heading-normalization.md) | Why `(module, column_key) -> variant_kind` is a real invariant the schema cannot express, the composite-FK normalization that would make it structural, and why that migration is deferred in favour of a write-time check. Still an open decision. |
| [`type-safety-plan.md`](type-safety-plan.md) | **The plan for PR #15.** Making the domain vocabulary un-mistypeable: real enums for the 11 closed-set strings, `NewType` for the 49 identifiers, and the five stages it lands in. Carries the facts that took measuring — why moving `VariantKind` needs no migration, why `NewType` cannot cross into Strawberry. All five stages shipped. Two of its stated facts were wrong and are corrected in place. |
| [`pr-15-diary.md`](pr-15-diary.md) | **Live, not historical.** The narrative of the `type-safety` branch, written as each stage lands rather than after. Records why the plan keeps changing shape — including two claims the plan had to correct about itself. Read `type-safety-plan.md` for what happens next; read this for why. |
| [`scoreentry-plan.md`](scoreentry-plan.md) | **The plan for PR #16.** The ScoreEntry chapter: what is still unbuilt in the GraphQL layer, the order it has to land in, and why `Context.interface_kinds()` has to come first. Names what PR #15 handed over and did not close. Agreed, not started. |
| [`published-data-goal.md`](published-data-goal.md) | The stretch goal: ingest published external antibody data to test whether the schema is genuinely general rather than fitted to one vendor's export. Not started. |
| [`graphql-schema.md`](graphql-schema.md) | **The committed GraphQL spec.** The SDL, the four decisions behind its harder choices, and the measurements those rest on. Written before any resolver existed, so it was reviewed as a document rather than as already-typed code. Supersedes the SDL discussion in `graphql-schema-handoff.md`. |
| [`schema-erd.md`](schema-erd.md) | Entity-relationship reference for the tables. Assumes some domain familiarity. |

## Historical — accurate about their moment, stale about now

Kept because the reasoning is worth preserving, but do not treat them as descriptions of the current
state. Each carries a status banner at the top.

| doc | why it is stale |
|---|---|
| [`catalog-seed-plan.md`](catalog-seed-plan.md) | Written before any real experiment ran, so it plans to curate the catalog from module READMEs — guessing what modules *might* emit. That approach was inverted once real data existed: the corpus is now the source of truth for what exists, and READMEs supply only meaning. Its *mechanics* (JSON seed file, idempotent upserts on natural keys, two-pass name→id resolution) were followed and are still accurate. |
| [`graphql-schema-handoff.md`](graphql-schema-handoff.md) | The GraphQL layer was designed before the data landed. The type design and the "GraphQL is the apparent representation" principle still stand, but it describes a data model that predates migration 003 and states that no real candidate output exists. Its SDL is now committed separately in [`graphql-schema.md`](graphql-schema.md) — read that for the contract, this for the reasoning. |
| [`first-run-findings.md`](first-run-findings.md) | A deep analysis of the *first* real export (2026-07-04). Correct about that run; superseded in breadth by `schema-stress-log.md`, which covers all 11. |
| [`aws-extension-letter.md`](aws-extension-letter.md) | Correspondence about extending the trial. Context only. |
| [`pr-13-diary.md`](pr-13-diary.md) | A narrative of PR #13 — what the metric-catalog chapter set out to be, the naming and layering conversations that reshaped it, and the max-effort review that found nine real bugs including one that predates the branch. Written as a record of *why the work kept moving*, where the commit messages record what changed. Primary source for the development blog. |
| [`pr-14-diary.md`](pr-14-diary.md) | A narrative of the `spring-cleaning-in-summer` branch, which closed all fifteen defects the PR #13 review found in its own fixes. The long half is one item — how a tripwire around a non-reentrant lock turned into `TaskSafeSession` — because a comment warning you not to do something is a design telling on itself; the epilogue covers the other fourteen. Primary source for the development blog, with PR #13's. |
| [`handoff-081026.md`](handoff-081026.md) | A raw session transcript from the PC → Mac handoff mid-`Context.catalog()`. Every instruction in it is superseded by later commits on the same branch; kept for the record of how the two-machine workflow actually went. Carries a do-not-follow banner. |

## Not documentation

- `module_evaluation_review.xlsx` — a spreadsheet of AWS's per-module benchmark pages, the raw
  material for the `benchmark_*` tables that were deliberately never populated.

## If you are an AI assistant starting a session here

Read `../CLAUDE.md` (conventions, and how the owner prefers to work), then `../README.md`, then the
stress log. Be aware that everything under **Historical** will contradict the README — the README wins.
Verify claims against the running database rather than against any document, including this one.
