"""Builds a replay file from two years of saved Yahoo hourly bars (research download)."""
import pandas as pd, json, sys
bars = pd.read_pickle(sys.argv[1])
out = {}
for s, d in bars.items():
    sym = s.replace("-USD", "")
    start = [int(x.timestamp()) for x in d.index]
    lag = 3600 if s.endswith("-USD") else 5400
    h = [[int(t) + lag, float(o), float(hi), float(lo), float(c)] for t, o, hi, lo, c in zip(start, d.o, d.h, d.l, d.c)]
    if s.endswith("-USD"):
        daily = d.c.resample("1D").last().dropna()
        dd = [[i.strftime("%Y-%m-%d"), float(v)] for i, v in daily.items()]
    else:
        et_idx = d.index.tz_convert("America/New_York")
        daily = pd.Series(d.c.values, index=et_idx).groupby(et_idx.date).last()
        dd = [[str(i), float(v)] for i, v in daily.items()]
    out[sym] = {"h": h, "d": dd}
json.dump(out, open(sys.argv[2], "w"))
print({k: (len(v["h"]), len(v["d"])) for k, v in out.items()})
