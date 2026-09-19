# PR #16 diary — the chapter where a number learns what it measures

> **Status: written as the branch closed (2026-09-19), with the early acts reconstructed from their
> commit messages rather than from memory.** `docs/scoreentry-plan.md` is the plan and stays
> authoritative for *what* was built and in what order; this file records what it cost and what it
> taught. Where the two disagree, the plan is the one that was kept current as each stage landed.

**Span:** 2026-09-04 → 2026-09-19 · **branch:** `score-entry-resolvers` · 26 commits · 32 files ·
+3942 / −261 · 9 → 22 SDL types · 158 → 236 tests · five stages, in the order 1, 1b, 3, 2, 4, 5 ·
merged as `328d069` · one follow-up, issue #17

---

## Where it came from

From the last line of the previous branch. PR #15 typed the domain vocabulary end to end so that
*"our home stretch on graphQL is as stable as possible"* — and this is the home stretch. Everything
PR #15 made un-mistypeable exists to be consumed here.

It is also the chapter the *schema* was designed for. The three-layer data model, the catalog, the
`candidate_summary` view and its `CASE` were all built between 2026-07-16 and 2026-08-10 on the
strength of one finding, and until this branch not one line of the API could demonstrate it. The
plan said so in its own success criterion, which is the sharpest sentence in it:

> Not "the resolvers return data". It is whether this query returns three DIFFERENT display names
> for one JSONB key, chosen by what each candidate folded. [...] Nothing in the API demonstrates it
> today.

## The finding, for anyone reading this cold

`boltz2.protein_iptm` is one column in the Bio Discovery export. It measures three different
physical quantities depending on which chains went into the fold:

| the candidate folded | what ipTM means | range in this corpus |
|---|---|---|
| heavy + light + target | real HER2 binding confidence | 0.196 – 0.793 |
| heavy + light | the antibody pairing with *itself* | ~0.95 |
| one chain | nothing; there is no interface | 0.000 |

The discriminator is in neither the key nor the value. It is a property of the candidate. And the
meaningless readings score *highest*, so `ORDER BY` on the raw number ranks antibodies that never
met the antigen above the best real binder in the corpus.

Everything below is machinery for answering one question honestly: **which of those three is this
number?**

---

## Act I — the keystone, and a harness that lied

**`12d6d28`, `42640a2` · stage 1**

`Context.interface_kinds()` is the candidate-side half of the lookup, and nothing else could start
without it. Two comments in the codebase already referred to it as though it existed, and CLAUDE.md
had given it a design constraint before a line of it was written.

Its shape came from `catalog()`: one query for all candidates rather than one per candidate,
memoized per request, double-checked inside the lock, handed out behind a `MappingProxyType`. The
one genuinely new decision was the lock — a **third** one, not a reuse of `_catalog_lock`. The two
builds never nest, so sharing would not have deadlocked today. But it would have serialised two
unrelated memos, and it would have put one object back in charge of two invariants, which is the
exact shape that deadlocked in PR #14. One lock per invariant is the rule; this was the third
invariant.

### The key is the vendor's, and it is only unique per experiment

`candidate_summary.candidate_id` is `candidates.sequence_id` — the `id` column of the export — while
`uq_candidate_seq` constrains `(experiment_id, sequence_id)`, not `sequence_id` alone. The map spans
all nine experiments. Two colliding rows would fold into one entry and hand one candidate the
*other's* interface kind: heavy-light pairing confidence reported as HER2 binding, which is the
precise inversion the whole design exists to prevent.

Measured before deciding: 14 candidates, 14 distinct ids, and the two same-antibody pairs in the
corpus carry *different* vendor ids across runs. So no collision exists, and the guard is
belt-and-braces against a guarantee the schema does not make rather than against an observed fault.
It was still proved by forcing one — a duplicate `sequence_id` under a second experiment, inside a
rolled-back transaction, made the view return two rows for one id and the guard fired.

### The harness lied first, which is the point

The tests were mutation-tested: each guard was reverted and the test watched go red, because a green
test is evidence of nothing until you have seen it fail. Seven mutants, seven reds.

The first run reported **five of six surviving**. That was not a result, it was a broken probe: a
malformed pytest node id sent a usage error to stderr while the check read stdout, so every run
looked green. The rewrite verifies *itself* before it verifies anything else — each target must
select exactly one test and pass unmutated, and the run aborts otherwise.

