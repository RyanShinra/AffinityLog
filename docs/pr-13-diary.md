# PR #13 diary — the metric catalog, and what reviewing it turned up

> **Status: historical record, written 2026-08-21 while the PR was still open.** A narrative of one
> pull request: what it set out to do, what it actually became, and the eleven things that turned
> out to be wrong along the way — several of them in work that had already been checked. Kept
> because the commit messages record *what* changed and this records *why the shape of the work
> kept moving*.

**Span:** 2026-08-09 → 2026-08-21 · **20 commits** · 23 files, +2168 / −138 ·
~7,700 words of commit messages · 67 → 93 tests

---

## What it was supposed to be

One chapter of the GraphQL layer: `Context.catalog()`, a per-request cache of the metric catalog,
so a `ScoreEntry` resolver could ask "what does this score key mean?" without a query per key. The
sizing made the design obvious — 144 catalog rows, 1,132 `ScoreEntry` objects in a full
`{ candidates { scores } }`. Load the catalog once, turn 1,132 round trips into 1,132 dict lookups.

Roughly forty lines of code.

## What it became

Twenty commits, a rebuilt test suite, a write-time invariant check that did not exist before, a
concurrency fix for a bug that predates the branch entirely, and six corrections to documentation
this same branch had made stale. The forty lines are in there somewhere.

That is not scope creep in the usual sense. Every addition came from pulling on something that was
already loose — and the pattern worth recording is that *almost none of it was found by writing
code*. It was found by measuring, by naming things out loud, and eventually by a review that
contradicted its author.

---

## Act I — reconstructing what "the corpus" meant

The branch resumed mid-task on a different machine. The previous session had left `catalog()`
scaffolded with a `YOUR TURN` block and a 475-word commit message as the handoff, on the correct
theory that commit messages travel and memory files do not.

The first real work was not code. It was answering *which CSVs actually make up the corpus*, because
the repo did not say. `CLAUDE.md` claimed nine experiments, 14 candidates, 26 chains, 200 score keys
— but there are eleven CSVs on disk and nothing recorded which were loaded, or under what names.

Reconstructing it took four independent measurements before the numbers reconciled:

| | |
|---|---|
| CSVs on disk | 11 |
| CSVs that load | **7** |
| experiment rows they produce | **9** |

The gap is that `load_csv` creates one `Experiment` per distinct `experimentId` **in a file**, and
experiments 1 and 5 each span two sub-experiments. Seven files, nine rows. Every other count then
fell into place, including the 200 distinct score keys — adding the two ESM2 experiments takes it to
204, which is how we confirmed they were deliberately excluded rather than forgotten.

That became `experiment_results/load_experiment_results.py`: provenance kept executable, so the next
cold machine does not re-derive it. The reason it lives beside `CORPUS_MANIFEST.md` rather than in
`scripts/` is that it is *data about one corpus that happens to run*, not a general tool.

**Blog-worthy bit:** the reason experiments 9 and 10 are excluded is not a data problem, it is a
*physics* problem. The ESM2 Only recipe is a property predictor — it folds nothing — so those
candidates have no predicted structures to pair with. The manifest states it from the other side
("Boltz2 folds exist for exp 1/2/3/5/6/8/11"), which is exactly the seven that load.

---

## Act II — the naming conversation

`catalog()` returns two indexes. The first draft called them `by_identity` and `variant_kinds`. This
took the better part of an hour to fix and was worth every minute, because the confusion was real:
the owner had to ask twice what `variant_kinds` meant, having written half of it himself.

The diagnosis was that three dicts used two naming conventions:

| name | named for its… | key | value |
|---|---|---|---|
| `by_identity` | **key** | identity tuple | one `Metric` |
| `variant_kinds` | **value** | (module, column_key) | a **set** of kinds |
| `interface_kinds` | **value** | candidate id | **one** kind |

So `variant_kinds` was plural because each *value* was plural, while `interface_kinds` was plural
only because there were many *candidates*. Same suffix, structurally different meaning.

