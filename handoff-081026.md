He's mid-write — the `catalog()` body is still the `YOUR TURN` block. Let me check two things before I commit that, because both could bite you.

Both concerns resolved by your own past config, with one wrinkle:

- `unfixable = ["F401"]` means ruff **won't** delete the three imports the scaffold needs — good, that would have been a nasty silent loss.
- But ruff still *reports* them, so the pre-commit hook will block the commit.
- CI triggers only on `main`/`develop` and PRs to `main`, so pushing `score-entry` won't light up a red build.

Let me read the existing memory before I add to it.

Now I'll update the memory. Updating the existing chapter file rather than adding a duplicate.

Now the temporary `noqa` so the pre-commit hook will let a WIP scaffold through:

The file changed on disk while I was working — let me look before committing anything.

Ruff's isort wants the comment out of the middle of the import block. Let me let the same hooks that run on commit fix it, then check the result.

67 passing. Committing and pushing now.

Both done. Branch pushed: [score-entry](https://github.com/RyanShinra/AffinityLog/tree/score-entry), commit `dd2e376`.

## One thing you should know about task 1

**The memory files don't travel.** They live in `C:\Users\ryano\.claude\projects\F--Source-Code-AffinityLog\memory\` — that's PC-local, outside the repo, and the Mac's Claude will never see them. I updated them anyway (they're right for when you come back to this machine), but they can't be the handoff mechanism you asked for.

So the actual handoff went into the **commit message**, which is what `CLAUDE.md` already tells a cold session to trust: *"everything needed lives in the repo."* `git log -1` on the Mac gets the whole thing — what's scaffolded, the two open decisions, the five measured DB facts, the remaining order, and an explicit "do NOT fill the body in."

## What I committed

The scaffold only — `MetricIdentity`, `MetricCatalog`, the memo slot, and `catalog()` stopping at the `YOUR TURN` block. Your body is still yours.

Two lint details worth knowing, since both were near-misses:

- Your `unfixable = ["F401"]` setting saved the scaffold. Ruff wanted to *delete* `select`/`selectinload`/`Select` as unused; that config stopped it. It still *reported* them, which would have blocked the commit, so I added a clearly-marked temporary `# noqa: F401` — flagged in the message for removal.
- mypy's `Missing return statement` on `catalog()` is **left in deliberately**. It's the marker of where you stopped, and papering over it would have hidden that.

CI won't fire — it triggers on `main`/`develop` and PRs to `main` only. 67 tests pass, ruff and black clean.

## Before you start on the Mac

Two things bite on a machine switch:

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]" && .venv/bin/pre-commit install
```

And the one that's easy to miss: **the Mac's Postgres won't have the corpus.** Every number in that commit message — 144 rows, 9 INTERFACE rows, the 16-vs-144 `IN` lists — was measured against the PC's database. On a fresh Mac container you'll need `alembic upgrade head`, then `scripts/load_experiment.py`, then the seed chain (`seed_catalog` → `seed_metric_skeleton` → `seed_corpus_context` → `seed_recipes`). Without that, `catalog()` will correctly return an empty catalog and every score will resolve to no metric — which looks like a bug in your new code and isn't.