That self-check immediately found a real defect it was not looking for. `-k
test_the_memo_survives_concurrent_callers` selected **two** tests, because the new interface-memo
test had been given the same name as the existing catalog one in another class. A check that
silently covers something other than what it claims — the same family as the `_LEAVES` /
`_ENTRY_POINTS` / `_MIRRORED` mistakes on the type-safety branch, and not the last time this branch
met it.

---

## Act II — the crown jewel was guarded by a copy of itself

**`f786597`, `b3b6c64` · stage 1b, which the plan did not have**

The two-tier lookup existed. It lived in `tests/test_context_catalog.py::_identity_for`, written
longhand, and its own docstring admitted the arrangement was temporary. Which meant
`TestTheIptmFinding` — the test the module docstring calls the one that matters — was asserting the
finding **against a copy of the logic rather than against anything in `app/`**. A resolver written
with the branch inverted would have left it green.

So stage 1 grew a half: name the types the lookup is assembled from, move it onto `MetricCatalog`,
delete the copy. The spec was written first and committed red on purpose, with `RED ON PURPOSE` in
its first line and an instruction not to push it alone.

### Promoting a tuple to a class turned three of four defects into compile errors

The types add no behaviour the tuples lacked. What they buy is reading speed:

```python
Mapping[tuple[ModuleName, ColumnKey], frozenset[VariantKind]]   # ten seconds
Mapping[Heading, VariantAxes]                                   # one
```

and `if axes.interface_qualified:` says what is being asked where `if VariantKind.INTERFACE in
kinds:` merely permits you to work it out.

Four defects were found landing it. Three became compile errors the moment the alias became a class.
The fourth was invisible to mypy and is the one worth keeping:

> `metric_for` fell through to the raise for **every ordinary key.** `.get()` with no default
> returns `None` for a heading with no variant axes — 140 of 144 catalog rows — so control reached
> `if interface_kind is None: raise`. `temstapro.clash` would have raised
> `INTERFACE_KIND_REQUIRED`.

Caught by three tests written before the code, and confirmed by restoring the original and watching
them go red. `VariantAxes.none()` as the default is what removed it: the lookup answers
unconditionally, instead of the caller branching on absence before it can branch on the answer.

### And the file's own warning, turned on itself

`tests/test_extract_score_keys.py` derived its arity as `len(get_args(MetricIdentity))`. That works
only while `MetricIdentity` is a `tuple[...]` **alias**. Promoting it to a `NamedTuple` makes
`get_args` return `()` — arity zero, the mirrored-field list empty, and `@parametrize` over an empty
list runs **zero tests while reporting success.**

That is the fourth time this repo has shipped a derived constant that silently covers nothing, and
it was sitting in the file whose job is to catch exactly that. Changed to `_fields`, which fails
loudly today and is correct afterwards.

---

## Act III — the errors nobody could read

**`1695043`, `f7f0f61`**

Fourteen stage-3 tests failed. Every one of them read `Internal server error.` where the message
should have been `Cannot query field 'metrics' on type 'Query'.`

`MaskInternalErrors` was replacing the message of *any* error without a deliberate `code`, including
the ones graphql-core raises during parse and validate. Measured on the live schema: a nonexistent
field, an unterminated document and a bad field on `Candidate` all reached the client masked, with
the real message going only to the log. Every client typo, for as long as the extension had
existed, had come back as "it broke."

That contradicts the extension's own rationale, which is about not leaking SQL, hostnames and ports.
A parse or validate error runs **before any resolver**, so nothing internal has executed and there
is nothing to leak; the schema is introspectable by design.

The discriminator turned out to be `original_error`: GraphQL's own errors carry none, while anything
a resolver raised is wrapped and carries one.

### The follow-up, which is the better story

The fix used a compound boolean and left a pre-existing test file untouched. `tests/test_error_masking.py`
went red, and it was right to.

`is not None` is not truthiness. The original predicate asked `not (extensions or {}).get("code")`,
so a code of `""` was falsy and masked. The rewrite asked `"" is not None`, which is True — so an
error whose code someone forgot to fill in **started publishing its message.** An omission must fail
toward silence, and that one failed toward noise.

Then the more uncomfortable half: **four of that file's tests were fictional.** They built bare
`GraphQLError(...)` objects with comments claiming they were "what SQLAlchemy, asyncpg and any
ordinary bug produce". True of the intent, never of the object — graphql-core *wraps* whatever a
resolver raised, so a real one always carries an `original_error`, and a bare one is the signature of
a syntax error instead. Under the old one-condition predicate the difference was invisible, so they
passed for months while exercising a value that cannot occur.

