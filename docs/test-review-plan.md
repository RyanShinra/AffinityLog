# The test-review pass — a collecting doc

> **Status: not started. This is a list, not a plan.** Items land here as they are noticed during
> other work, so that a reviewer of the eventual PR has the reasoning rather than just the diff.
> Nothing here is urgent; every item is a test that passes today.

The pass has been referenced since PR #13 (`docs/pr-13-diary.md`, Acts VI and the epilogue) without
ever having a place to accumulate. This is that place.

---

## The theme, added 2026-09-05

**Where a test reaches into an implementation, ask whether dependency injection would let it reach
through the front door instead.**

Python's "we're all adults here" makes the reach *possible* — `monkeypatch`, private attributes,
module-level rebinding — and that availability is exactly why it needs a deliberate policy rather
than a case-by-case shrug. CLAUDE.md already carries the narrow rule (patch where the value is
READ, then prove it by reverting the fix); this pass is the broader question of whether the patch
should exist at all.

The failure mode is specific and this project has hit it twice, both recorded in
`tests/test_fixture_guards.py`: a patch aimed at the wrong place does not error, it **passes**, and
it passes *against the bug it was written to catch*. A dependency injected as a parameter cannot
fail that way, because there is no second place for the value to come from.

Not a license to rewrite every test. `monkeypatch` is sometimes genuinely the only way — process
start-up state, environment read at import, third-party singletons — and the goal is to be able to
say which of those applies, per site, rather than to reach for it first.

### The inventory as of 2026-09-05

| file | `monkeypatch` uses | first question to ask |
|---|---|---|
| `tests/test_fixture_guards.py` | 13 | These are *about* process-start state, so most are probably correct — but this file also contains the two documented cases of a patch that asserted nothing. It is the one to read first, not the one to change first. |
| `tests/test_demo_router.py` | 6 | The router reads its dependencies through FastAPI. `app.dependency_overrides` is the front door and may replace most of these. |
| `tests/test_import_graph.py` | 1 | Deliberate; the file already runs subprocesses where the state genuinely is process-start. Likely a no-change. |

Five further sites reach a private attribute directly (`context._catalog_lock`,
`context._session._lock`). Those are in `TestTheLocksAreSeparate`, where the *point* is to assert
object identity between two privates — a public accessor would be inventing API surface for a test.
Probably correct as they stand; listed so the decision is recorded rather than re-derived.

---

## Individual items

### 1. `tests/test_importer.py` uses an unbound `AsyncSessionLocal`

`test_loads_one_experiment_with_chains_and_scores` opens `AsyncSessionLocal()` directly. That works
only because `load_csv` never awaits the session — it calls `session.add()`, which populates the
identity map without connecting. The module docstring's "WHY NO DATABASE" section explains it and
says it was verified with the container stopped.

It is correct today and fragile by construction: the day `load_csv` grows a `flush()`, this fails
with `UnboundExecutionError` from a test whose docstring says no database is required. Injecting the
session (as a fixture parameter) would make the dependency explicit and the failure impossible.

### 2. The deliberately unmanaged session in `test_two_root_fields_do_not_break_a_virgin_session`

Its docstring already states the cost plainly: the session sits outside the fixture's transaction,
so nothing rolls it back, and it must stay read-only forever. The constraint is real — the bug only
reproduces on a session that has not checked out a connection, and opening a transaction to roll
back would warm it — so this may simply be irreducible. Worth confirming that rather than assuming
it, since "we checked and it cannot be fixed" and "nobody looked" read identically in a diff.

### 3. `conftest.py`'s `AsyncSessionLocal` rebinding

The fixture unbinds `AsyncSessionLocal` by default and rebinds it to the test's connection for the
duration. That is module-level rebinding of a global — the same category this pass is about — but it
is what stops any test reaching the dev corpus, and it makes a seeder's `commit()` a SAVEPOINT
release inside the rollback. Almost certainly right. Listed because a reviewer will ask.

---

## Closed

- **`_identity_for` was a copy of the two-tier lookup living in a test file**, so the ipTM tests
  asserted the finding against the copy rather than against anything in `app/`. Closed in PR #16
  (`f786597` and the commit that follows it): the lookup moved to `MetricCatalog.metric_for` and the
  three tests now call it. This was the item PR #13's diary flagged as belonging to this pass; it
  came due earlier because the ScoreEntry chapter needed the lookup to exist anyway.
