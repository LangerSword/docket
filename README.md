# Verdict

Release gates for Flagsmith experiments. Audit the wiring, refuse to ship on noise,
and roll out the winner with a decision record that says why.

Flagsmith tells you a win probability. Verdict tells you whether you are allowed to
ship, and refuses with the numbers when you are not.

```
$ verdict gate --env <env key> --experiment 7

VERDICT: INCONCLUSIVE  (exit 1)
  leading variant: red
  - red: lift +17.6%, 95% interval [-52.6%, +87.9%], chance to win 68.9%
  - interval still crosses zero; not enough traffic to call it
  rough extra identities needed on the leader: ~1783
```

## Why

A dashboard cannot answer "may this release proceed". It shows a number and leaves
the judgement to whoever is awake. That fails in three documented ways:

1. Instrumentation lies silently: a variation with no key records no exposures, an
   SDK pointed at Flagsmith Cloud instead of your instance sends events nowhere, and
   a mistyped event name leaves conversions at zero. The dashboard looks healthy.
2. A point estimate is not a decision. An interval that crosses zero is not a win,
   whatever colour the number is.
3. The decision evaporates. Nothing records who shipped what, on which numbers.

Verdict fixes all three, on self-hosted Flagsmith, in one binary and one GitHub Action.

## Install

```bash
pipx install "git+https://github.com/LangerSword/verdict"
# or
pip install "git+https://github.com/LangerSword/verdict"
```

Python 3.11 or newer. No dependencies outside the standard library.

## Quickstart

You need a Flagsmith instance with Experimentation enabled and a running experiment.
Self-host it in one command:

```bash
curl -fsSL https://raw.githubusercontent.com/Flagsmith/flagsmith/main/docker-compose.yml -o docker-compose.yml
docker compose up -d
```

Then:

```bash
export VERDICT_TOKEN=<api token from your Flagsmith user>
verdict init            # writes verdict.toml after asking three questions
verdict audit           # check the wiring before you trust any number
verdict gate            # the decision, with exit codes for CI
verdict ship --confirm  # only runs when the verdict is WINNER
verdict report          # standalone HTML you can attach to a PR
```

`VERDICT_TOKEN` is read from the environment. The tool never writes a secret to disk.

## The seven states

| State | Meaning | Exit |
|---|---|---|
| `winner` | interval excludes zero in the expected direction, chance to win above the threshold, guardrails clean | 0 |
| `collecting` | below the floors (50 identities, 5 conversions per arm); there is no result yet | 1 |
| `inconclusive` | interval still crosses zero; prints roughly how much more traffic is needed | 1 |
| `losing` | the treatment loses; do not ship | 2 |
| `guardrail` | the winner regressed another metric beyond tolerance | 2 |
| `broken` | sample ratio mismatch, a dead arm, or no exposures; the data cannot be trusted | 2 |
| `unreachable` | API, auth, or results not computed yet | 3 |

Exit codes are the contract. `0` is the only value that allows a ship.

## Audit checks

Pre-flight: warehouse connected, experiment running, rollout above zero, every variation
keyed, control variant present, metric event has actually arrived in the warehouse, the
SDK events URL targets your instance.

Live: both arms receiving exposures, conversions firing once the floor is met, sample
ratio mismatch computed from your configured split, results freshness.

Each failure prints the fix, not just the symptom.

## Configuration

`verdict init` writes this. Edit it by hand at any time:

```toml
[flagsmith]
base = "http://localhost:8000"
environment = "<environment key>"

[experiment]
id = 7
dashboard = "http://localhost:8000"

[rule]
n_min = 50
c_min = 5
win_threshold = 0.95
expected_direction = "increase"     # or "decrease"
weights = { control = 50, red = 50 } # for the sample ratio check
```

Guardrails are declared per metric:

```toml
[rule]
guardrails = [
  { metric_id = 2, name = "revenue", expected_direction = "not_decrease", tolerance = 0.01 },
]
```

## GitHub Action

```yaml
- uses: LangerSword/verdict/action@v1
  with:
    base: http://localhost:8000
    token: ${{ secrets.FLAGSMITH_TOKEN }}
    env: ${{ vars.FLAGSMITH_ENV_KEY }}
    experiment: "42"
    events-url: http://localhost:8000/api
```

It runs the audit, gates the release, fails the job on anything that is not `winner`,
and comments the verdict on the pull request.

## What it does not do

It does not create experiments, generate traffic, replace the Flagsmith UI, or approve
changes on your behalf. `ship` requires `--confirm`, refuses a completed experiment, and
records the exact mutation it performed.

## Licence

MIT.
