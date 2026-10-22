# SPDX-License-Identifier: LicenseRef-PolyForm-Noncommercial-1.0.0 (see LICENSE)
"""Check pine/DivergenceSignalAlerts.pine against tests/reference.py, bar by bar, with PineTS (a local Pine v6 runtime).

    cd tests/runner && npm install && cd ../..
    python3 tests/check_pine.py <bars dir>
<bars dir> holds <SYMBOL>_<TF>.csv files with the columns time (unix ms), open, high, low, close[, volume], oldest first.

Parity: on every bar the script's divergence (direction, type, oscillator mask, older pivot; Data Window plots) equals
the reference's, and its RSI and MACD histogram equal the reference's to 1e-5 of the series' median size (PineTS rounds
every reassigned value to 10 decimals, so EURUSD's RSI averages of about 1e-4 lose up to 1e-6 relative), for each
configuration below.
Exact ties: PineTS also treats two numbers within 1e-10 as equal, where TradingView, the MetaTrader editions and the
reference compare exactly. A mismatch counts as an exact tie only when the reference rerun with PineTS's comparisons
gives the script's value; ties are listed bar by bar and do not fail the check. Any other mismatch fails it.
Repaint: the script is re-run on the series cut at three points; every bar before a cut must read the same as in the
full run (a closed bar never changes when later bars arrive)."""
import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from reference import NAMES, Params, run  # noqa: E402

PINE = HERE.parent / "pine" / "DivergenceSignalAlerts.pine"
RUNNER = HERE / "runner" / "run_pine.mjs"
EVENT_PLOTS = ["Divergence (+1 bullish, -1 bearish)", "Type (1 regular, 2 hidden)", "Oscillators (bit mask: 1 RSI, 2 MACD)",
               "Older pivot (bars back)"]
OSC_PLOTS = ["RSI", "MACD histogram"]
# tag: (PineTS input overrides keyed by input title, reference Params)
CONFIGS = {
    "default": ({}, Params()),
    "hidden_both": ({"Hidden divergence (continuation)": True, "Only when RSI and MACD both diverge": True},
                    Params(hidden=True, min_count=2)),
    "rsi_noclean": ({"MACD histogram divergence": False, "Clean lines": False, "Pivot: bars on the left": 3,
                     "Pivot: bars on the right": 2},
                    Params(use=(True, False), clean=False, left=3, right=2)),
}


