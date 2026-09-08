#!/usr/bin/env python3
"""Harvests pools.trade and accumulates its own history of Robinhood Chain launches.

pools.trade caps every endpoint at 100 rows and only accepts two sort orders, so
no single call sees the whole chain. This script works around that in two ways:

  1. Breadth. It reads trending, volume, the auction list, and a search sweep
     across single characters. Each call returns a different 100; merged and
     deduplicated by token address they cover far more than any one of them.

  2. Memory. Everything seen is written to data/launches.json and kept. A token
     that drops out of pools.trade's top 100 stays in ours, with the timestamp
     we first saw it and a trail of snapshots. That history is the thing the
     source itself does not keep.

No API key. The upstream endpoint is undocumented and can change without notice,
so every field access is defensive and a failed run leaves existing data alone.
"""
import html, json, os, sys, time, urllib.error, urllib.parse, urllib.request

BASE = "https://pools.trade/api/trpc"
GT = "https://api.geckoterminal.com/api/v2/networks/robinhood"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "data")
UA = {"User-Agent": "launchbrain/1.0 (+https://github.com/undefined-ui/launchbrain)"}

SWEEP = list("abcdefghijklmnopqrstuvwxyz0123456789")
GT_PAGE_CAP = 10           # the free tier answers 401 beyond page 10, everywhere
GT_BUDGET = int(os.environ.get("LB_BUDGET_S", "780"))  # sweep time budget, seconds:
                           # at ~28 calls/min the full sweep can outlast any CI
                           # timeout, so stop on budget and write what we have
MAX_TOKENS = 6000          # evict least recently seen beyond this
SNAPSHOT_CAP = 48          # per token, roughly two days at 1h spacing
DETAIL_TOP = 300           # keep trades and price series for this many by volume

# the committed baseline is a cold-start file for the browser, which takes over
# live after first paint. keep it small enough for mobile: recent tokens stay in
# launches.json, the rest moves to archive.json, which the page does not load.
WORK_WINDOW = 3 * 86400    # a token unseen this long leaves the working set
WORK_CAP = 1500            # hard cap on working-set rows, kept by 24h volume
ARCHIVE_CAP = 20000        # archive rows, kept by last_seen
ARCHIVE_SNAPSHOT_CAP = 96  # archived tokens keep a longer but leaner trail


def call(proc, payload, tries=2):
    q = urllib.parse.quote(json.dumps({"0": payload}, separators=(",", ":")))
    url = f"{BASE}/{proc}?batch=1&input={q}"
    for n in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.load(r)[0]["result"]["data"] or []
        except Exception as e:
            if n + 1 == tries:
                print(f"  ! {proc} {payload}: {e}", file=sys.stderr)
                return []
            time.sleep(2)


def num(v, d=0.0):
    try: return float(v)
    except (TypeError, ValueError): return d


# ---- GeckoTerminal: the breadth. Whole chain, 40 dexes, no key. ----
# The free tier allows roughly 30 calls a minute and enforces it with 429s,
# so the limiter below is adaptive: back off on 429, decay back on success.
# Do not remove it.
_gt_delay = 2.1


