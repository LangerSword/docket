"""Standalone HTML report: one file, no server, no assets, openable anywhere."""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone

STATE_COLOURS = {
    "winner": "#2fbf71",
    "collecting": "#e3b341",
    "inconclusive": "#e3b341",
    "losing": "#e5534b",
    "broken": "#e5534b",
    "guardrail": "#e5534b",
    "unreachable": "#8b949e",
}


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.1%}"


def _interval_bar(low: float | None, high: float | None, lift: float | None) -> str:
    if low is None or high is None or lift is None:
        return '<div class="muted">no inference</div>'
    lo, hi = min(low, 0.0), max(high, 0.0)
    span = hi - lo or 1.0
    pos = lambda v: max(0.0, min(100.0, (v - lo) / span * 100))
    zero = pos(0.0)
    return f"""
    <div class="bar">
      <div class="zero" style="left:{zero:.1f}%"></div>
      <div class="range" style="left:{pos(low):.1f}%;width:{max(1.0, pos(high)-pos(low)):.1f}%"></div>
      <div class="point" style="left:{pos(lift):.1f}%"></div>
    </div>
    <div class="scale"><span>{_pct(lo)}</span><span>zero</span><span>{_pct(hi)}</span></div>
    """


def render(verdict: dict, checks: list[dict] | None, experiment: int,
           environment: str, dashboard: str = "http://localhost:8000") -> str:
    state = verdict.get("state", "unreachable")
    colour = STATE_COLOURS.get(state, "#8b949e")
    arms = verdict.get("arms") or {}
    inf = verdict.get("inference") or {}
    leader = verdict.get("winner") or ""
    leader_inf = inf.get(leader) or {}
    reasons = verdict.get("reasons") or []

    arm_rows = "".join(
        f"<tr><td><code>{html.escape(k)}</code></td><td>{v['n']}</td>"
        f"<td>{v['total']:g}</td><td>{v['mean']:.2%}</td></tr>"
        for k, v in sorted(arms.items())
    )
    check_rows = "".join(
        f"<tr class='{c['status']}'><td>{c['status'].upper()}</td>"
        f"<td>{html.escape(c['name'])}</td><td>{html.escape(c['detail'])}</td></tr>"
        for c in (checks or [])
    )
    reason_html = "".join(f"<li>{html.escape(r)}</li>" for r in reasons)
    link = f"{dashboard}/project/1/environment/{environment}/experiments/{experiment}"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Verdict · experiment {experiment}</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:#0d1117; color:#e6edf3;
         font:15px/1.6 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif; }}
  main {{ max-width:820px; margin:0 auto; padding:56px 24px 96px; }}
  h1 {{ font-size:15px; letter-spacing:.14em; text-transform:uppercase; color:#8b949e;
        font-weight:600; margin:0 0 8px; }}
  .state {{ font-size:44px; font-weight:700; letter-spacing:-.02em; color:{colour}; margin:0 0 28px; }}
  .card {{ border:1px solid #21262d; border-radius:10px; padding:20px 22px; margin:0 0 18px;
           background:#0f141b; }}
  .card h2 {{ font-size:12px; letter-spacing:.12em; text-transform:uppercase; color:#8b949e;
              margin:0 0 14px; font-weight:600; }}
  table {{ width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums; }}
  th,td {{ text-align:left; padding:7px 6px; border-bottom:1px solid #1c2128; }}
  th {{ color:#8b949e; font-weight:500; font-size:12px; text-transform:uppercase; letter-spacing:.08em; }}
  code {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:13px; }}
  .bar {{ position:relative; height:34px; margin:10px 0 4px; background:#161b22;
          border-radius:6px; overflow:hidden; }}
  .zero {{ position:absolute; top:0; bottom:0; width:1px; background:#e6edf3; opacity:.55; }}
  .range {{ position:absolute; top:11px; height:12px; background:{colour}; opacity:.35; border-radius:3px; }}
  .point {{ position:absolute; top:4px; width:3px; height:26px; background:{colour}; border-radius:2px; }}
  .scale {{ display:flex; justify-content:space-between; color:#8b949e; font-size:12px; }}
  ul {{ margin:0; padding-left:18px; }}
  li {{ margin:4px 0; }}
  .pass td:first-child {{ color:#2fbf71; }}
  .warn td:first-child {{ color:#e3b341; }}
  .block td:first-child {{ color:#e5534b; }}
  .muted {{ color:#8b949e; }}
  footer {{ margin-top:32px; color:#8b949e; font-size:12px; }}
  a {{ color:#58a6ff; text-decoration:none; }}
</style></head><body><main>
  <h1>Verdict · experiment {experiment}</h1>
  <p class="state">{state}</p>

  <section class="card">
    <h2>Decision</h2>
    <ul>{reason_html or '<li class="muted">no reasons recorded</li>'}</ul>
    <p class="muted">as of {html.escape(str(verdict.get('as_of') or 'unknown'))}
      · exit code {verdict.get('exit_code')}
      {f"· roughly {verdict['more_traffic_needed']} more identities on the leader" if verdict.get('more_traffic_needed') else ""}</p>
  </section>

  <section class="card">
    <h2>Leader vs control</h2>
    {_interval_bar(leader_inf.get('ci_low'), leader_inf.get('ci_high'), leader_inf.get('lift'))}
    <p class="muted">lift {_pct(leader_inf.get('lift'))} ·
       chance to win {"n/a" if leader_inf.get('chance_to_win') is None else f"{leader_inf['chance_to_win']:.1%}"}</p>
  </section>

  <section class="card">
    <h2>Arms</h2>
    <table><thead><tr><th>variant</th><th>identities</th><th>conversions</th><th>rate</th></tr></thead>
    <tbody>{arm_rows}</tbody></table>
  </section>

  <section class="card">
    <h2>Audit</h2>
    <table><thead><tr><th></th><th>check</th><th>detail</th></tr></thead>
    <tbody>{check_rows or '<tr><td colspan="3" class="muted">audit not run for this report</td></tr>'}</tbody></table>
  </section>

  <footer>
    <a href="{html.escape(link)}">open in Flagsmith</a> ·
    generated {datetime.now(timezone.utc).isoformat(timespec="seconds")} ·
    verdict is a snapshot, not a live query
  </footer>
</main></body></html>
"""