Note which tests did **not** break: `TestMaskingEndToEnd`, which raises a real `RuntimeError` through
a real schema. The end-to-end case was right where the unit cases were fictional, which is an
argument about test altitude that this project keeps re-learning.

---

## Act IV — stage 3 before stage 2, on purpose

**`0c85303`, `120d16d`**

The plan had ordered them the other way. `ScoreEntry.metric` points at `Metric`, and
`ScoreEntry.numericValue` is specified as "null unless `metric.valueType` is FLOAT or INT" — so
stage 2 could not be built without at least part of stage 3. Building the pointer first would have
meant publishing a partial `Metric` in the SDL and growing it a commit later: a public contract
changing twice for no reason.

The stages were swapped and **deliberately not renumbered**, because the commits reference the
original numbers.

Stage 3 was also cheaper than the plan implied. `_load_catalog` already eager-loads `module`,
`concept`, `benchmark_results` and `transform_of`, so it was type declarations over data already in
memory: no new queries, and no `MissingGreenlet` risk.

Three things it decided:

* **`Module.metrics` is a resolver, not a field.** Structural rather than stylistic: `Metric.from_row`
  builds its `Module`, so a `Module.from_row` that built its metrics would recurse without end.
* **`Metric.transformOf` is not exposed, though the spec lists it.** Nothing writes
  `transform_of_metric_id`, and `selectinload` loads exactly one level, so recursing into the parent
  touches ITS unloaded `module` and raises. A test asserts the **absence**, so the gap reads as a
  decision rather than an oversight.
* **A Strawberry enum binding cannot be used as a type annotation.** `strawberry.enum` returns
  `EnumType | Callable[[EnumType], EnumType]` — a union serving both decorator forms — so
  `module_type: ModuleType` is rejected while `db.ModuleType` is fine. Those module-level lines are
  registration statements, not aliases.

---

## Act V — stage 2, and a change in how the work was done

**`de3480b`, `0b6ea4f`**

This is the chapter's point, and it is where the collaboration changed shape.

CLAUDE.md's standing rule is that decision-carrying code is the owner's, with tests and mechanical
fallout as the two exceptions. Stage 2 opened the usual way — a plan, the four judgement calls named,
a scaffold with `YOUR TURN` raising `NotImplementedError` at each spot. Then:

> Let's set it up with everything shown in the chat, so that it's Mavis Beacon Teaches Typing, (or at
> least she teaches copy pasta). Interrogating all the code before it goes in has a better chance of
> me learning and ironing out idiosyncrasies like the huge ternary mess from yesterday.

So the mode became: **every line, tests included, posted in the chat as a locale list, read, and
pasted in by hand.** Not because the code was hard, but because reviewing a finished diff and
reviewing code on its way in are different activities, and only one of them catches a style you did
not want. It is the same economics argument as the 2026-08-03 note in memory, arrived at from the
opposite direction.

The scaffold landed with two helpers raising, and the red was informative: both tests failed with
`NotImplementedError: YOUR TURN: filter semantics`, which is the signal that the wiring was complete
and the resolver was reaching the decision spots.

### The four decisions

* **`numericValue`.** FLOAT and INT only — a BOOL stored as `"1"` would `float()` happily, and then a
  truth value is served as a measurement. `ValueError` only, not `Exception`, because `value` is
  typed `str` all the way down and a `TypeError` there is *our* bug. A parse failure logs at DEBUG:
  a censored `"<40"` under a numeric metric is a data-quality signal, not an alarm.
* **The `metric: null` fallback** passes straight through, unlogged. On the real corpus every key
  resolves, so a miss means a new export column, and the seeder's `--dry-run` is the tool for that —
  not a resolver firing 1132 times a request.
* **`module` filters on the key's prefix**, not the catalog's module. They agree for every catalogued
  key and differ only for an uncatalogued one, which has no metric to match: matching the key keeps
  it findable. The client typed a string it can see in `key`.
* **`chain: null` means no filter**, two-valued. `strawberry.UNSET` would allow an explicit `null` to
  mean "unsuffixed entries only"; nothing asks for it, and a client can read `chain == null` off the
  response.

### The bug that passed alone and failed in the suite

The DEBUG log needed a test, and `caplog` reported no records. The helper worked when probed
directly. The test passed when run alone. It failed in the full suite.

`migrations/env.py` calls `fileConfig(config.config_file_name)`, and **`disable_existing_loggers`
defaults to `True`** — it switches off every logger that exists when it runs. `tests/conftest.py`
runs the migrations *in-process*, after the app has been imported. So the first logger this project
ever added went silent for the rest of the pytest session, along with any `app.*` logger anyone
added later.

