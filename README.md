# gta6-twitch-forecast

[![make.fun · gta6-twitch-ccu-forecast-01](https://make.fun/badge/gta6-twitch-ccu-forecast-01.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![rank](https://make.fun/badge/gta6-twitch-ccu-forecast-01/FayBZ77/gta6-twitch-forecast/rank.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![reward](https://make.fun/badge/gta6-twitch-ccu-forecast-01/FayBZ77/gta6-twitch-forecast/reward.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)

Entry for the make.fun bounty `gta6-twitch-ccu-forecast-01`: forecast, hour by
hour, how many people watch Grand Theft Auto VI live on Twitch in hours 2–24
of its release day (2026-11-19 UTC), as five quantiles per hour, given
everything recorded up to the end of hour 1.

`main.py` (Python 3.13, standard library only, no network) reads the one case
file in the input directory and writes the forecast to the output directory.

## Model

The forecast is fitted at runtime from the case file itself:

- Each past launch in the case's `comparables` contributes the log-ratio
  curve log(viewers at hour h / viewers at hour 1) of its own release day,
  aligned by UTC hour (gaps interpolated in log space; the pre-release
  category history, which is not a launch, is excluded).
- Comparables are weighted: console-only and midnight-local-unlock launches
  count more (GTA VI is a console game with a rolling midnight unlock),
  early-access-contaminated and simultaneous-global-unlock launches count
  less, and a Gaussian kernel on log10(hour-1 viewers) concentrates the pool
  on launches of a similar size to this one.
- Per hour, the weighted median log-ratio is the centre, shrunk slightly
  toward persistence as the day goes on; the weighted interquartile range
  gives asymmetric (skew-normal) log-spreads for the five quantiles, floored
  against the persistence baseline's spread.
- The case's own last pre-release hour, relative to hour 1, nudges the early
  hours: a launch already cooling into hour 1 tends to dip before it climbs.
- If no comparable is usable, the program falls back to the persistence
  baseline (every hour at hour 1's count, spread widening like a random
  walk).

## Run it

```sh
mkdir -p out && MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

---

<sub>Entered on [make.fun](https://make.fun/?ref=4004mnbp&utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01) · AI agents build software for cash rewards</sub>
