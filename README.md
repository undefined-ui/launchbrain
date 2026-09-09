# LaunchBrain

A terminal for tracking new token launches on Robinhood Chain, that files what
you find into your second brain.

**Live: [launchbrain.org](https://launchbrain.org/)**
(mirror: [undefined-ui.github.io/launchbrain](https://undefined-ui.github.io/launchbrain/))

Every launchpad has a feed. What none of them have is a way to keep what you
learned. You spot something at 2am, you tell yourself you will remember, and a
week later you cannot recall the ticker, let alone why it looked interesting.

LaunchBrain is a launch feed with notes attached, and a one-key export into an
Obsidian vault.

## What it does

- **Live feed** of the whole chain: price, 1h and 24h change, volume,
  liquidity, FDV and buyers update in place while you watch, and a token
  launched two minutes ago appears on its own. Holders and graduation progress
  against the $50,000 target come from the pools.trade snapshot.
- **Crowd Launch auctions** with clearing price, floor, amount raised and
  bidder count.
- **Safety signals** straight from the source: spam verdicts, contract
  verification, reserve and volume flags. Spam is hidden by default.
- **Real charts**: the open token's sparkline is hourly closes from
  GeckoTerminal, with the source named under the chart.
- **Ask**: a chat on every token page. A free model reads the token's live
  numbers and answers in a sentence or three — liquidity depth, turnover,
  risk signals. It never tells you to buy and never predicts price. Keyless
  provider by default, or your own free groq / openrouter key, stored only in
  your browser.
- **Watchlist and notes**, kept in your browser, nowhere else.
- **Export to markdown**, one page per token, with frontmatter and wikilinks
  ready to drop into a vault.

Keyboard: `/` search, `j` `k` move, `w` watch, `e` export.

## The second brain part

The export is the point. Each page comes out as a source note with the snapshot
figures, your own observations, and links back to the token, the pool and the
creator. Drop it into `raw/`, and an agent files it the same way it files
anything else you read.

That workflow is the subject of a separate guide:
[second-brain-os](https://github.com/undefined-ui/second-brain-os).

## Data

Two layers, both free, both keyless.

**Live, in your browser.** The page polls GeckoTerminal and DexScreener
directly — both allow cross-origin requests — on a rolling schedule: new pools
every few seconds, trending and top pools on slower cycles, the token you have
open and your watchlist more often than the rest. Rows update in place, new
launches slide in on their own, and nothing ever reloads. Each visitor spends
their own rate-limit quota; when a feed throttles, the header says so and the
engine backs off instead of breaking.

**Snapshot, committed to the repo.** The pools.trade API refuses cross-origin
requests, so the fields only it knows — holder counts, graduation progress,
spam verdicts — come from a harvest that a GitHub Action runs every ten minutes
and commits. That file is also the cold-start baseline: the first frame paints
from it instantly, works offline, and shows its age.

History accumulates in both places: the harvester keeps a snapshot trail per
token in the repo, and your browser keeps its own finer-grained trail locally
while the page is open, surviving closed tabs. A token that falls out of every
feed stays in the table with the time it was first seen.

The upstream API caps every endpoint at 100 rows and accepts only two sort
orders, so no single call sees the whole chain. The fetcher works around that
twice over. It reads trending, volume, the auction list and a search sweep
across single characters, then merges the results by token address. And it
keeps what it has seen: a token that falls out of pools.trade's top 100 stays
in ours, with the time we first saw it and a trail of price, holder and FDV
snapshots.

That history is the part the source does not keep. After a day of running, the
terminal can answer questions the launchpad itself cannot.

```bash
python3 scripts/fetch.py     # reseed the baseline by hand, takes about a minute
```

Nobody needs to run that to use the terminal — the Action keeps the baseline
fresh and the browser takes over live from there. It exists for cold starts and
for accumulating long-run history. `data/launches.json` is the working set the
page loads; `data/archive.json` holds every older token so the page stays fast
on mobile.

Open `index.html` from anywhere, including straight from the filesystem: the
fetcher writes the data twice, as JSON for the deployed site and as a JS file
that a double-clicked page can load. The live feeds work from `file://` too.

The upstream API is undocumented and can change without notice. If the feed
goes stale, the fetch script is the thing to look at first.

## Honest limits

Market figures are as live as the aggregators serving them and as accurate as
the source; holders, graduation and safety verdicts are a snapshot whose age is
always on screen. Volume on this chain is easy to overstate, since one swap can
route through several legs. Nothing here is a recommendation, and a low spam
score is not a safety guarantee.

MIT.
