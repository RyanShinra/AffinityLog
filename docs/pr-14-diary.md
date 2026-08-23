# PR #14 diary — one comment, and the six questions that deleted it

> **Status: historical record, written 2026-08-21 as the branch was opened.** A narrative of a
> single cleanup item that was supposed to take twenty minutes. Kept because the interesting part
> is not what shipped — it is the sequence of questions that turned a tripwire into a design
> change, and because every one of those questions came from the owner rather than from the code.

**Span:** 2026-08-21 → 2026-08-23 · **branch:** `spring-cleaning-in-summer` · 13 commits ·
19 files, +1299 / −159 · 93 → 120 tests · all 15 cleanup items closed

---

## What it was supposed to be

Item 15 of fifteen, from `pr-13-diary.md`'s Act VI work list:

> nothing *enforces* that code holding `_session_lock` avoids `self.execute()` — a comment is the
> only guard against a silent deadlock when `interface_kinds()` is written

The plan was a tripwire. Record which task holds the lock; if `execute()` is entered by that same
task, raise instead of parking. Fifteen lines, an afternoon, move on to items 1, 2 and 6.

## What it became

`TaskSafeSession` — a class that owns the session and its lock together, so the hazard the comment
warned about cannot be written down. The comment is gone. So is the special case it explained, the
CLAUDE.md corollary about `asyncio.Lock` non-reentrancy, and the convention-grep that was the only
check on either.

Net effect on the codebase: one new class, one renamed method, and **three deletions of things
that existed only to compensate for the design**.

---

## Act I — the item, as written, was a tripwire on a smell

The original `Context` held one `asyncio.Lock` and used it for two jobs:

1. serialise access to the session — one statement at a time, in `execute()`
2. serialise construction of the catalog memo — check, load, store, atomically, in `catalog()`

Job 2 holds the lock across a call to `_load_catalog()`, seventy lines of it. Anything that method
does, it does with the lock held — so the statement it issues had to bypass the wrapper and call
`session.execute` directly, under a comment explaining why. That comment was the entire defence,
and `interface_kinds()` was about to have the same shape.

The proposed fixes, in the order they were proposed and rejected:

| | approach | why it was not enough |
|---|---|---|
| A | rename the inner door `_execute_locked` | names the special case; does not remove it |
| B | track the owning task, raise on re-entry | a tripwire on a design smell |
| C | write a reentrant lock | redefines a primitive to accommodate one caller |

## Act II — "is it the same lock?"

Six messages went into a question that turned out to be the load-bearing one:

> does our call to `asyncio.Lock()` bring us the same lock that the one inside `sqlalchemy.execute()`
> uses? Otherwise how can the normal call stack deadlock?

The answer required separating two failures that look identical in a stack trace and are opposites
in structure:

```
different task waits on a held lock  ->  blocks, then proceeds   -- correct; the reason the lock exists
the holding task waits on it again   ->  blocks forever          -- deadlock
```

A bare mutex cannot tell these apart, because it stores no owner. Demonstrated with no database and
no SQLAlchemy at all — a twenty-line toy with one task and one lock hangs on its own.

Two things measured along the way, both of which ended up in the shipped docstrings:

- **SQLAlchemy's own lock does not cover us.** `_execute_mutex` hangs off the *connection adapter*
  (`dialects/postgresql/asyncpg.py`) and guards the wire protocol. The `AsyncSession` above it is
  documented, in its own docstring, as "**not safe for use in concurrent tasks**". The virgin-session
  measurement from PR #13 — 3 of 4 concurrent executes raise — happens *above* the layer that mutex
  protects, because there is no connection yet for it to guard.
- **The warm case is the dangerous one.** Warm session, 4 concurrent executes, 0 raise. That is not
  safety; it is the connection mutex serialising the wire while session-level state is trampled
  quietly.

The framing that finally landed was C. `asyncio.Lock` is `PTHREAD_MUTEX_NORMAL` — no owner field,
so relocking self-deadlocks. C has had this exact problem since 1995 and ships three answers to it:

| pthread mutex type | relocking from the owning thread | our option |
|---|---|---|
| `PTHREAD_MUTEX_NORMAL` | deadlocks | what we had |
| `PTHREAD_MUTEX_ERRORCHECK` | returns `EDEADLK` | B |
| `PTHREAD_MUTEX_RECURSIVE` | increments a count | C |

## Act III — the critique that changed the answer

> A+B means "well written behavior with exceptions thrown during bad" vs C means "redefine
> fundamental behavior", and the whole thing feels a lot more like an ownership problem. It's kinda
> rowing against the "easy to use, hard to use incorrectly" mantra

That is the whole diary in one paragraph. Every symptom fell out of one conflation — one lock
serving two unrelated invariants — and all three proposed fixes treated the symptom.

**Option D: give each invariant its own lock.** `catalog()` holds `_catalog_lock`; `_load_catalog()`
calls the front door, which takes the *session's* lock, a different object. Nothing to re-enter.

Two things checked before proposing it, rather than after:

- the memo double-check still runs under a lock, so PR #13's measured 14-callers-14-catalogs
  failure stays fixed
- `_load_catalog` issues exactly one statement, so nothing depended on holding the session lock
  across a multi-statement read — no consistency guarantee is being given up

## Act IV — and then the ownership argument was taken one step further

> maybe we should have our own `sqlwrapper.execute()` so that the "only one door" is enforced by a
> class and not just our member functions on context agreeing not to call `sqlalchemy.execute()`
> directly

This is what makes the rule disappear rather than merely hold. If the lock lives inside the object
that owns the session, `catalog()` **cannot** acquire it — not "must not", *cannot*. `_load_catalog()`
calls `execute_statement()` because it is the only thing there is to call.

The cost was measured before it was accepted, and it was zero:

```
call sites in app/graphql/ using a raw session   ->  2, both inside context.py
tests reaching into ctx.session                  ->  0
Context(session=<AsyncSession>) construction     ->  13 sites, all unchanged
```

Every resolver already went through the front door. The wrapper needed exactly one method.

The owner's own summary of the trade, which is the reason `self._session` is private rather than a
public `TaskSafeSession`:

> well named functions protecting the single instance of the `_session` seems preferable to having
> the instance of it exposed and pass-around-able just to keep the `context.session.execute()`
> legibility

## Act V — naming, which took as long as the design

`GuardedSession` (guarded against *what*? in a web app that reads as auth), `SessionGate`, and
`SerialSession` — rejected outright, because `SERIAL` is a Postgres column type and "serialize"
means JSON three files away. `TaskSafeSession` won for answering SQLAlchemy's own sentence in
SQLAlchemy's own vocabulary.

Then the method. `Context.execute()` became `execute_statement()`, on the owner's argument that a
`Context` could plausibly execute other things — "a HTTP request or the like? A jump to warp?" The
supporting evidence was already in the repo: `tests/test_context_catalog.py:217` calls
`schema.execute(...)`, which is **Strawberry's** `Schema.execute()` and runs a GraphQL *document*.
So a bare `execute` would have meant SQL in six resolver call sites and GraphQL in the test suite.

(The first version of this paragraph, and of the docstring it describes, claimed both spellings
appeared "twelve lines apart" in one file. They do not — that was invented precision, caught by
grepping the claim before committing it. Recorded here because it is the same failure mode PR #13's
second review pass found five times: a documentation claim that was false the moment it was
written.)

`TaskSafeSession.execute()` kept the short name deliberately — a wrapper should present the same
verb as the thing it wraps, and inside a class named `…Session` there is nothing to disambiguate
from. It is `Context` that is the grab-bag carrying a `request`, a `response` and
`background_tasks`.

On the Pythonista objection to long names, the owner's position is recorded for posterity:

> we're now programming on displays over 2000 pixels wide, preferably "4000", not 1024

---

## What shipped

```
TaskSafeSession            owns the AsyncSession + the lock; app/database.py
  .execute(stmt)           the only door to the session; critical section is one statement

Context
  self._session            a TaskSafeSession, private
  self._catalog_lock       guards the memo only — a different object entirely
  .execute_statement()     delegates; what all six resolver call sites use
  .catalog()               holds _catalog_lock; _load_catalog() calls the front door
```