Three rounds of refinement followed, each one rejecting the previous answer for a specific reason:

1. **`by` everywhere** → rejected: an identity tuple is a lookup handle you construct, a column is a
   thing that *has* variant kinds. The distinction carries information.
2. **`of` for the attribute case** → rejected on English grounds. `variant_kinds_of_column` parses
   as *variant (kinds of column)* — "kinds of X" is a stronger collocation than the binding we
   meant. `interface_kind_of_candidate` has the identical problem: it reads as a taxonomy of
   candidates.
3. **`per`** → adopted. It cannot misbind, and it signals a mapping outright.

Then the owner made the sharper observation: **"column" is overloaded in a database application.**
`column_key` holds a raw CSV export header, not a SQL column. So `heading`.

Final: `metric_by_identity` and `variant_kinds_per_heading`. Two prepositions, each meaning
something — `by` for a constructed lookup handle, `per` for one entry per domain entity — and
singular/plural following real cardinality rather than the number of keys.

**Why this belongs in a blog post:** the rename cost nothing at the time (one construction site,
zero readers) and would have cost a great deal a week later. And the trigger for it was not a style
review. It was a person saying *"something about these names throws me and I can't put my finger on
it."* That instinct was correct and it was pointing at a real structural inconsistency.

---

## Act III — the invariant, and where enforcement belongs

The two-tier lookup depends on something the schema never states. `metrics` is unique on
`(module_id, column_key, variant_kind, variant)`, which happily accepts both:

```
(boltz2, foo, PARAMETER, 'esm')
(boltz2, foo, INTERFACE, 'antibody-target complex')
```

They differ in `variant_kind`, so they do not collide. But the resolver relies on a **functional
dependency**: `(module_id, column_key) -> variant_kind`. Break it and score resolution goes wrong
silently.

The question that produced the good conversation was: *what happens if that set has two entries?*
The answer turned out to be that no lookup can fix it — a metric row carries **one**
`(variant_kind, variant)` pair, so there is no row for "esm **and** complex". The cross product has
nowhere to live. A two-axis heading is incoherent at rest; the resolver is only where you'd notice.

Which raised the owner's sharpest question of the branch:

> "If we're throwing when the GraphQL model is being built, but it's allowed in the database, then
> that's a pretty hefty leaky abstraction."

Correct, and it moved the fix twice:

- **Not in the resolver.** The resolver only reads, and raising there takes down every GraphQL
  request for all 144 metrics because one row is miscataloged.
- **At write time**, in both seeders, before `commit()`, so a bad catalog rolls back rather than
  landing.
- **And structurally, eventually** — Postgres cannot express a functional dependency with `UNIQUE`
  or `CHECK`, but it can with a `metric_headings` parent keyed on the heading plus a composite FK,
  which makes a second axis *unrepresentable*. Written up in
  `docs/metric-heading-normalization.md` as a deliberately deferred migration rather than done.

**One premise correction worth recording:** the owner's first guess was that the permissiveness came
from JSONB flexibility. It did not — `variant_kind` and `variant` are ordinary typed columns. The
JSONB is `candidates.scores` and is not involved. That mattered, because it changed where a fix
could go.

---

## Act IV — the test fixtures, and four things Postgres teaches you

`tests/conftest.py` had been a two-line tombstone since the schema-first redesign, so `catalog()`
had **no caller and no test**. Those turned out to be the same problem: a test *is* a caller.

Rebuilding it surfaced four constraints, none of them obvious from the outside:

1. **`metadata.create_all()` cannot be used.** `candidate_summary` is a VIEW created by migration
   004, and `app/models/views.py` is deliberately excluded from the metadata so autogenerate does
   not emit `CREATE TABLE` for it. `create_all()` therefore produces every table and no view — and
   the two-tier lookup reads `interface_kind` from that view. Migrations are the only path to a
   complete schema.
2. **The container fixture must stay synchronous.** `migrations/env.py` ends in `asyncio.run(...)`,
   which raises if a loop is already running.
