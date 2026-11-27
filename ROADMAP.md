# Docket roadmap

## The thesis

Feature flags decoupled deploy from release. Experimentation platforms decide
which variant looks better. But the **release decision** — ship, wait, roll
back — is still a person staring at a dashboard.

The gap is not another dashboard. It is an open decision layer for releases:

- read the evidence the flag platform already has;
- apply a pre-registered policy;
- mutate the flag only when the evidence clears it;
- watch the guardrails after the flag flips;
- leave an audit-grade record of every decision.

Enterprises buy pieces of this from closed platforms. Open-source teams
self-hosting Flagsmith, Unleash or OpenFeature have flags and statistics, but no
decision layer. That is the gap Docket is built to fill.

## What exists today

- `docket audit` — pre-flight wiring checks: warehouse, rollout, variation
  keys, control value, metric events, events URL, dead arms, conversions, SRM.
- `docket gate` — seven-state decision engine with sample floors, SRM,
  credible intervals and guardrail enforcement. Exit 0 is the only yes.
- `docket ship` — completes the experiment, writes the winning variation to
  100%, and writes a JSON + Markdown decision record. `--confirm` is required.
- `docket verify` — HMAC-SHA256 attestation; any edit to the record invalidates
  it.
- `docket ledger list|verify` — append decisions to a JSONL ledger and verify
  every signed record offline.
- `docket release check|ship` — aggregate several experiments into one release
  docket, pre-validate every mutation, and sign one release record.
- A one-command self-hosted demo, a real Python SDK sample app, a GitHub Action,
  and tests that run without network.

This is the first vertical slice: evidence → decision → mutation → record. A
release docket groups several experiments into one decision, so the unit of
release matches how teams actually ship.

## Phase 1 — Release guard (next)

The decision does not end when the flag flips. The biggest missing piece is what
happens after `ship`:

- watch a guardrail metric after the rollout and compare it to the pre-release
  baseline;
- on a regression, either pause the rollout, roll back to the control value, or
  open an approval request — never silently continue;
- record the guard decision and the rollback mutation in the same ledger.

Technical shape:

- a `docket guard` command with `--once` for CI/cron and a `--watch` loop for a
  long-running process;
- provider adapters for post-release metrics, because Flagsmith freezes
  experiment results when an experiment completes;
- webhook and CI integrations so a rollback can be a PR, a deploy, or a flag
  mutation;
- explicit human approval for irreversible actions; automatic rollback only for
  a pre-declared, bounded policy.

Success is measured in prevented bad releases, not in flags flipped.

## Phase 2 — Release ledger (local ledger shipped)

A decision record is only evidence if it is durable and queryable.

- append-only ledger of signed decision records, keyed by experiment, feature,
  environment, commit and deploy (local JSONL shipped; remote next);
- `docket ledger list|verify` with JSON output for CI and auditors;
- attach the record to the pull request and the deploy automatically;
- export to an object store or a warehouse; verify offline with the signing key.

This is the “git blame for releases” layer: who shipped what, on which numbers,
with which mutation, and can the record still be trusted.

## Phase 3 — Policy as code

The seven states are the statistical core. Teams also need organizational rules:

- minimum sample and conversion floors per metric;
- guardrail bands and tolerances per metric;
- freeze windows (Friday afternoons, launch weeks, incidents);
- required approvers and separation of duties;
- maximum rollout percentage before a second approval;
- allow/deny lists for experiment types and environments.

Declare them once in `release.policy.toml`, evaluate them in CI and locally, and
make the decision reproducible. Think “OPA for releases”, not a new approval UI.

## Phase 4 — OpenFeature adapter

Flagsmith is the first provider because it is where the experimentation API and
the free-tier governance gap are clearest. The decision engine should not be
locked to it.

- implement an OpenFeature provider interface for evidence reads and flag
  mutations;
- add adapters for Flagsmith, Unleash, LaunchDarkly and GrowthBook;
- keep provider-specific capabilities explicit rather than pretending every
  platform returns the same statistics;
- normalize only what the decision engine needs: arms, intervals, exposures,
  conversions, guardrails, mutation handles.

Distribution comes from being the neutral decision layer, not from owning the
flag platform.

## Phase 5 — Agent-native releases

Coding agents can write the change, but they should not be the final authority on
shipping it.

- expose `audit`, `gate`, `guard`, `ship` and `verify` through an MCP server and
  a small JSON CLI;
- let an agent propose a release, attach the evidence and stop at a deterministic
  gate;
- require a human approval for irreversible actions;
- return stable exit codes and machine-readable reasons so an agent can react
  without guessing.

This is the 2026 shape: agents do the work, humans own the consequences.

## Design principles

1. **Refusal is the product.** A gate that only says yes is a dashboard with
   extra steps.
2. **Every state carries its numbers.** No unexplained red or green.
3. **A decision is a record, not a log line.** Sign it, attach it, verify it.
4. **Never auto-ship without an explicit human or a pre-declared bounded policy.**
5. **Prefer composition over a platform.** One CLI, one Action, one compose file;
   integrate with the tools teams already run.
6. **Be honest about snapshots.** Flagsmith throttles result refreshes; Docket
   treats results as snapshots keyed by `as_of`, never as live data.

## Non-goals

- A replacement for the Flagsmith UI or a new experimentation engine.
- A traffic simulator sold as a product.
- A clone of paid change-request/approval workflows.
- Multi-experiment orchestration as a first-class feature.
- An auto-ship bot with no human or policy boundary.

## 30-day plan

- Week 1: ledger schema, signed records in CI, `docket ledger`.
- Week 2: `docket guard --once` and rollback policy for Flagsmith; a real
  post-release metric source.
- Week 3: policy-as-code, OpenFeature provider interface, one second adapter.
- Week 4: three design partners on self-hosted Flagsmith; a case study with a
  prevented bad release; MCP wrapper.

Metrics that matter: bad releases prevented, time from “experiment finished” to
“decision recorded”, percentage of releases with a verified decision record, and
the number of teams that keep the gate in CI after a month.

## Contributing

The repo is MIT. The most useful contributions right now are provider adapters,
post-release metric sources, and adversarial tests for the decision engine. If
you run self-hosted Flagsmith and have an experiment that Docket gets wrong,
open an issue with the payload; that is the highest-value bug report.