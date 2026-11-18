"""gta6-twitch-ccu-forecast-01 entry (Python 3.13, standard library only).

Forecasts the Twitch concurrent viewers of a game's category in hours 2..24
of its release day (UTC), as five quantiles per hour, from a case file that
holds everything recorded up to the end of hour 1: that hour's print, the
pre-release history, and a set of past launches ("comparables") with their
hourly curves.

The model is fitted at runtime from the case file itself. Each comparable
contributes the log-ratio curve log(viewers at hour h / viewers at hour 1)
of its own release day. The curves are pooled per hour with weights that
favour launches like this one (console-only, rolling midnight-local unlock,
no early access, similar hour-1 size), and the weighted median and
interquartile range of the pooled log-ratios give the forecast's centre and
its asymmetric spread around hour 1's print. The case's own last pre-release
hour nudges the early hours, and if no comparable is usable the program
falls back to persistence (every hour at hour 1's count, with a spread that
widens like a random walk's).

How the grader runs it: `python3 -P -s /bot/main.py` in python:3.13-slim,
standard library only, no network. It reads every <id>.in in the input
directory (MAKEFUN_INPUT_DIR, else /input) and writes <id>.out to the output
directory (MAKEFUN_OUTPUT_DIR, else /output): {"hours": [{"hour": h,
"q": [q05, q25, q50, q75, q95]} ...]} with exactly 23 rows, hours 2..24,
each q five finite numbers above 0 and at most 1e9, non-decreasing.
"""

import json
import math
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# The grader's -P leaves /bot off sys.path: put it back first, so a module
# shipped beside this file could be imported.
sys.path.insert(0, str(Path(__file__).resolve().parent))

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

# The quantile levels the contest scores, in the order each row lists them.
LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
# The standard normal's quantile at each level.
Z = [-1.645, -0.674, 0, 0.674, 1.645]

# Pool weights for the comparables: launches like this one count more.
W_EARLY_ACCESS = 0.3    # its release day follows an early-access window
W_WEAK_TIER = 0.5       # the case file's own notes call it a weak analogue
W_CONSOLE_ONLY = 1.5    # no PC version at launch, as with GTA VI
W_MIDNIGHT = 1.3        # a rolling midnight-local unlock, as with GTA VI
W_SIMULTANEOUS = 0.4    # one simultaneous global unlock time
W_TINY_HOUR1 = 0.2      # hour 1 under 2% of its day peak: barely out yet
W_SMALL_HOUR1 = 0.6     # hour 1 under 8% of its day peak
# Gaussian kernel bandwidth, in log10 units, on hour-1 viewers: launches of
# a similar size to this one dominate the pool.
SIZE_BANDWIDTH = 0.45
# The pooled median log-ratio is shrunk toward persistence (0) by this
# factor, easing from the first forecast hour to the last. The easing is
# mild: leave-one-out scoring over the case file's own comparables showed
# the pooled median only mildly overshoots for launches like this one,
# while a stronger shrink badly under-forecasts the launches whose curves
# keep climbing after hour 1.
MED_SHRINK_START = 0.95
MED_SHRINK_END = 0.9
# Spread: the pooled IQR gives the sigmas, scaled, and floored against the
# persistence baseline's random-walk spread of 0.15 per square-root hour.
# The same leave-one-out scoring recalibrated the pair: the pooled IQR
# overstates the width of the centre band (hence the 0.65 scale), but it
# stays flat across the day while real curves keep spreading, so the
# random-walk floor has to bind harder in the late hours (0.7 of it).
SIG_SCALE = 0.65
SIG_FLOOR_FRAC = 0.7
BASE_SIGMA = 0.15
# The case's own pre-release momentum: if the last whole hour before the
# day ran above hour 1 (log ratio pre_last), the early hours are nudged by
# TREND_BETA * pre_last, fading to nothing at hour 9.
TREND_BETA = -0.3
TREND_LAST_HOUR = 9