The server never hit it, because docker-compose runs Alembic as its own process. One keyword fixed
it. It belongs in the same family as Act I's harness and Act II's `get_args`: **a check that
silently covers less than it claims**, discovered only because something that should have been true
was not.

---

## Act VI — stage 4, and a premise that was simply wrong

**`81d1866`, `4df1a3f`**

Three small fields: `interfaceKind`, `target`, `artifacts`. Two of them went as planned.

`interfaceKind` is non-null, so a candidate missing from the view cannot resolve to `null` without
nulling the entire response — the stage was written believing it was only the `Candidate`, and the
review corrected that (see the coda). It raises `CANDIDATE_NOT_IN_SUMMARY` from a helper testable
with an empty map. Notably the `scores` resolver does **not** use that helper — a candidate absent from the
view can still resolve every non-INTERFACE heading, and `metric_for` raises only for the ones it
cannot. `target` is one join per candidate that asks, rather than reusing
`Experiment.select_statement()` and its three eager loads, which is how `candidates { experiment
{ name } }` came to measure at 58 queries rather than 15.

And then `artifacts`. The proposal said:

> Nothing in the repo writes `artifacts` rows, and the 11 predicted structures live on disk under
> `experiment_results/`, found by `demo.py` with a filename glob. So the table is empty and the field
> would ship `[]` for every candidate, exactly like `benchmarkResults`.

The owner decided on that basis: *"Ship artifacts empty for now, we'll do the loader later."* The
field was built, documented as always empty, and tested with a row inserted inside a rolled-back
transaction because the fixture had none.

It was wrong. `scripts/seed_corpus_context.py` — which is named in CLAUDE.md's own rebuild
sequence, in the same sentence as the corpus loader — registers every `<candidate id>_<tool>.pdb` as an artifact row, with
the producing tool as `kind` and the repo-relative path as `uri`. **Eleven rows, already in the
database.** The grep that missed it looked for `Artifact(`; the seeder inserts with raw SQL.

It was caught by running the finished resolvers against the real corpus, which was supposed to be a
victory lap. No code changed — the resolver was correct either way, which is the only reason this is
an anecdote rather than an incident. What changed was prose: three docstrings, the ORM's `kind`
comment (which claimed `"structure" / "sequence"` and was *also* wrong, since the seeder writes
`boltz2` and `rfantibody`), the tests' framing, and the plan, where it is recorded as a false premise
rather than quietly corrected.

Two things worth keeping from it:

1. **A grep for a constructor does not find a writer.** Raw SQL, `text()`, bulk inserts and
   `INSERT ... SELECT` all write rows without ever naming the ORM class.
2. **The fixture could not have caught this and the corpus did.** The fixture is a specification and
   is right to seed no artifacts. Only the real database knew.

---

## Act VII — the one write

**`9817fbe`**

`Mutation.annotateCandidate` is the only write in the API, and the interesting part is not the
resolver. It is that `TaskSafeSession` had already written down what to do:

> Mutations will want `add`, `flush` and `commit`; each gets its own guarded method when it is
> needed.

That sentence is from PR #14, four weeks earlier, written by a class whose entire purpose is that
nothing outside it can reach the session's lock. So stage 5's first question — where does a commit
go? — was already answered, by a design that had made the wrong answer unspellable before anyone
asked.
`commit()` takes the lock, commits, releases. `Context.commit()` is a one-line door. The mutation
reads through `execute_statement` and writes through `commit` and can reach nothing else, because
there is nothing else to reach.

Two decisions of its own:

* **The empty string clears.** `annotation` is `String!` and the column is nullable, so the API needs
  a rule. A nullable argument was rejected for a reason worth remembering: a nullable argument with
  no default is also *omittable*, so `annotateCandidate(id: "x")` would clear silently.
* **An unknown-but-well-formed id is `NOT_FOUND`**, kept distinct from `_as_uuid`'s `BAD_USER_INPUT`.
  The return type is non-null, so `null` was never available for either.

The lock is tested by holding it from outside and watching `commit()` queue behind it — the shape
`TestTheLocksAreSeparate` established, and timeout-bounded for its reason: a deadlock hangs a test
rather than failing it, and a hung suite reads as CI being slow.

---

## Coda — what the review found

A review pass over the finished PR, at high effort, found five issues and none in the core logic:
the two-tier lookup, the three locks, the collision guard and the mutation's commit path all held.
Each finding was reproduced before it was reported, with a toy schema where no database was needed.

