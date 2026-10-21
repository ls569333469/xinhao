"""Read-only Santiment exchange flows. Historical evidence never changes live signals."""
import argparse
import datetime as dt
import json
import math
from pathlib import Path
import time
import urllib.request
from env_config import env_get

from scanner import CG_IDS

ROOT = Path(__file__).resolve().parent
UTC = dt.timezone.utc
ENDPOINT = "https://api.santiment.net/graphql"
METRICS = ("exchange_inflow", "exchange_outflow", "exchange_inflow_usd", "exchange_outflow_usd")


def graphql(query):
    headers = {"Content-Type": "application/json", "User-Agent": "EvidencePanel/1.0"}
    # Provider-specific credential; never forward keys between providers.
    key = env_get("SANTIMENT_API_KEY")
    if key:
        headers["Authorization"] = "Apikey " + key
    req = urllib.request.Request(ENDPOINT, data=json.dumps({"query": query}).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.load(response)


def iso(value):
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def normalize_series(series, start, end):
    """Reject partial days, duplicate buckets and null/nonfinite numbers; do not fill zeros."""
    expected = {iso(start + dt.timedelta(days=i)) for i in range((end-start).days)}
    result = {}
    if not isinstance(series, list):
        return None
    for point in series:
        stamp, value = point.get("datetime"), point.get("value")
        if stamp not in expected or stamp in result:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            return None
        result[stamp] = value
    return result if set(result) == expected else None


def collect(start, end):
    raw = []
    def fetch(q):
        response = graphql(q)
        raw.append({"query": q, "response": response, "retrievedAt": iso(dt.datetime.now(UTC))})
        time.sleep(.55)
        return response
    query = "{" + " ".join(f'm{i}:getMetric(metric:{json.dumps(m)}){{metadata{{isRestricted availableSlugs docs{{link}}}}}}' for i,m in enumerate(METRICS)) + "}"
    meta = fetch(query)
    if meta.get("errors") or not meta.get("data"):
        raise ValueError("Provider metadata unavailable; no coverage can be confirmed")
    supported = set.intersection(*(set(meta["data"][f"m{i}"]["metadata"]["availableSlugs"]) for i in range(4)))
    mappings = [(asset, slug) for asset,slug in CG_IDS.items() if slug in supported]
    projects = fetch("{" + " ".join(f'p{i}:projectBySlug(slug:{json.dumps(slug)}){{slug ticker name infrastructure}}' for i,(_,slug) in enumerate(mappings)) + "}") if mappings else {"data": {}}
    # Explicit project ID allowlist plus provider identity. Ticker-only search is never used.
    accepted = [(asset,slug,projects.get("data",{}).get(f"p{i}")) for i,(asset,slug) in enumerate(mappings)]
    accepted = [(a,s,p) for a,s,p in accepted if p and p["slug"] == s and p["ticker"].upper() == a]
    now = dt.datetime.now(UTC)
    probe_end = now.replace(hour=0,minute=0,second=0,microsecond=0)
    probe_start = probe_end-dt.timedelta(days=1)
    probe = fetch('{getMetric(metric:"exchange_inflow"){timeseriesData(slug:"bitcoin",from:'+json.dumps(iso(probe_start))+',to:'+json.dumps(iso(probe_end))+',interval:"1d"){datetime value}}}')
    records, failures = [], []
    for offset in range(0,len(accepted),4):
        batch = accepted[offset:offset+4]
        parts = []
        for i,(_,slug,_) in enumerate(batch):
            for j,metric in enumerate(METRICS):
                parts.append(f'r{i}_{j}:getMetric(metric:{json.dumps(metric)}){{timeseriesData(slug:{json.dumps(slug)},from:{json.dumps(iso(start))},to:{json.dumps(iso(end))},interval:"1d"){{datetime value}}}}')
        response = fetch("{"+" ".join(parts)+"}")
        for i,(asset,slug,project) in enumerate(batch):
            series = [normalize_series(((response.get("data") or {}).get(f"r{i}_{j}") or {}).get("timeseriesData"),start,end) for j in range(4)]
            errors = [e for e in response.get("errors",[]) if e.get("path",[None])[0] in [f"r{i}_{j}" for j in range(4)]]
            if any(s is None for s in series) or errors:
                failures.append({"asset": asset, "slug":slug, "reason":"Incomplete/denied daily flow series", "errors":errors})
                continue
            days = [{"from":stamp,"to":iso(dt.datetime.fromisoformat(stamp.replace("Z","+00:00"))+dt.timedelta(days=1)),
                     "inflowTokens":series[0][stamp],"outflowTokens":series[1][stamp],
                     "netInflowTokens":series[0][stamp]-series[1][stamp],
                     "inflowUsd":series[2][stamp],"outflowUsd":series[3][stamp],
                     "netInflowUsd":series[2][stamp]-series[3][stamp]} for stamp in sorted(series[0])]
            records.append({"asset":asset,"slug":slug,"identity":project,"mappingStatus":"explicit project ID + provider slug/ticker; network scope not audited",
                            "days":days,"eligibleForLiveSignals":False,"usage":"historical provider evidence only"})
    scan = json.loads((ROOT/"scan.json").read_text(encoding="utf-8"))
    covered = {r["asset"] for r in records}
    result = {"provider":"Santiment","endpoint":ENDPOINT,"retrievedAt":iso(dt.datetime.now(UTC)),
              "windowFrom":iso(start),"windowTo":iso(end),"interval":"1d UTC", "signConvention":"net inflow = inflow - outflow; negative means net outflow",
              "scope":"Provider-labelled aggregate exchanges, not Binance-only; address/network completeness and internal transfer exclusions not independently audited",
              "usdMethod":"Provider exchange_inflow_usd/exchange_outflow_usd; never multiplied by current price",
              "currentProbe":{"from":iso(probe_start),"to":iso(probe_end),"response":probe},
              "liveSignalCoverage":0,"snapshotContracts":len(scan["rows"]),"historicalAssets":len(records),
              "historicalContractReferences":sum(r["asset"] in covered for r in scan["rows"]),
              "completeAssetDays":sum(len(r["days"]) for r in records),"records":records,"failures":failures,
              "unmappedAssets":"Other assets remain unknown; unavailable slug or unverified identity never interpreted as zero",
              "metadata":meta["data"]}
    return result,raw


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", help="UTC complete day, YYYY-MM-DD")
    parser.add_argument("--to-date", help="UTC exclusive end, YYYY-MM-DD")
    args = parser.parse_args()
    default_end = (dt.datetime.now(UTC)-dt.timedelta(days=31)).replace(hour=0,minute=0,second=0,microsecond=0)
    end = dt.datetime.strptime(args.to_date,"%Y-%m-%d").replace(tzinfo=UTC) if args.to_date else default_end
    start = dt.datetime.strptime(args.from_date,"%Y-%m-%d").replace(tzinfo=UTC) if args.from_date else end-dt.timedelta(days=7)
    if not 1 <= (end-start).days <= 90 or end > dt.datetime.now(UTC).replace(hour=0,minute=0,second=0,microsecond=0):
        parser.error("Require 1-90 complete UTC days ending no later than today")
    result,raw = collect(start,end)
    for filename,data in (("chain-flows.json",result),("chain-flow-evidence.json",raw)):
        path=ROOT/filename
        temp=path.with_suffix(".tmp.json")
        temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
        temp.replace(path)
    print(json.dumps({k:result[k] for k in ("windowFrom","windowTo","historicalAssets","historicalContractReferences","completeAssetDays","liveSignalCoverage")},ensure_ascii=False))


if __name__ == "__main__":
    main()
