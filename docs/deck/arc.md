# The deck's story arc

The arc review comes **before** sentence-level edits. Work that starts from the details tidies
slides that might move, merge or go. Started 2026-11-12. Wording decisions still go in
`README.md` here; this file holds who the deck is for and the order it tells its story in.

## Decided

**Audience, primary: technical hiring people, ideally in biopharma.** The goal is a role somewhere
like Merck. The deck is a novelty that shows a backend engineer who took the science seriously.
**Secondary: general tech-stack hiring**, as a demonstration of what one person can build with AI
helping.

**Read alone, not presented.** Nobody narrates it, and there are no speaker notes to lean on.

### What those two answers imply

- **Overclaims cost the most with the primary reader.** A biopharma reader spots "binding" standing
  in for ipTM at once. The precision of the caveats is the selling point to that reader, so a wrong
  one undoes the whole pitch. The slide 7 title was fixed first for that reason (2026-11-14).
- **The caveats are a feature, but only if they are gathered.** Precision about what a number is
  *not* is what that reader is hiring for. Nine slides ending on a hedge reads as nervousness.
  The same hedges collected in one place reads as rigor. (Row 6 in the handoff.)
- **Read alone means the titles are the skim path.** A hiring reader with two minutes reads titles
  and the bold numbers. Reading the titles alone should tell the story; check every title change
  against that.
- **Read alone means the hook has to be on the page early.** Nobody is there to say "stick with me,
  it gets good on slide 8."
- **Two audiences, one deck.** The biopharma reader skips *terms*; the general tech reader needs it.
  That's an argument for keeping the reference slides but moving them out of the critical path,
  not for cutting them.
- **For the secondary audience, the AI split is part of the story.** Right now it appears once, on
  the cover ("How the work split"). Whether it needs to appear again is not decided.

**Thesis plus method, with the method named up front (2026-11-14).** The deck has a second
thread, *measure, don't assert* (slides 8, 14, 16, 17, 20 and *close*'s point 03). It gets
introduced early, as the deck's "how", so later slides pay off a promise instead of arriving cold.
Where it goes and how it's worded are still open. The cover's "How the work split" paragraph is the
natural home, and it would serve the secondary audience too: building with AI is only impressive
if the verification shows. The thesis stays the headline.

## Open, owner to answer

1. **Is a cold open fair game?** For example, the inverted sort ("sorting by this column puts those
   five above every real complex", now a footer on slide 8) as slide 2, with the setup after it.

## Observations from the first read (2026-11-12), not yet acted on

- **The hook is a footer.** The inverted ranking is the most striking fact in the deck. It is set at
  24px under the pictures on slide 8, after seven slides of setup. Two of those seven label
  themselves skippable, and the 0.09 headline is told twice (5 and 7).
- **The thesis stops at slide 14.** Slides 2–14 pay off "flexibility breeds complexity". Slides 15–19
  read as a separate story about rigor. A possible reframe: the same trade, one layer up. GraphQL
  lets the client pick the tree, which is why *locks* exists. The schema is code-first, so any
  annotation can change the contract, which is why *snapshot* exists. A hypothesis, not checked
  with the owner.
- ***predicted-property* (12) interrupts the build-up**, between *lookup* and the *query* payoff.
  After *query* it would read as "where the design runs out". Moving it means rewording its footer,
  which points at "the slide before this one".
- ***close* does not answer the cover.** Its three points are about method. A direct answer would
  call back to the inverted sort: the catalog is what makes ranking by ipTM honest.

## Beat sheet, as it stands

| # | slide | what it does in the story now |
|---|---|---|
| 1 | cover | states the thesis |
| 2 | no-api | the constraint; plants the *flexibility* half |
| 3–4 | terms, models | reference, both labelled skippable |
| 5–6 | corpus, anatomy | inventory: what came out |
| 7 | humanization | a science result, then "the next slide is why" |
| 8 | finding | **the turn** |
| 9–11 | layers, discriminator, lookup | building the answer |
| 12 | predicted-property | a side trip: the case the design doesn't solve |
| 13 | query | payoff |
| 14 | measured | self-audit |
| 15–19 | stack, checks, witness, locks, snapshot | engineering, told as a second story |
| 20 | open | limits |
| 21 | close | three things I did |