def gt_call(path, tries=8):
    global _gt_delay
    for n in range(tries):
        time.sleep(_gt_delay)
        try:
            req = urllib.request.Request(GT + path, headers={**UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=45) as r:
                _gt_delay = max(2.0, _gt_delay * 0.9)
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:                  # keep waiting: the sleep above escalates
                _gt_delay = min(_gt_delay * 1.8, 60)
                if n + 1 == tries:
                    print(f"  ! gecko {path}: still 429 after {tries} tries", file=sys.stderr)
                continue
            if e.code not in (401, 404):       # 401 = past the free page cap
                print(f"  ! gecko {path}: http {e.code}", file=sys.stderr)
            return None
        except Exception as e:
            if n + 1 == tries:
                print(f"  ! gecko {path}: {e}", file=sys.stderr)
                return None
    return None


def gt_token(pool, inc):
    at = pool.get("attributes") or {}
    rel = pool.get("relationships") or {}
    bid = ((rel.get("base_token") or {}).get("data") or {}).get("id") or ""
    addr = bid.split("_", 1)[-1].lower()
    if not addr.startswith("0x"):
        return None
    ta = (inc.get(bid) or {}).get("attributes") or {}
    pcp = at.get("price_change_percentage") or {}
    vol = at.get("volume_usd") or {}
    tx = (at.get("transactions") or {}).get("h1") or {}
    return {
        "sym": html.unescape(ta.get("symbol") or ""), "name": html.unescape(ta.get("name") or ""),
        "addr": addr, "img": ta.get("image_url"),
        "pool": (at.get("address") or "").lower(),
        "dex": ((rel.get("dex") or {}).get("data") or {}).get("id"),
        "created": at.get("pool_created_at"), "desc": "",
        "price": num(at.get("base_token_price_usd")), "fdv": num(at.get("fdv_usd")),
        "mcap": num(at.get("market_cap_usd")), "liq": num(at.get("reserve_in_usd")),
        "ch1h": num(pcp.get("h1")), "ch6h": num(pcp.get("h6")), "ch24h": num(pcp.get("h24")),
        "vol1h": num(vol.get("h1")), "vol24": num(vol.get("h24")),
        "buys1h": tx.get("buys") or 0, "sells1h": tx.get("sells") or 0,
        "buyers1h": tx.get("buyers") or 0,
        "holders": 0, "grad": None, "spam": False, "verdict": None,
        "flags": [], "badges": [], "series": [], "trades": [],
    }


def harvest_gecko():
    t0 = time.time()
    seen, calls = {}, 0

    def absorb(doc):
        nonlocal calls
        calls += 1
        if not doc:
            return 0
        inc = {i["id"]: i for i in doc.get("included") or []}
        rows = doc.get("data") or []
        for pool in rows:
            t = gt_token(pool, inc)
            if not t:
                continue
            prev = seen.get(t["addr"])
            # a token trading in several pools keeps its deepest pool's numbers
            if not prev or (t["liq"] or 0) > (prev["liq"] or 0):
                seen[t["addr"]] = t
        return len(rows)

    def spent():
        return time.time() - t0 > GT_BUDGET

    for p in range(1, 4):
        absorb(gt_call(f"/new_pools?include=base_token,dex&page={p}"))
    absorb(gt_call("/trending_pools?include=base_token,dex"))
    for p in range(1, GT_PAGE_CAP + 1):
        # a short page is the last page; do not pay for the empty one after it
        if absorb(gt_call(f"/pools?include=base_token,dex&page={p}")) < 20:
            break
    # the real breadth: every venue's own pool list, paged until empty.
    # Busy venues first — ranked by how often each appears in the pools already
    # absorbed — so a spent budget costs the dust tail, not the movers.
    dexes = []
    for p in range(1, 4):
        doc = gt_call(f"/dexes?page={p}")
        rows = (doc or {}).get("data") or []
        if not rows:
            break
        dexes += [d.get("id") for d in rows if d.get("id")]
    freq = {}
    for t in seen.values():
        freq[t.get("dex")] = freq.get(t.get("dex"), 0) + 1
    dexes.sort(key=lambda d: -freq.get(d, 0))
    swept = 0
    for dex in dexes:
        if spent():
            print(f"  time budget spent after {swept}/{len(dexes)} dexes; "
                  f"writing what we have", file=sys.stderr)
            break
        for p in range(1, GT_PAGE_CAP + 1):
            if absorb(gt_call(f"/dexes/{dex}/pools?include=base_token,dex&page={p}")) < 20 or spent():
                break
        swept += 1
    print(f"  {calls} gecko calls, {swept}/{len(dexes)} dexes in "
          f"{int(time.time()-t0)}s -> {len(seen)} unique tokens")
    return seen


# fields only pools.trade knows; they overlay the gecko record when both saw a token
PT_ENRICH = ["desc", "x", "xok", "img", "emoji", "hue", "holders", "grad", "target",
             "creator", "chandle", "spam", "verdict", "flags", "badges",
             "series", "trades", "status"]


def launch(r):
    ps, sf = r.get("poolStats") or {}, r.get("safety") or {}
    return {
        "sym": html.unescape(r.get("tokenSymbol") or ""),
        "name": html.unescape(r.get("tokenName") or ""),
        "addr": (r.get("tokenAddress") or "").lower(),  # the whole model keys on lowercase
        "pool": r.get("poolId"), "launchpad": "pools.trade",
        "desc": (r.get("description") or "")[:220],
        "x": r.get("xUrl"), "xok": bool(r.get("xVerified")),
        "img": r.get("imageUrl"), "emoji": r.get("imageEmoji"), "hue": r.get("imageHue"),
        "created": r.get("createdAt"), "status": r.get("status"),
        "fdv": num(r.get("fdvUsd")), "grad": num(r.get("graduationProgress")),
        "target": num(r.get("graduationTargetUsd")),
        "holders": r.get("holderCount") or 0, "buyers1h": r.get("buyersLast1h") or 0,
        "creator": r.get("creatorAddress"), "chandle": r.get("creatorHandle"),
        "price": num(ps.get("priceUsd")), "ch1h": num(ps.get("priceChange1hPct")),
        "ch24h": num(ps.get("priceChange24hPct")),
        "vol24": num(ps.get("volume24hUsd")), "liq": num(ps.get("liquidityUsd")),
        "spam": bool(sf.get("isSpam")), "verdict": sf.get("verdict"),
        "flags": sf.get("features") or [],
        "badges": [f"{b.get('type')}{':'+b['tier'] if b.get('tier') else ''}"
                   for b in (r.get("badges") or [])],
        "series": [round(num(p.get("clearingPriceUsd")), 12)
                   for p in (r.get("poolPriceSeries") or [])][-72:],
        "trades": [{"side": t.get("side"), "usd": round(num(t.get("amountUsd")), 2),
                    "trader": (t.get("traderAddress") or "")[:10], "at": t.get("at"),
                    "tx": t.get("txHash")} for t in (r.get("recentTrades") or [])[:12]],
    }


def auction(r):
    sf, ps = r.get("safety") or {}, r.get("poolStats") or {}
    return {
        "sym": html.unescape(r.get("tokenSymbol") or ""),
        "name": html.unescape(r.get("tokenName") or ""),
        "addr": (r.get("tokenAddress") or "").lower(),
        "auction": r.get("auctionContractAddress"),
        "desc": (r.get("description") or "")[:220],
        "emoji": r.get("imageEmoji"), "hue": r.get("imageHue"), "img": r.get("imageUrl"),
        "xok": bool(r.get("xVerified")), "status": r.get("status"),
        "clearing": num(r.get("clearingPriceUsd")), "floor": num(r.get("floorPriceUsd")),
        "fdv": num(r.get("fdvUsd")), "raised": num(r.get("raisedUsd")),
        "raised1h": num(r.get("raisedUsdLast1h")),
        "target": num(r.get("graduationTargetUsd")), "grad": num(r.get("graduationProgress")),
        "bidders": r.get("bidderCount") or 0, "holders": r.get("holderCount") or 0,
        "starts": r.get("startsAt"), "ends": r.get("endsAt"),
        "creator": r.get("creatorAddress"), "chandle": r.get("creatorHandle"),
        "cfee": bool(r.get("creatorFeeEnabled")),
        "spam": bool(sf.get("isSpam")), "verdict": sf.get("verdict"),
        "vol24": num(ps.get("volume24hUsd")), "liq": num(ps.get("liquidityUsd")),
        "bids": [{"who": b.get("bidder"), "usd": num(b.get("amountUsd")), "at": b.get("at")}
                 for b in (r.get("recentBids") or [])[:10]],
    }


def harvest():
    seen, calls = {}, 0
    def take(rows):
        nonlocal calls
        calls += 1
        for r in rows:
            a = (r.get("tokenAddress") or "").lower()
            if a and a not in seen:
                seen[a] = launch(r)

    take(call("curve.listLaunches", {"sortBy": "trending"}))
    take(call("curve.listLaunches", {"sortBy": "volume"}))
    for term in SWEEP:
        take(call("curve.searchLaunches", {"query": term}))
        time.sleep(0.25)
    print(f"  {calls} calls -> {len(seen)} unique tokens")
    return seen


def merge(old, fresh, now, archive):
    """Keep everything ever seen; record a snapshot trail per token."""
    for t in old.get("launches", []):        # migrate pre-normalisation files
        t["addr"] = (t.get("addr") or "").lower()
    out = {t["addr"]: t for t in old.get("launches", [])}
    for addr, t in fresh.items():
        prev = out.get(addr) or archive.pop(addr, None)  # reappearance restores history
        if prev:
            hist = prev.get("hist", [])
            last = hist[-1] if hist else None
            if not last or now - last[0] >= 1800:      # at most one snapshot per 30 min
                hist.append([now, round(t["price"], 12), t["holders"], round(t["fdv"], 2)])
            t["hist"] = hist[-SNAPSHOT_CAP:]
            t["first_seen"] = prev.get("first_seen", now)
            if not t["series"]:
                t["series"] = prev.get("series", [])
            if not t["trades"]:
                t["trades"] = prev.get("trades", [])
        else:
            t["hist"] = [[now, round(t["price"], 12), t["holders"], round(t["fdv"], 2)]]
            t["first_seen"] = now
        t["last_seen"] = now
        out[addr] = t

    rows = sorted(out.values(), key=lambda t: -t.get("last_seen", 0))
    if len(rows) > MAX_TOKENS:
        rows = rows[:MAX_TOKENS]
    # trim heavy fields on everything outside the top slice by volume
    keep = {t["addr"] for t in sorted(rows, key=lambda t: -t.get("vol24", 0))[:DETAIL_TOP]}
    for t in rows:
        if t["addr"] not in keep:
            t["series"], t["trades"] = [], []
    return rows


def split_archive(rows, archive, now):
    """Move stale or over-cap tokens out of the working set into the archive."""
    fresh_enough = [t for t in rows if now - t.get("last_seen", 0) <= WORK_WINDOW]
    stale = [t for t in rows if now - t.get("last_seen", 0) > WORK_WINDOW]
    if len(fresh_enough) > WORK_CAP:
        # either signal keeps a token in the working set: turnover or real depth
        fresh_enough.sort(key=lambda t: -max(t.get("vol24") or 0, t.get("liq") or 0))
        stale += fresh_enough[WORK_CAP:]
        fresh_enough = fresh_enough[:WORK_CAP]
    for t in stale:
        t = dict(t, series=[], trades=[])
        t["hist"] = t.get("hist", [])[-ARCHIVE_SNAPSHOT_CAP:]
        archive[t["addr"]] = t
    arch_rows = sorted(archive.values(), key=lambda t: -t.get("last_seen", 0))[:ARCHIVE_CAP]
    return fresh_enough, arch_rows


def main():
    os.makedirs(OUT, exist_ok=True)
    # one harvester at a time: concurrent runs starve each other on the rate
    # limit and race on the output files. A fresh lock means someone is running.
    lock = f"{OUT}/.fetch.lock"
    if os.path.exists(lock) and time.time() - os.path.getmtime(lock) < 1800:
        raise SystemExit("another fetch is running (data/.fetch.lock is fresh); exiting")
    open(lock, "w").write(str(int(time.time())))
    try:
        run()
    finally:
        try: os.remove(lock)
        except OSError: pass


def run():
    now = int(time.time())
    lp, ap = f"{OUT}/launches.json", f"{OUT}/auctions.json"
    xp = f"{OUT}/archive.json"
    old = json.load(open(lp)) if os.path.exists(lp) else {"launches": []}
    archive = {(t.get("addr") or "").lower(): dict(t, addr=(t.get("addr") or "").lower())
               for t in (json.load(open(xp)).get("launches", []) if os.path.exists(xp) else [])}

    print("harvesting geckoterminal (whole chain)")
    fresh = harvest_gecko()
    print("harvesting pools.trade (enrichment)")
    pt = harvest()
    for addr, t in pt.items():
        g = fresh.get(addr)
        if not g:
            fresh[addr] = t
            continue
        # gecko keeps the market numbers (deepest pool, whole chain);
        # pools.trade adds what nothing else has
        for k in PT_ENRICH:
            v = t.get(k)
            if v or k in ("spam", "xok"):
                if k == "img" and g.get("img"):
                    continue
                g[k] = v
        g["launchpad"] = "pools.trade"
    if not fresh:
        raise SystemExit("nothing returned, leaving previous data in place")

    rows = merge(old, fresh, now, archive)
    rows, arch_rows = split_archive(rows, archive, now)
    print("harvesting auctions")
    auctions = [auction(r) for r in call("cca.listAuctions", {"sortBy": "trending"})]
    if not auctions:
        auctions = [auction(r) for r in call("cca.listAuctions", {})]

    meta = {"fetched_at": now, "chain_id": 4663, "source": "geckoterminal + pools.trade",
            "tracked": len(rows), "seen_this_run": len(fresh), "auctions": len(auctions),
            "new_this_run": sum(1 for t in rows if t.get("first_seen") == now),
            "archived": len(arch_rows)}
    launches_doc = {"meta": meta, "launches": rows}
    auctions_doc = {"meta": meta, "auctions": auctions}
    json.dump(launches_doc, open(lp, "w"))
    json.dump(auctions_doc, open(ap, "w"))
    json.dump({"meta": meta, "launches": arch_rows}, open(xp, "w"))
    # js copies: fetch() is blocked on file://, a script tag is not
    with open(f"{OUT}/launches.js", "w") as f:
        f.write("window.LB_LAUNCHES=" + json.dumps(launches_doc) + ";")
    with open(f"{OUT}/auctions.js", "w") as f:
        f.write("window.LB_AUCTIONS=" + json.dumps(auctions_doc) + ";")
    print(f"tracked {len(rows)} tokens ({meta['new_this_run']} new), "
          f"{len(arch_rows)} archived, {len(auctions)} auctions, "
          f"{os.path.getsize(lp)//1024} KB working set")


if __name__ == "__main__":
    main()