| finding | outcome |
|---|---|
| `numericValue` returns NaN or infinity, which GraphQL's Float cannot serialize, so the entry comes back as a masked "Internal server error." | **fixed** — `math.isfinite`, logged at DEBUG like any other value that will not serve as a number |
| `Query.metrics` and `Module.metrics` come back in heap order, which the idempotent seeder reshuffles on every re-run | **fixed** — the catalog query orders on the metric's full identity |
| The `Artifact` docstring cited an ORM comment the correction commit had already changed | **fixed** |
| One candidate missing from the view nulls the entire `candidates` response, not "the whole Candidate" as three documents say | **prose corrected**, in the code, the spec, the plan and this diary; the behaviour is deferred to issue #17 |
| A NUL character in an annotation is rejected by Postgres at commit and surfaces as a masked internal error | **fixed** — confirmed read-only against the dev database (`CharacterNotInRepertoireError`), then refused as `BAD_USER_INPUT` before the session is touched |

The blast-radius finding led somewhere larger. Asked what could make the trigger reachable, the
answer was isolation: the request runs at READ COMMITTED, so every statement takes a fresh snapshot,
and the context module's claim that one session keeps out "rows that never coexisted" was never
true. One session narrows the window; it does not close it. The prose now says so, and the decision
— whether each request should run at REPEATABLE READ, and what that costs the one mutation — is
issue #17.

The ordering fix is the one worth a sentence. `scores` had been sorted from the start, with a
docstring explaining exactly why a list in Postgres's order is a flaky list. The two lists beside it
were not, and nobody noticed because a static table returns rows in insertion order until something
updates one. The test does one UPDATE and asks again; it went red with the ORDER BY removed.

## What the branch actually taught

**One failure mode, five times.** A mutation harness reading stdout while the error went to stderr.
A `-k` expression selecting two tests instead of one. `get_args` on a promoted NamedTuple returning
`()`, so a parametrized test ran zero cases and reported success. Four error-masking tests exercising
an object that cannot occur. Alembic silencing the logger a test was watching. Every one is **a check
that passes while covering less than it claims**, and not one announced itself. The repo's existing
rule — derive a constant rather than hand-listing it, and give the derivation its own assertion —
now has a sibling: *a probe must verify itself before you trust what it reports.*

**The end-to-end test was right where the unit tests were fictional.** Act III is the clearest case,
and it argues against a reflex this project has, which is to push tests downward toward pure
functions. Pure-function tests are cheap and fast and they encode your *beliefs* about the objects
involved. The end-to-end test encoded the objects.

**Running against real data is not a victory lap.** It found the artifacts premise after five stages,
three reviews and 230 green tests. The fixture is a specification; the corpus is a witness, and they
answer different questions.

**Copy-paste was a reasonable use of time.** Three stages were typed in by hand from the chat rather
than written to disk. It is slower per line and it caught things a diff review would not have: an
`if/elif` chain that wanted to be a table, a lambda with a `noqa` that wanted to be a `def`, a
generator expression standing in for a find. The owner's framing was learning; the effect was style
control.

## Still open

* **`Metric.transformOf` and `Recipe.modules`** are the last two things in the committed spec that
  the code does not serve. Both are deliberate, both are documented, and `transformOf` has a test
  asserting its absence.
* **`artifacts.kind` is a `String(32)` holding a closed set of tool names.** That is the
  type-safety plan's enum-plus-migration shape, and Act VI turned it from a hypothetical into a live
  question.
* **`app/routers/demo.py` still globs the filesystem for structures**, while the database has had
  them registered all along. The seeder's docstring says registering them is "what lets the demo ask
  the database instead of the filesystem", and the demo has not been changed to do it.
* **Selection-set-driven query planning.** The N+1s are measured and documented rather than fixed.
  Building loader options from `info.selected_fields` is real machinery and should be added
  deliberately.
* **The `NO_CHAINS_RECORDED` arm is now reachable *and* reached** — by a test, for the first time.
  Nothing in the corpus hits it, which is still the right situation.

## What this chapter was for

```graphql
{ candidates { sequenceId interfaceKind
    scores(module: "boltz2") { key value metric { displayName } } } }
```

Three display names. One key. Chosen by what each candidate folded. Measured against the nine loaded
experiments on 2026-09-19: 1132 score entries, 200 distinct keys, 0 unresolved, 995 numeric values,
0 unparseable, 4 complex / 6 pairing / 4 single-chain.

The 2026-07-31 finding, served over HTTP.
