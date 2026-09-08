# Task: make the terminal live

Read `CLAUDE.md` first. It has the verified API facts and the rules.

## The problem

Two things are wrong with how the terminal gets its data today.

**It waits for a harvest.** New tokens only appear after `scripts/fetch.py` runs
and writes `data/`. On GitHub Pages that means a cron job, a commit and a page
that is minutes behind. Running the harvester by hand to see a token that
launched five minutes ago is not a product.

**It reloads instead of streaming.** The page polls its own data file every 20
seconds and re-renders. It should behave like DexScreener: rows update in place,
new launches slide in on their own, nothing is ever manually refreshed.

## What to build

A client-side live engine, with the harvested file as a cold-start baseline
rather than the source of truth.

**On load**, paint immediately from `data/launches.json`, so the first frame is
instant and works offline. Show its age honestly.

**Then take over in the browser.** Poll the aggregators directly on a rolling
schedule, merge results into the in-memory table, and never reload the page:

- new pools, so launches appear within seconds of existing
- trending and top pools, for the movers
- the open token, more often than the rest
- the watchlist, more often than the rest of the table

Respect the rate limits from `CLAUDE.md` with a shared queue: one request at a
time, adaptive backoff on 429, and a visible indicator when a feed is throttled
or down. Each user spends their own quota, which is the point.

**Merge, do not replace.** A token seen once stays in the table with its
`first_seen` and its snapshot trail, even when it falls out of every feed. Keep
accumulating `hist` in the browser between polls and persist it locally so the
history survives a closed tab.

**Update in place.** No full re-render on every tick. Rows whose numbers changed
update their own cells and flash briefly. Sort order holds unless the user is at
the top of the list. Scroll position, selection, open chat and typed text are
never disturbed by an update.

**Show new arrivals.** A launch that appears mid-session is marked as new and
counted somewhere visible, so a person watching the screen sees it happen.

## Then remove the manual step

`scripts/fetch.py` should stay, but its job narrows: it seeds the baseline file
and accumulates long-run history for anyone loading the site cold. Nobody should
ever have to run it to use the terminal. Make the workflow run it on a schedule
and keep the committed file small enough that the page loads fast on mobile;
split the archive from the working set if that is what it takes.

## Acceptance

- Open the page, leave it open, and a token launched two minutes ago appears
  without any interaction.
- Prices, volume and liquidity of visible rows move on their own.
- Typing in the chat, scrolling, or reading a token page is never interrupted.
- Throttling degrades gracefully: slower updates and an honest indicator, not a
  broken table or a wall of console errors.
- With the network cut, the page still opens from the committed file and says
  how old it is.
- `python scripts/fetch.py` still works and still writes the same shape.

## Out of scope

Do not add a framework, a build step, a bundler or a package manager. Do not add
a backend, a database or a key. Do not write a chain indexer. Do not change the
visual language.

## After this

Two things are queued behind it, in order:

1. Holder counts and contract verification from Blockscout for tokens the
   launchpad feed does not cover, so the read stops saying "holder count
   unknown".
2. Deployment on our own host, where the harvester can run every 30 seconds and
   push over SSE instead of the browser polling. Design the live engine so that
   swapping the polling layer for a push feed later is a small change.