3. **`NullPool` is mandatory.** asyncpg connections belong to the event loop that opened them, and
   pytest-asyncio gives each test a fresh loop. A pooled connection handed to a later test is
   attached to a loop that no longer exists.
4. **Postgres has no nested transactions.** It has `SAVEPOINT`, which is what SQLAlchemy's
   `join_transaction_mode="create_savepoint"` emits — the mechanism that lets the code under test
   `commit()` without escaping the fixture's rollback.

Point 4 prompted an aside worth keeping: *this is the sort of thing "2 years of Postgres" on a
résumé ought to mean, and it never came up on Bloomberg's proprietary MySQL layer.* SAVEPOINT is
SQL-standard and MySQL/InnoDB has it too — what varies is whether your tooling ever surfaces it.

The fixture data was hand-written rather than seeded from `seed/catalog.json`, and the owner's
argument for that is the best one-line defense of brittle tests I have heard:

> "Hand written, brittle test data failing when code is updated, even when the new code is fully
> correct, is a sign that you need to examine and update the test. It forces you to properly
> consider the new code and test together."

A fixture built from production data passes for reasons nobody chose.

---

## Act V — the review, and the reviewer being wrong

A max-effort review ran over the 1,480-line diff: ten finder angles, a verification pass, fifteen
findings. Nine were real bugs. The uncomfortable part is that the two most serious were in code that
had already been described — in this branch's own commit messages — as *verified*.

### The worst one was not in this PR at all

The filed finding was that `catalog()`'s memo races. True: 14 concurrent callers produced **14
distinct catalogs and 14 queries**, because graphql-core gathers the `scores` resolver across the
candidate list and all 14 pass the `is not None` check before any returns.

Chasing it produced something worse. The failure is not the memo, it is the **shared session**:

```
virgin session, 4 concurrent execute()  ->  3 raise InvalidRequestError
warm session,   4 concurrent execute()  ->  0 raise
```

So the crash fires on the *first* concurrent touch of a request. Which predicts a specific legal
query should 500 — and it does:

```
{ candidates { sequenceId } }                        ->  0 errors
{ candidates { sequenceId } experiments { name } }   ->  IllegalStateChangeError
```

Two root fields, gathered onto a session with no connection yet. It raises out of the session's own
`__aexit__`, so the request 500s with a poisoned session and `MaskInternalErrors` never sees it.
That query touches only `Query.candidates` and `Query.experiments` — neither file was in this diff.
**It has been on `main` all along.**

Fixed out of band, with a starred block in the commit message saying so. The lock went on the
session, not on `catalog()`, because fixing only the memo would have left a known 500 in place.

**Method note:** the first attempt to reproduce this *failed* — the fixture's session was already
warm, which is precisely the state where it does not reproduce. Getting a false negative and
recognizing it as one was the step that found the real trigger.

### Three tests that passed for the wrong reason

This is the theme of the review, and it recurred three times:

- **`test_relationships_are_eager_loaded`** passed with both `selectinload`s *deleted*. The fixture
  creates the Modules in the same session, so a lazy load answered from the identity map
  short-circuits with no IO. `expunge_all()` fixed it — which is what a real request looks like
  anyway.
- **`test_two_root_fields_do_not_break_a_virgin_session`**, the first version, passed *against the
  bug* for the warm-session reason above.
- **A regression-test verification** was worthless because the revert script used `str.replace` with
  twelve spaces of indent where the file has eight. `str.replace` says nothing when it matches
  nothing, so the "reverted" tree still had the fix. Every subsequent revert used an assertion on
  the anchor — and that caught two more silent misses later in the same session.

### The review corrected its own reasoning

The finding about the invariant check said it would block the documented raw/`-transformed` catalog
addition, *because the bare row can never be reached*. Half right. Building the shape and measuring
both halves side by side:

```
the check said : fastdpe.SFvCSP: has 1 variant-less row(s) beside
                 ['TRANSFORM'], which can never be reached
the lookup did : fastdpe.SFvCSP -> "SFvCSP (Fv charge symmetry)"
```

