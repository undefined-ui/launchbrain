# LaunchBrain

A terminal for tracking token launches on Robinhood Chain that files what you
find into a second brain. Static site, no backend, no API keys.

Owner: @undefinedKi. Companion project to
[second-brain-os](https://github.com/undefined-ui/second-brain-os), a guide to
agent-maintained knowledge bases. The link between them is the export: every
token page comes out as markdown with frontmatter and wikilinks, ready to drop
into an Obsidian vault.

## What exists now

```
index.html          the whole terminal: table, detail panel, live engine, export
scripts/fetch.py    harvester, seeds the cold-start baseline in data/
data/launches.json  working set: recently seen tokens, with snapshot history
data/archive.json   everything else ever seen; the page does not load it
data/auctions.json  pools.trade Crowd Launch auctions
data/*.js           the same payloads as `window.LB_LAUNCHES` / `LB_AUCTIONS`,
                    so the page also works from file:// where fetch() is blocked
.github/workflows/update.yml   cron that runs the harvester and commits data/
```

The page is one file on purpose. No framework, no build step, no dependencies at
runtime. Keep it that way unless there is a reason that survives scrutiny.

## The live engine

The committed baseline is only the first frame. After painting it, the page
polls the two CORS-open aggregators itself and merges into the in-memory table,
never reloading:

- GeckoTerminal `new_pools` every ~15s (launches appear within seconds),
  `trending_pools` and top `pools` pages on slower cycles
- DexScreener for the open token (~12s), the watchlist round-robin, and a
  one-off enrichment (image, socials) for each newly discovered token
- one request in flight at a time; per-feed gap doubles on 429 and decays back
  on success; feed state (ok / throttled / down) is shown in the header
- rows update their own cells and flash; sort order is held unless the user is
  at the top of the list; new arrivals otherwise queue behind a "N new — show"
  chip. Selection, scroll, notes and typed text are never disturbed.
- browser-accumulated history (`hist`, `first_seen`) persists in localStorage
  (`lb.live.v1`) and is merged over the baseline on the next load
- everything below `upsert()` is transport-agnostic: a future SSE push feed
  replaces the polling jobs and calls the same merge path

**pools.trade cannot be polled from the browser.** It returns 403 to any
request carrying an Origin header (verified September 2026). Holders,
graduation, safety verdicts, price series and recent trades therefore come only
from the committed snapshot, and the UI labels them as such. GeckoTerminal and
DexScreener both send `access-control-allow-origin: *` (verified the same day).

**Charts.** The open token's sparkline is real hourly closes from the
GeckoTerminal OHLCV endpoint, fetched on selection and cached five minutes,
with honest fallbacks (pools.trade snapshot series, then local snapshots) and
the source named under the chart.

**Ask.** Each token page has a chat: a model gets the token's JSON and answers
in 1-3 sentences, in the user's language. Provider layer in `AI_PROVIDERS`:
pollinations (keyless, default), openrouter and groq (bring-your-own-key,
stored only in localStorage `lb.ai`). As of September 2026 the pollinations
anonymous tier answers 401 to any non-cached prompt — effectively dead, kept as
the default attempt in case it comes back; the working free path is a free
groq or openrouter key. The system prompt forbids buy/sell recommendations and
price predictions; the anonymous tier also rejects the `system` role and
sampling params, so the bare provider folds everything into one user message.

## Verified facts about the data sources

All checked by hand in September 2026. None of these need an API key.

**GeckoTerminal** — `https://api.geckoterminal.com/api/v2`, network id
`robinhood`. This is the breadth: the whole chain, 40 DEXes, with volume,
liquidity, FDV, price changes and buyer counts per pool.

- `/networks/robinhood/pools?include=base_token,dex&page=N` — 20 rows per page
- `/networks/robinhood/new_pools`, `/networks/robinhood/trending_pools`
- `/networks/robinhood/dexes` — 40 venues, `uniswap-v2/v3/v4-robinhood`,
  `pancakeswap`, `sushiswap`, `curve`, `ramses`, `pons`, `clanker`, `robinswap`,
  `uniswap-pools-trade` and more
- `/networks/robinhood/dexes/{dex}/pools?include=base_token,dex&page=N` — the
  real breadth path: the global `/pools` list is capped, per-dex lists cover
  the chain
- `/networks/robinhood/tokens/{addr}/pools` — every pool for one token
- `/networks/robinhood/pools/{pool}/ohlcv/{minute|hour|day}?aggregate=1&limit=N`
  — real candles, `ohlcv_list` of `[ts,o,h,l,c,vol]` newest first. Works with
  uniswap v4 pool ids and with pools.trade poolIds (verified September 2026).
- Free tier is roughly 30 calls a minute and it enforces it with 429s. The
  harvester has an adaptive limiter; do not remove it.
- Free tier caps EVERY pool listing at page 10 — page 11 answers 401, not an
  empty list. `/dexes` does not paginate at all (400 on page=2). Both verified
  September 2026.
- Some responses intermittently arrive without CORS headers; in the browser
  that surfaces as a fetch error, not a status. Treat it as a transient.

**DexScreener** — `https://api.dexscreener.com`, chainId `robinhood`. Second
opinion on the same pairs, plus token images and socials.

- `/latest/dex/search?q={term}` — up to 30 pairs, used as a letter sweep
- `/token-pairs/v1/robinhood/{tokenAddress}` — every pair for one token
- CORS-friendly, so the browser can call it directly.

**pools.trade** — `https://pools.trade/api/trpc/{procedure}?batch=1&input={json}`
Undocumented tRPC endpoint that serves their own frontend. It only knows tokens
launched on pools.trade, which is one venue out of 40, so it is an enrichment
layer, never the source of the list.

- `curve.listLaunches` — `sortBy` accepts only `trending` and `volume`. Anything
  else returns 400. `limit` is ignored, always 100 rows.
- `curve.searchLaunches` `{query}` — 100 rows, used as a letter sweep
- `cca.listAuctions` — Crowd Launch auctions
- Token addresses arrive checksummed (mixed case). The whole data model keys
  on LOWERCASE addresses; normalise at every boundary or live merges duplicate
  rows (this bug shipped once).
- Gives what nothing else does: `holderCount`, `graduationProgress` against a
  $50,000 FDV target, `safety.isSpam` and verdicts, linked X accounts,
  `poolPriceSeries`, `recentTrades`.

**Robinhood Chain** — chain id 4663, Arbitrum Orbit L2, mainnet since 1 July
2026, gas in ETH, ~100ms blocks. Public RPC `rpc.mainnet.chain.robinhood.com`,
explorer `robinhoodchain.blockscout.com` (free API, useful for holder counts and
contract verification on tokens the launchpad does not cover).

Reading raw chain data is not a shortcut. Price in USD, liquidity, 24h volume
and FDV are not stored on chain; deriving them means decoding Uniswap v4 swaps,
reconstructing reserves and aggregating across ~900k blocks a day. That is an
indexer, not a parser.

## Data model

One record per token, keyed by lowercase contract address, merged from all
sources:

```
addr sym name img desc socials x xok
pool dex pair created price ch1h ch6h ch24h vol1h vol24 liq fdv mcap
buys1h sells1h buyers1h holders
grad target launchpad spam verdict flags badges series trades
hist          our own snapshots: [unix_ts, price, holders, fdv]
first_seen last_seen
```

`hist`, `first_seen` and `last_seen` are the part no source provides. A token
that drops out of every feed stays in ours. That accumulated history is the only
thing here that cannot be copied by a competitor with the same APIs.

## Rules

**No API key ever reaches the client bundle.** The site has no key of its own.
The chat offers a free keyless provider by default, and a bring-your-own-key
option stored in the user's browser.

**Free sources only, and name them.** Every figure on screen traces to a source
the user can check. When a number is missing, show a dash and say why, never
invent or interpolate.

**Nothing is financial advice.** The token read states what the numbers imply
about liquidity depth, concentration and turnover. It never predicts price or
tells anyone to buy.

**Honest limits in the UI.** Snapshot age is always visible. When a feed fails,
the page says so instead of showing stale numbers as if they were live.

**Style.** Dense monospace terminal, dark. Colours already defined as CSS
variables at the top of `index.html`. No emoji, no gradients, no rounded card
soup. British-ish plain English in the interface, lowercase labels.

**Keyboard shortcuts must never fire while the user is typing.** This broke once
already: pressing `e` in the chat downloaded a file.
