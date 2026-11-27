"""Standalone HTML report: one file, no server, no assets, openable anywhere."""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone

STATE_COLOURS = {
    "winner": "#2f6347",
    "collecting": "#8a6a1f",
    "inconclusive": "#8a6a1f",
    "losing": "#b33127",
    "broken": "#b33127",
    "guardrail": "#b33127",
    "unreachable": "#5d6159",
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


def render(docket: dict, checks: list[dict] | None, experiment: int,
           environment: str, dashboard: str = "http://localhost:8000",
           project: int = 1) -> str:
    state = docket.get("state", "unreachable")
    colour = STATE_COLOURS.get(state, "#8b949e")
    arms = docket.get("arms") or {}
    inf = docket.get("inference") or {}
    leader = docket.get("winner") or ""
    leader_inf = inf.get(leader) or {}
    reasons = docket.get("reasons") or []

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
    link = f"{dashboard}/project/{project}/environment/{environment}/experiments/{experiment}"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Docket · experiment {experiment}</title>
<style>
  @font-face {{ font-family:"Anton"; src:url("../assets/fonts/anton.woff2") format("woff2"); font-weight:400; }}
  @font-face {{ font-family:"Newsreader"; src:url("../assets/fonts/newsreader.woff2") format("woff2"); font-weight:400 600; }}
  @font-face {{ font-family:"DocketMono"; src:url("../assets/fonts/jetbrains-mono-latin.woff2") format("woff2"); font-weight:400 500; }}
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:#efe7d6; color:#151714;
         font:16px/1.65 "Newsreader",Georgia,serif; }}
  main {{ max-width:880px; margin:0 auto; padding:56px 28px 96px; }}
  .kicker {{ font:400 9px/1 "DocketMono",monospace; letter-spacing:.16em; text-transform:uppercase; color:#5d6159; margin:0 0 10px; }}
  h1 {{ font:400 20px/1.2 "Anton",Impact,sans-serif; letter-spacing:-.02em;
        margin:0 0 8px; }}
  .state {{ font:400 64px/.86 "Anton",Impact,sans-serif;
            letter-spacing:-.05em; color:{colour}; margin:0 0 28px; }}
  .card {{ border-top:1px solid #c9c0ad; padding:20px 0 22px; margin:0; }}
  .card h2 {{ font:400 9px/1 "DocketMono",monospace; letter-spacing:.14em; text-transform:uppercase; color:#5d6159;
              margin:0 0 14px; }}
  table {{ width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums; }}
  th,td {{ text-align:left; padding:8px 8px 8px 0; border-bottom:1px solid #d6cbb6; }}
  th {{ color:#5d6159; font-weight:400; font-size:9px; font-family:"DocketMono",monospace; text-transform:uppercase; letter-spacing:.1em; }}
  code {{ font-family:"DocketMono","SFMono-Regular",Menlo,monospace; font-size:12px; }}
  .bar {{ position:relative; height:28px; margin:10px 0 4px; background:#d7ccb7;
          overflow:hidden; }}
  .zero {{ position:absolute; top:0; bottom:0; width:1px; background:#151714; opacity:.55; }}
  .range {{ position:absolute; top:8px; height:12px; background:{colour}; opacity:.45; }}
  .point {{ position:absolute; top:2px; width:2px; height:24px; background:{colour}; }}
  .scale {{ display:flex; justify-content:space-between; color:#5d6159; font:400 9px/1.5 "DocketMono",monospace; }}
  ul {{ margin:0; padding-left:18px; }}
  li {{ margin:4px 0; }}
  .pass td:first-child {{ color:#2f6347; }}
  .warn td:first-child {{ color:#8a6a1f; }}
  .block td:first-child {{ color:#b33127; }}
  .muted {{ color:#5d6159; }}
  footer {{ margin-top:28px; padding-top:14px; border-top:1px solid #c9c0ad; color:#5d6159; font:400 9px/1.7 "DocketMono",monospace; }}
  a {{ color:#151714; text-decoration:none; border-bottom:1px solid #b33127; }}
</style></head><body><main>
  <p class="kicker">Docket / decision report</p>
  <h1>Experiment {experiment}</h1>
  <p class="state">{state}</p>

  <section class="card">
    <h2>Decision</h2>
    <ul>{reason_html or '<li class="muted">no reasons recorded</li>'}</ul>
    <p class="muted">as of {html.escape(str(docket.get('as_of') or 'unknown'))}
      · exit code {docket.get('exit_code')}
      {f"· roughly {docket['more_traffic_needed']} more identities on the leader" if docket.get('more_traffic_needed') else ""}</p>
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
    docket is a snapshot, not a live query
  </footer>
</main></body></html>
"""