It reaches it perfectly well. Tier one qualifies **only on INTERFACE**, the one axis whose variant
is absent from the key string. So it was a plain false positive — a write-time check refusing a
legitimate, documented shape, which is worse than no check because it blocks the work rather than
the error.

That also falsified a comment written earlier in this same branch, which had asserted tier one
"always qualifies". The review inherited the error from the code it was reviewing.

---

## Recurring themes

**Measure, then assert — and expect to be wrong.** Every substantive finding in this branch came
from building the case and running it, not from reasoning about it. Several times the measurement
contradicted a confident prediction made moments earlier, including twice by the same author in the
same session.

**A test that cannot fail is worse than no test**, because it reports coverage it does not provide.
The reliable check is to break the thing on purpose and confirm the test goes red. It caught three
worthless tests here.

**Silent no-ops are the enemy.** `str.replace` matching nothing, a skipped test suite exiting 0, a
lookup missing and returning `null` — the shape is identical: the system reports success for work it
did not do. Three separate fixes in this branch are variations on *make the silent case loud*.

**Documentation drifts fastest in the branch that changes it.** Six of fifteen findings were
documents this PR itself made stale, including a count that was simply invented. `CLAUDE.md`'s own
convention — every number is a measurement someone can re-verify — is what made them findable.

**Naming is structural, not cosmetic.** The `by`/`per`/`heading` conversation looked like
bikeshedding and was not: it exposed that two dicts with the same suffix had structurally different
cardinality.

---

## Where it ended

**14 of 15 findings closed.** 93 tests, all green; ruff, black, `mypy app/` and the view/migration
check clean.

The one left open is deliberate. `_identity_for` in `tests/test_context_catalog.py` is still a
second implementation of the two-tier lookup, so the ipTM tests assert against a copy rather than
production code. Closing it means putting the lookup on `Context` — which *is* the `ScoreEntry`
resolver's core, and `CLAUDE.md` is explicit that the decision-carrying pieces are the owner's to
write. It is flagged in the file and belongs with the planned test-review PR.

**Still ahead in the chapter:** `Context.interface_kinds()`, `ScoreKey.identity`, the
`Metric`/`Module`/`Concept`/`ScoreEntry` types, and `Candidate.scores` with its three filters. Plus
`Query.modules`, `Query.metrics`, `Candidate.target`, `Candidate.artifacts` and `Mutation` entirely
— all declared in the SDL, none implemented.

---

## Act VI — the second pass, which answered the open question

The diary originally ended on a choice: second pass before merge, or close the PR and move on. We
ran the second pass. It found **fifteen more findings, and they were concentrated in the fixes** —
the ~700 lines written *in response to* the first review, which had never themselves been reviewed.

That is the most useful thing this branch taught, and it is worth stating plainly: **fix code
written under review pressure gets less scrutiny than the code it fixes.** The first pass found nine
real bugs across 1,480 lines. The second found a comparable density in 700 lines of remediation, and
three of them are ironic in a way that is hard to argue with.

### The three that sting

**The satisfiability check has a false negative in exactly the shape it was written to catch.**
`DECOMPOSABLE_VARIANT_KINDS` flattens `_VARIANT_RULES` to `{'PARAMETER'}`. But those rules are keyed
by *module* and their regex pins the *column* — so PARAMETER is only recoverable for
`evoprotgrad.pseudolikelihood_ratio`. Measured:

```
decompose('evoprotgrad.esm_entropy')          -> (evoprotgrad, esm_entropy, None, None)
decompose('boltz2.esm_pseudolikelihood_ratio') -> (boltz2, esm_pseudolikelihood_ratio, None, None)

Heading('evoprotgrad', 'entropy', ('PARAMETER',), 0).problems()  ->  CLEAN
```

A heading of PARAMETER rows under any other column passes the check while nothing can resolve it.
The fix needs a rule lookup keyed by `(module, column_key)`, not a set of kind names.