def pinets_semantics():
    """reference.py with PineTS's comparisons: PineTS treats two numbers within 1e-10 of each other as equal (TradingView
    compares exactly, like the reference and the MetaTrader editions). Used only to tell an exact-tie mismatch from a real one: on a
    tie such as a bar touching the q-p line exactly, the two runtimes legitimately decide differently."""
    spec = importlib.util.spec_from_file_location("reference_pinets", HERE / "reference.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    eps = 1e-10

    def gt(x, y):
        return x - y >= eps

    def lt(x, y):
        return y - x >= eps

    def pivot_low(l, p, pr):
        return p - pr.left >= 0 and all(gt(l[p - j], l[p]) for j in range(1, pr.left + 1)) and \
            not any(gt(l[p], l[p + j]) for j in range(1, pr.right + 1))

    def pivot_high(h, p, pr):
        return p - pr.left >= 0 and all(gt(h[p], h[p - j]) for j in range(1, pr.left + 1)) and \
            not any(lt(h[p], h[p + j]) for j in range(1, pr.right + 1))

    def clean(series, q, p, d):
        a, b = series[q], series[p]
        for j in range(q + 1, p):
            v = series[j]
            if v is None:
                return False
            line = a + (b - a) * (j - q) / float(p - q)
            if lt(v, line) if d > 0 else gt(v, line):
                return False
        return True

    def check(h, l, osc, pr, p, d, prev):
        price = l if d > 0 else h
        for q in prev:
            dist = p - q
            if dist < pr.min_dist:
                continue
            if dist > pr.max_dist:
                break
            pp, pq = price[p], price[q]
            typ = (1 if lt(pp, pq) else (2 if gt(pp, pq) else 0)) if d > 0 else (1 if gt(pp, pq) else (2 if lt(pp, pq) else 0))
            if typ == 0 or (typ == 1 and not pr.regular) or (typ == 2 and not pr.hidden):
                continue
            if pr.clean and not clean(price, q, p, d):
                continue
            mask = cnt = 0
            for o in range(len(pr.use)):
                op, oq = osc[o][p], osc[o][q]
                if not pr.use[o] or op is None or oq is None:
                    continue
                if not (gt(d * (op - oq), 0) if typ == 1 else lt(d * (op - oq), 0)):
                    continue
                if pr.clean and not clean(osc[o], q, p, d):
                    continue
                mask |= 1 << o
                cnt += 1
            if cnt >= max(1, pr.min_count):
                return dict(dir=d, type=typ, mask=mask, count=cnt, p=p, q=q, ppx=pp, qpx=pq)
        return None

    m.pivot_low, m.pivot_high, m.clean, m.check = pivot_low, pivot_high, clean, check
    return m.run


RUN_PINETS = pinets_semantics()


def pine_run(bars_csv, inputs):
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as t:
        out = t.name
    cmd = ["node", str(RUNNER), str(PINE), str(bars_csv), out]
    for k, v in inputs.items():
        cmd += ["--input", f"{k}={str(v).lower() if isinstance(v, bool) else v}"]
    subprocess.run(cmd + EVENT_PLOTS + OSC_PLOTS, check=True, capture_output=True, text=True)
    d = pd.read_csv(out)
    os.unlink(out)
    return d


def events_of(d):
    """Per bar: direction, type, mask, older pivot bars back (0 where no divergence)."""
    return np.stack([d[c].fillna(0).astype(int).to_numpy() for c in EVENT_PLOTS], axis=1)


def expected(b, pr, runner=run):
    ev, osc = runner(*(b[k].to_numpy(float) for k in ("high", "low", "close")), pr)
    e = np.zeros((len(b), 4), dtype=int)
    for k, x in ev.items():
        e[k] = (x["dir"], x["type"], x["mask"], k - x["q"])
    return e, osc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bars")
    a = ap.parse_args()
    series = sorted(Path(a.bars).glob("*.csv"))
    ok = True
    for tag, (inputs, pr) in CONFIGS.items():
        events = bad = ties = osc_bad = 0
        for f in series:
            b = pd.read_csv(f)
            d = pine_run(f, inputs)
            got = events_of(d)
            exp, osc = expected(b, pr)
            events += int((exp[:, 0] != 0).sum())
            miss = np.where((got != exp).any(axis=1))[0]
            if len(miss):
                alt, _ = expected(b, pr, RUN_PINETS)
                tie = [i for i in miss if (got[i] == alt[i]).all()]
                ties += len(tie)
                miss = [i for i in miss if i not in tie]
                for i in tie:
                    print(f"  {tag} {f.stem} bar {i}: exact tie, pine {got[i].tolist()} reference {exp[i].tolist()}")
            bad += len(miss)
            for i in miss[:3]:
                print(f"  {tag} {f.stem} bar {i}: pine {got[i].tolist()} reference {exp[i].tolist()}")
            for o, name in enumerate(NAMES):
                ref = np.array([np.nan if v is None else v for v in osc[o]], dtype=float)
                g = d[OSC_PLOTS[o]].to_numpy(float)
                scale = max(np.nanmedian(np.abs(ref)), 1e-12)
                diff = np.where(np.isnan(ref) & np.isnan(g), 0.0, np.abs(g - ref))
                n = int((~(diff <= 1e-5 * scale)).sum())
                osc_bad += n
                if n:
                    i = int(np.argmax(~(diff <= 1e-5 * scale)))
                    print(f"  {tag} {f.stem} {name} bar {i}: pine {g[i]} reference {ref[i]}")
        print(f"parity {tag}: {len(series)} series, {events} divergences, {bad} mismatches, {ties} exact ties; "
              f"oscillator values off: {osc_bad}")
        ok &= bad == 0 and osc_bad == 0
    # repaint: cut each series at three points and compare with the full run (hidden divergence on as well)
    inputs = {"Hidden divergence (continuation)": True}
    changed = checked = 0
    for f in series:
        b = pd.read_csv(f)
        full = events_of(pine_run(f, inputs))
        n = len(b)
        for cut in (n // 2, n - 300, n - 1):
            with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as t:
                b.iloc[:cut].to_csv(t, index=False)
            part = events_of(pine_run(t.name, inputs))
            os.unlink(t.name)
            checked += part[:cut].size
            changed += int((part[:cut] != full[:cut]).sum())
    print(f"repaint: {checked} bar values re-read after cuts, {changed} changed")
    ok &= changed == 0
    print("ALL PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