def round2(x: float) -> float | int:
    """x rounded to 2 decimals, half up, as JavaScript's Math.round(x * 100) / 100 rounds it.

    A whole number comes back as an int, so json.dumps writes 1240 where JavaScript writes 1240 (not 1240.0).
    """
    scaled = x * 100
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 100
    return int(value) if value.is_integer() else value


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def day_curve(comp: dict) -> list[float] | None:
    """A comparable's release-day viewers, hours 1..24, or None if unusable.

    Points are the comparable's hourly rows inside its release day; gaps are
    filled by interpolation in log space. A comparable with no hour-1 point
    or a mostly empty day cannot anchor a ratio curve and is dropped.
    """
    start = parse_ts(comp["day_start"])
    pts: dict[int, float] = {}
    for p in comp.get("hourly") or []:
        try:
            delta = (parse_ts(p["from"]) - start).total_seconds() / 3600.0
            v = p.get("viewers")
        except (KeyError, TypeError, ValueError):
            continue
        if v is not None and 0 <= delta < 24 and delta == int(delta):
            pts[int(delta) + 1] = max(1.0, float(v))
    if 1 not in pts or len(pts) < 18:
        return None
    hours = sorted(pts)
    out = []
    for h in range(1, 25):
        if h in pts:
            out.append(pts[h])
            continue
        lo = max((x for x in hours if x < h), default=None)
        hi = min((x for x in hours if x > h), default=None)
        if lo is None or hi is None:
            return None
        f = (h - lo) / (hi - lo)
        out.append(math.exp(math.log(pts[lo]) + f * (math.log(pts[hi]) - math.log(pts[lo]))))
    return out


def comp_features(comp: dict) -> dict | None:
    """Weighting features for one comparable, or None if it is not a launch.

    The one comparable that is the category's own pre-release history spans
    months, not the 17 days of a launch window; the span test drops it.
    """
    hourly = comp.get("hourly") or []
    if len(hourly) < 2:
        return None
    try:
        span_days = (parse_ts(hourly[-1]["from"]) - parse_ts(hourly[0]["from"])).total_seconds() / 86400.0
    except (KeyError, TypeError, ValueError):
        return None
    if span_days > 20:
        return None
    curve = day_curve(comp)
    if curve is None:
        return None
    unlock = (comp.get("unlock") or "").lower()
    platforms = comp.get("platforms") or []
    plat_text = " ".join(platforms).lower() if isinstance(platforms, list) else str(platforms).lower()
    return {
        "curve": curve,
        "log_v1": math.log10(curve[0]),
        "v1_frac": curve[0] / max(curve),
        "early_access": "early access" in unlock or "early-access" in unlock,
        "weak_tier": "tier: weak" in unlock,
        "console_only": "pc" not in plat_text,
        "midnight": "midnight" in unlock,
        "simultaneous": (
            "simultaneous" in unlock or "single global unlock" in unlock or "single simultaneous" in unlock
        ),
    }


def base_weight(f: dict) -> float:
    w = 1.0
    if f["early_access"]:
        w *= W_EARLY_ACCESS
    if f["weak_tier"]:
        w *= W_WEAK_TIER
    if f["console_only"]:
        w *= W_CONSOLE_ONLY
    if f["midnight"]:
        w *= W_MIDNIGHT
    if f["simultaneous"]:
        w *= W_SIMULTANEOUS
    if f["v1_frac"] < 0.02:
        w *= W_TINY_HOUR1
    elif f["v1_frac"] < 0.08:
        w *= W_SMALL_HOUR1
    return w