Deliberately **not** a `__getattr__`-forwarding proxy. Every method worth exposing gets added on
purpose, holding the lock. A forwarding proxy would re-expose every unguarded method on
`AsyncSession` and put us back where we started. Mutations will want `add`, `flush` and `commit`;
each gets its own guarded method when it is needed.

### The test that makes the design load-bearing

```python
async def test_collapsing_them_into_one_lock_deadlocks(self, seeded_catalog):
    context = Context(session=seeded_catalog)
    context._catalog_lock = context._session._lock      # the pre-cleanup design, in one line
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(context.catalog(), timeout=1.0)
```

It passes, which means the merged-lock design really does hang, and the separation is load-bearing
rather than incidental. Without it nothing in the suite would notice the two locks being quietly
merged back — **every other test passes under both designs**.

Timeout-bounded on purpose: a deadlock does not fail a test, it *hangs* one, and a hung suite reads
as slow CI rather than as a bug.

This is PR #13's rule applied to a case where there was no bug to reproduce. The failure could not
be reproduced from the existing code because the code was correct — so the test induces the failure
on purpose and asserts it, which is the same discipline pointed at a hazard instead of a defect.

---

## What this branch actually taught

**A comment warning you not to do something is a design telling on itself.** The line
`# self.session.execute, not self.execute: the caller already holds _session_lock` was accurate,
well-reasoned, and load-bearing. It was also the smell. When the fix was made structural, the
comment did not need rewriting — it needed deleting, along with the CLAUDE.md corollary and the
`grep -n 'session.execute' app/graphql/` that was the only enforcement either ever had.

**The ladder is worth remembering as a ladder**, because the first four rungs all look like fixes:

```
comment  ->  exception  ->  rename  ->  split the locks  ->  give the lock to the owner of the resource
```

Rungs one through three make the mistake *survivable*. Rung four makes it *impossible in this
class*. Rung five makes it *unspellable anywhere*.

**And the questions came from outside the code.** Nothing in the diff was found by reading the
diff. "Is it the same lock?" and "isn't this an ownership problem?" are not code review comments —
they are someone refusing to accept an explanation that did not sit right, five times in a row.
PR #13's lesson was *reproduce before fixing*. This one's is narrower and harder: **when the
explanation needs six messages, the design is the thing that is wrong, not the listener.**

---

## Epilogue — the other fourteen

Item 15 was written up first because its story was the interesting one. The remaining fourteen were
expected to be a tail of one-liners. Three of them were not.

### The ones that changed shape

**Item 1 — the satisfiability check's own false negative.** `DECOMPOSABLE_VARIANT_KINDS` flattened
the variant rules to a bare set of kind names, so it answered *"is PARAMETER decomposable?"* when
the answerable question is *"is it decomposable **for this heading**?"* The rules are keyed by module
and their regex pins the column, so PARAMETER is recoverable for exactly one heading in the corpus:

```
Heading('evoprotgrad', 'entropy', ('PARAMETER',), 0).problems()  ->  CLEAN
```

It could only ask the unanswerable form because the regex was the source of truth and the data was
trapped inside it as capture groups. So the table was inverted — data primary, regex derived — and
the check became a lookup. Drift became unrepresentable rather than tested for, which mattered
because *the bug being fixed was a drift bug*.

**Item 2 — a malformed row that silenced a check.** A metrics row with a NULL `variant_kind` and a
non-NULL `variant` is unreachable, but it did not merely sit there: the query counted it as a BARE
row, and `bare_rows` is the guard on the unsatisfiable-axis branch. One such row turned a real
violation clean for its whole heading.

```
Heading('fastdpe','SFvCSP',('TRANSFORM',), bare_rows=0) -> "nothing can supply..."
Heading('fastdpe','SFvCSP',('TRANSFORM',), bare_rows=1) -> CLEAN
```

The review proposed splitting the count and adding a Python branch. But unlike the functional
dependency that module exists for, this invariant *is* expressible — `CHECK ((variant_kind IS NULL)
= (variant IS NULL))` — so migration 008 enforces it and the Python branch was never written. Same
move as item 15, one layer further down: the row cannot be created, so nothing needs to detect it.