**The "trustworthy" Docker probe added a new way to be wrong about a working daemon — on the same
commit that made being wrong fatal.** `DockerClient.__init__` performs a *registry login* when
`DOCKER_AUTH_CONFIG` is set. So a rate-limited Docker Hub now fails the build with "Docker is not
reachable". That commit's own message argues a probe which can be wrong must not be the thing that
fails a build. It then made the probe wronger.

**The leak regression test fails open.** `test_a_commits_through_the_apps_own_sessionmaker` has no
assertion at all, so `pytest -k test_b_sees_none_of_it` or `pytest --lf` runs B alone against an
empty schema and passes vacuously. The verification that the pair catches the bug was real — but
only for the one ordering that happened to be run.

### The rest, as a work list

Roughly in severity order. This is the **first thing to do in the next branch**, before any new
resolver work:

| # | where | what |
|---|---|---|
| 1 | `app/catalog/keys.py` | `DECOMPOSABLE_VARIANT_KINDS` needs `(module, column_key)`, not a kind set |
| 2 | `app/catalog/invariants.py` | a row with NULL `variant_kind` but non-NULL `variant` counts as bare and suppresses all three branches |
| 3 | `tests/conftest.py` | Docker probe does a registry login; use `docker.DockerClient(base_url=get_docker_host())` |
| 4 | `tests/test_context_catalog.py` | make the leak test self-contained — commit, then read from a *second* connection in the same test |
| 5 | `tests/conftest.py` | `os.environ.get("CI")` is a presence test; `CI=false` fails the run |
| 6 | `app/catalog/invariants.py` | INTERFACE coverage is never checked *per heading* — a heading with one of three variants passes clean |
| 7 | `scripts/seed_catalog.py` | dry-run catches `OSError` only; an unmigrated database gives `ProgrammingError` |
| 8 | `tests/conftest.py` | `configure()` merges, so `join_transaction_mode` is welded on permanently and cannot be reset |
| 9 | `tests/conftest.py` | the flagged block states a guarantee five `test_importer.py` tests already break |
| 10 | `tests/conftest.py` | says "eight modules" (it is ten); and `app.database.engine` is a second unguarded path to the corpus |
| 11 | `tests/test_context_catalog.py` | the virgin-session test reopens the second door from the test side |
| 12 | `scripts/seed_catalog.py` | `--dry-run` still documented as "touch nothing"; it now upserts |
| 13 | `tests/conftest.py` | the probe rewrites `DOCKER_HOST` and leaks an unclosed client |
| 14 | `tests/test_catalog_invariants.py` | docstring cites a `HAVING` that no longer exists |
| 15 | `app/graphql/context.py` | nothing *enforces* that code holding `_session_lock` avoids `self.execute()` — a comment is the only guard against a silent deadlock when `interface_kinds()` is written |

Note the shape of the list: **ten of fifteen are in test and tooling code**, and five are documentation
claims that were false the moment they were written. The application code came out comparatively
well, which is the opposite of where attention naturally goes.

### What that decided

Fixing these in-branch would produce a *third* generation of fix code with the same problem, inside
a PR already at 2,168 lines. So: **merge, and open a dedicated branch for them.** The remediation
gets to be its own reviewable unit rather than an appendix to a feature branch — which is exactly
what the second pass demonstrated it needs.

---

## What comes next

1. **"Spring cleaning in summer"** — the fifteen above, as their own branch and their own review.
2. **The rest of the GraphQL** — `Context.interface_kinds()`, `ScoreKey.identity`, the
   `Metric`/`Module`/`Concept`/`ScoreEntry` types, `Candidate.scores` with its filters. Then
   `Query.modules`, `Query.metrics`, `Candidate.target`, `Candidate.artifacts`, `Mutation`.
3. **The test-review pass** already planned, which is where `_identity_for` finally stops being a
   copy of the lookup and starts calling it.

One thing to carry forward into all three: every fix in this branch that survived scrutiny was one
where the failure had been *reproduced first* — the memo race, the fixture leak, the eager-load test.
Every fix that did not survive was one written from a description of the problem rather than a
demonstration of it. That is a sharper rule than "write tests", and this branch is the evidence for
it.