def weighted_quantile(pairs: list[tuple[float, float]], tau: float) -> float:
    """The tau quantile of (value, weight) pairs, by inverse-CDF interpolation."""
    pairs = sorted(pairs)
    total = sum(w for _, w in pairs)
    if total <= 0:
        return pairs[len(pairs) // 2][0]
    target = tau * total
    acc = 0.0
    prev_v, prev_cdf = pairs[0][0], 0.0
    for v, w in pairs:
        acc += w
        if acc >= target:
            if acc == w:
                return v
            frac = (target - prev_cdf) / w
            return prev_v + frac * (v - prev_v)
        prev_v, prev_cdf = v, acc
    return pairs[-1][0]


def smooth(vals: list[float], radius: int = 1) -> list[float]:
    out = []
    for i in range(len(vals)):
        lo = max(0, i - radius)
        hi = min(len(vals), i + radius + 1)
        out.append(sum(vals[lo:hi]) / (hi - lo))
    return out


def pre_last_ratio(case: dict, p1: float) -> float | None:
    """log(last whole hour before the day / hour 1), from the case's own history.

    Only an hour inside the three before the day starts counts; with none,
    the early-hours nudge is skipped.
    """
    try:
        start = parse_ts(case["day_start"])
    except (KeyError, TypeError, ValueError):
        return None
    best = None
    for row in case.get("history_hourly") or []:
        try:
            t = parse_ts(row["from"])
            v = row.get("viewers")
        except (KeyError, TypeError, ValueError):
            continue
        if v is None:
            continue
        delta = (t - start).total_seconds() / 3600.0
        if -3 <= delta < 0 and (best is None or t > best[0]):
            best = (t, max(1.0, float(v)))
    if best is None:
        return None
    return math.log(best[1] / p1)


def baseline_row(p1: float, h: int) -> list[float | int]:
    """The persistence forecast of hour h: hour 1's count, a widening spread."""
    return [round2(p1 * math.exp(z * BASE_SIGMA * math.sqrt(h - 1))) for z in Z]


def model_rows(case: dict, p1: float, hours: list[int]) -> dict[int, list[float | int]] | None:
    """The pooled-comparables forecast, or None when no comparable is usable."""
    pool = []
    for comp in case.get("comparables") or []:
        f = comp_features(comp)
        if f is not None:
            pool.append(f)
    if not pool:
        return None
    log_p1 = math.log10(p1)
    weights = [
        base_weight(f) * math.exp(-(((f["log_v1"] - log_p1) / SIZE_BANDWIDTH) ** 2)) for f in pool
    ]
    cols: dict[float, list[float]] = {tau: [] for tau in LEVELS}
    for h in hours:
        pairs = [(math.log(f["curve"][h - 1] / f["curve"][0]), w) for f, w in zip(pool, weights)]
        for tau in LEVELS:
            cols[tau].append(weighted_quantile(pairs, tau))
    for tau in LEVELS:
        cols[tau] = smooth(cols[tau], 1)
    pre_last = pre_last_ratio(case, p1)
    rows: dict[int, list[float | int]] = {}
    for i, h in enumerate(hours):
        lam = MED_SHRINK_START + (MED_SHRINK_END - MED_SHRINK_START) * (h - hours[0]) / (hours[-1] - hours[0])
        med = cols[0.5][i] * lam
        sig_lo = max(0.05, (cols[0.5][i] - cols[0.25][i]) / 0.674)
        sig_hi = max(0.05, (cols[0.75][i] - cols[0.5][i]) / 0.674)
        floor = SIG_FLOOR_FRAC * BASE_SIGMA * math.sqrt(h - 1)
        sig_lo = max(sig_lo * SIG_SCALE, floor)
        sig_hi = max(sig_hi * SIG_SCALE, floor)
        row = [med - 1.645 * sig_lo, med - 0.674 * sig_lo, med, med + 0.674 * sig_hi, med + 1.645 * sig_hi]
        if pre_last is not None and h <= TREND_LAST_HOUR:
            adj = TREND_BETA * pre_last * (TREND_LAST_HOUR - h) / (TREND_LAST_HOUR - 2)
            row = [x + adj for x in row]
        row = sorted(row)
        qs = []
        prev = 0.0
        for x in row:
            v = round2(p1 * math.exp(x))
            v = min(max(float(v), 0.01), 1_000_000_000.0)
            v = max(v, prev)
            prev = v
            qs.append(int(v) if float(v).is_integer() else v)
        rows[h] = qs
    return rows


def forecast_case(case: dict, name: str) -> dict:
    if case["levels"] != LEVELS:
        raise ValueError(f"{name} asks for levels {case['levels']}, not {LEVELS}")
    known = case["known"]
    if isinstance(known, dict):
        known = [known]
    first = next((k for k in known if k["hour"] == 1), None)
    if first is None:
        raise ValueError(f"{name} has no hour-1 print")
    p1 = max(1.0, float(first["viewers"]))
    hours = [row["hour"] for row in case["forecast"]]
    rows = model_rows(case, p1, hours)
    if rows is None:
        rows = {h: baseline_row(p1, h) for h in hours}
    return {"hours": [{"hour": h, "q": rows[h]} for h in hours]}


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Every case file, in name order, so two runs write the same bytes.
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = forecast_case(case, path.name)
        (OUTPUT_DIR / f"{path.stem}.out").write_text(
            json.dumps(forecast, separators=(",", ":")) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