**Item 6 — coverage, which item 1 made possible.** Nothing checked whether a heading qualified along
a *resolvable* axis actually covered it. Tier two builds
`(module, column_key, 'INTERFACE', <the candidate's kind>)`, so a heading seeded with two of three
scoreable kinds resolves to nothing for exactly the candidates carrying the third — a partial
failure, which is harder to notice than a total one.

The PARAMETER half of that check **could not have been written before item 1**. The two turned out
to be one question — *this heading is qualified along X; is every value of X present?* — asked of
two axes, with the enumeration coming from `InterfaceKind.scoreable()` or `declared_variants_for()`.

The domain call looked open and was not: three separate places already said the catalog carries
three interface rows and not four. Measured what the naive version would have cost:

```
demanding all four kinds -> flags boltz2.protein_iptm, .iptm and .complex_ipde
                            i.e. every interface-dependent heading in the live corpus
```

Which is the failure `invariants.py` already calls worse than no check at all.

### The tail, which was mostly a tail

| # | what it was |
|---|---|
| 3 + 13 | the Docker probe performed a **registry login**, so an unreachable registry read as an absent daemon — and in CI that is `pytest.fail` on a healthy runner. Fixed by keeping testcontainers' host *resolution* and dropping its client, which necessarily closed 13 too |
| 4 | the fixture-leak regression test was two tests, the writer asserting **nothing**. `-k` or `--lf` ran the reader alone against an empty schema and passed. Now one test, reading back from a second connection |
| 5 | `os.environ.get("CI")` is a presence test, so `CI=false` meant yes |
| 7 | `--dry-run` caught `OSError` only; an unmigrated database raised `ProgrammingError` and crashed |
| 8,9,10,11,12,14 | documentation that was false when written, or had rotted since |

### Two things the tail taught anyway

**A test can pass against the bug it was written for, and look like coverage.** Item 3's first pair
did. `monkeypatch.setenv("DOCKER_AUTH_CONFIG", ...)` does nothing, because the library reads that
variable through a dataclass `default_factory` evaluated once at import; and the other test patched
a *conditional* path that never fires on this machine or in CI. Both green, both vacuous. That
produced a CLAUDE.md entry: monkeypatch is a smell, not a default — patch where the value is
**read**, not where it is set, and prove it by reverting the fix.

Only the revert step made either visible. Every fix in this branch was verified that way, and it
caught something roughly one time in four.

**Enumerating a hierarchy is a guess that fails silently.** Item 7's obvious widening —
`(OSError, SQLAlchemyError)` — still missed two of the five ways a database can be unusable, because
asyncpg raises its own exceptions at connect time before SQLAlchemy has anything to wrap:

```
connection refused   ConnectionRefusedError    OSError
no such host         gaierror                  OSError
no schema            ProgrammingError          SQLAlchemyError
wrong password       InvalidPasswordError      asyncpg's own
no such database     InvalidCatalogNameError   asyncpg's own
```

Three unrelated hierarchies, and no reason to believe five is the whole list. So: catch everything
and *report precisely* rather than assert a cause — which pays down the real cost of a broad
`except` instead of ignoring it.

---

## The branch, finished

**13 commits · 19 files · +1299 / −159 · 93 → 120 tests · 15/15 items**

Four of the fifteen produced real changes to the system — a new class, an inverted rules table, a
schema constraint, a fourth invariant branch. Six were documentation that had rotted. The rest sat
in between.

The pattern across the substantial ones is the same one item 15 found, and it held every time:

> **comment → exception → rename → split the locks → give the lock to the owner of the resource**

Items 2 and 6 walked the same ladder in their own terms. Item 2 was offered as a Python check and
became a `CHECK` constraint. Item 6 was offered as a hardcoded list and became an enumeration from
the enum that already carried the reasoning. In every case the review's proposed fix was at the
wrong altitude rather than wrong — which is worth knowing about review findings generally: they are
reliable about *where something is broken* and much less reliable about *how deep the fix goes*.

And one sequencing lesson: item 1 had to precede item 6, not for tidiness but because it made item 6
expressible. A work list ordered by severity is not necessarily ordered by dependency, and nothing in
the list said so.
