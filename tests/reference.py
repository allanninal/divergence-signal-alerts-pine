# SPDX-License-Identifier: LicenseRef-PolyForm-Noncommercial-1.0.0 (see LICENSE)
"""Python twin of pine/DivergenceSignalAlerts.pine (and of the MetaTrader edition's divergence engine): the same two
oscillators (RSI, MACD), pivots and divergence rule, operation for operation. This file is the exact rule book; the Pine
script is checked against it bar by bar (tests/check_pine.py).

    from reference import Params, run
    events, osc = run(high, low, close, Params())
"""
from dataclasses import dataclass, field

import numpy as np

RSI, MACD = range(2)
NAMES = ["RSI", "MACD"]


@dataclass
class Params:
    use: tuple = (True, True)
    rsi_n: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_sig: int = 9
    left: int = 5
    right: int = 3
    min_dist: int = 5
    max_dist: int = 60
    regular: bool = True
    hidden: bool = False
    clean: bool = True
    min_count: int = 1


class Ema:
    """SMA-seeded EMA (DVEmaStep)."""

    def __init__(self):
        self.cnt, self.sum, self.v = 0, 0.0, None

    def step(self, n, x):
        self.cnt += 1
        if self.cnt < n:
            self.sum += x
            self.v = None
        elif self.cnt == n:
            self.sum += x
            self.v = self.sum / n
        else:
            self.v = self.v + 2.0 / (n + 1) * (x - self.v)
        return self.v


def oscillators(c, p: Params):
    """Both oscillators on every bar (None until they have enough bars), as DVOscStep computes them."""
    n = len(c)
    out = [[None] * n for _ in range(2)]
    sg = sl = gain = loss = 0.0
    ef, es, esg = Ema(), Ema(), Ema()
    for k in range(n):
        # RSI (Wilder)
        if k >= 1:
            ch = c[k] - c[k - 1]
            g, ls = max(ch, 0.0), max(-ch, 0.0)
            m = p.rsi_n
            if k < m:
                sg += g
                sl += ls
            elif k == m:
                sg += g
                sl += ls
                gain, loss = sg / m, sl / m
            else:
                gain = (gain * (m - 1) + g) / m
                loss = (loss * (m - 1) + ls) / m
            if k >= m:
                out[RSI][k] = 100.0 if loss == 0.0 else (0.0 if gain == 0.0 else 100.0 - 100.0 / (1.0 + gain / loss))
        # MACD histogram
        a, b = ef.step(p.macd_fast, c[k]), es.step(p.macd_slow, c[k])
        if a is not None and b is not None:
            mline = a - b
            s = esg.step(p.macd_sig, mline)
            if s is not None:
                out[MACD][k] = mline - s
    return out


def pivot_low(l, p, pr):
    if p - pr.left < 0:
        return False
    v = l[p]
    return all(v < l[p - j] for j in range(1, pr.left + 1)) and all(not (v > l[p + j]) for j in range(1, pr.right + 1))


def pivot_high(h, p, pr):
    if p - pr.left < 0:
        return False
    v = h[p]
    return all(v > h[p - j] for j in range(1, pr.left + 1)) and all(not (v < h[p + j]) for j in range(1, pr.right + 1))


def clean(series, q, p, d):
    a, b = series[q], series[p]
    for j in range(q + 1, p):
        line = a + (b - a) * (j - q) / float(p - q)
        v = series[j]
        if v is None:
            return False
        if (v < line) if d > 0 else (v > line):
            return False
    return True


def check(h, l, osc, pr, p, d, prev):
    """DVCheck: the first previous pivot q with enough diverging oscillators; None if none."""
    price = l if d > 0 else h
    for q in prev:
        dist = p - q
        if dist < pr.min_dist:
            continue
        if dist > pr.max_dist:
            break
        pp, pq = price[p], price[q]
        if d > 0:
            typ = 1 if pp < pq else (2 if pp > pq else 0)
        else:
            typ = 1 if pp > pq else (2 if pp < pq else 0)
        if typ == 0 or (typ == 1 and not pr.regular) or (typ == 2 and not pr.hidden):
            continue
        if pr.clean and not clean(price, q, p, d):
            continue
        mask = cnt = 0
        for o in range(2):
            if not pr.use[o]:
                continue
            op, oq = osc[o][p], osc[o][q]
            if op is None or oq is None:
                continue
            div = d * (op - oq) > 0 if typ == 1 else d * (op - oq) < 0
            if not div:
                continue
            if pr.clean and not clean(osc[o], q, p, d):
                continue
            mask |= 1 << o
            cnt += 1
        if cnt >= max(1, pr.min_count):
            return dict(dir=d, type=typ, mask=mask, count=cnt, p=p, q=q, ppx=pp, qpx=pq)
    return None


def run(h, l, c, pr: Params, start: int = 0, stop: int | None = None):
    """Feed bars start..stop-1 (closed bars) as the indicator does; events keyed by the confirming bar index."""
    stop = len(c) if stop is None else stop
    h, l, c = (list(map(float, x[start:stop])) for x in (h, l, c))
    osc = oscillators(c, pr)
    pl, ph = [], []
    events = {}
    for k in range(len(c)):
        p = k - pr.right
        if p < 0:
            continue
        ev = None
        if pivot_low(l, p, pr):
            ev = check(h, l, osc, pr, p, 1, pl)
            pl.insert(0, p)
            del pl[4:]
        is_ph = pivot_high(h, p, pr)
        if ev is None and is_ph:
            ev = check(h, l, osc, pr, p, -1, ph)
        if is_ph:
            ph.insert(0, p)
            del ph[4:]
        if ev is not None:
            ev = {**ev, "p": ev["p"] + start, "q": ev["q"] + start}
            events[k + start] = ev
    full = [[None] * start + o for o in osc]
    return events, full
