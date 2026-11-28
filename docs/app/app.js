(function () {
  "use strict";

  var API = String(window.DOCKET_API || "http://127.0.0.1:8765").replace(/\/+$/, "");
  var state = {
    mode: "demo",
    demo: null,
    docket: null,
    record: null,
    index: 0,
    live: null,
    busy: false,
    demoConfig: "",
    verifyText: "",
    recordNote: "",
    configTouched: false,
    flow: "decide",
    checkTimer: 0,
    toastTimer: 0
  };

  var $ = function (id) { return document.getElementById(id); };
  // Every dynamic string inserted with innerHTML passes through esc().
  var esc = function (value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  };
  var pct = function (value, digits) {
    if (value == null || Number.isNaN(Number(value))) return "n/a";
    var n = Number(value) * 100;
    return (n >= 0 ? "+" : "") + n.toFixed(digits == null ? 1 : digits) + "%";
  };
  var num = function (value) {
    if (value == null || Number.isNaN(Number(value))) return "n/a";
    return Number(value).toLocaleString("en-US");
  };
  var stateClass = function (value) {
    var key = String(value || "unreachable").toLowerCase();
    return ["winner", "guardrail", "blocked", "broken", "losing", "collecting", "inconclusive", "unreachable", "not_ready"].indexOf(key) >= 0 ? key : "unreachable";
  };
  var stateLabel = function (value) {
    return String(value || "unreachable").replace(/_/g, " ").toUpperCase();
  };

  function setHelp(text, isError) {
    var el = $("rail-help");
    if (!el) return;
    el.textContent = text;
    el.classList.toggle("error", Boolean(isError));
  }

  function showToast(text, bad) {
    var toast = $("toast");
    if (!toast) return;
    window.clearTimeout(state.toastTimer);
    toast.textContent = text;
    toast.classList.toggle("bad", Boolean(bad));
    toast.hidden = false;
    window.requestAnimationFrame(function () { toast.classList.add("is-visible"); });
    state.toastTimer = window.setTimeout(function () {
      toast.classList.remove("is-visible");
      window.setTimeout(function () { toast.hidden = true; }, 260);
    }, 3600);
  }

  function renderFlow() {
    var order = ["connect", "check", "decide", "ship"];
    var step = order.indexOf(state.flow);
    if (step < 0) step = 0;
    document.querySelectorAll("#flow li").forEach(function (item, index) {
      item.classList.toggle("is-done", index < step);
      item.classList.toggle("is-active", index === step);
      item.classList.toggle("is-locked", order[index] === "ship" && (state.mode === "demo" || !state.docket || state.docket.state !== "cleared"));
    });
    var note = $("flow-note");
    if (note) {
      note.textContent = state.mode === "demo"
        ? "Recorded file"
        : state.live ? "Live / " + state.live.base.replace(/^https?:\/\//, "") : "Not connected";
    }
  }

  function setFlow(step) {
    state.flow = step;
    renderFlow();
  }

  function setChecking(active, title, labels) {
    var layer = $("checking");
    if (!layer) return;
    window.clearInterval(state.checkTimer);
    if (!active) {
      layer.hidden = true;
      $("decision").setAttribute("aria-busy", "false");
      $("decision").classList.remove("is-checking");
      return;
    }
    layer.hidden = false;
    $("decision").setAttribute("aria-busy", "true");
    $("decision").classList.add("is-checking");
    $("checking-title").textContent = title || "Reading the release";
    var steps = Array.prototype.slice.call(document.querySelectorAll("#checking-steps li"));
    steps.forEach(function (item, index) {
      item.textContent = (labels && labels[index]) || item.textContent;
      item.classList.remove("is-on");
    });
    var current = 0;
    steps[0].classList.add("is-on");
    state.checkTimer = window.setInterval(function () {
      current = Math.min(steps.length - 1, current + 1);
      steps.forEach(function (item, index) { item.classList.toggle("is-on", index <= current); });
    }, 480);
  }

  function wait(ms) {
    return new Promise(function (resolve) { window.setTimeout(resolve, ms); });
  }

  function renderPreflight() {
    var preflight = $("preflight");
    if (!preflight) return;
    var show = state.mode === "live" && Boolean(state.live) && !state.docket;
    preflight.hidden = !show;
    $("decision").classList.toggle("is-preflight", show);
    if (!show) return;
    $("preflight-target").textContent = state.live.base.replace(/^https?:\/\//, "");
    $("preflight-env").textContent = state.live.environment;
    var cases = (state.live.config.match(/\[\[experiment\]\]/g) || []).length;
    $("preflight-cases").textContent = String(cases);
  }

  function updateConfirm() {
    var check = $("confirm-check");
    var input = $("confirm-input");
    $("confirm-submit").disabled = !(check && check.checked && input && input.value.trim() === "SHIP");
  }

  function currentCase() {
    if (!state.docket || !state.docket.cases || !state.docket.cases.length) return null;
    return state.docket.cases[Math.min(state.index, state.docket.cases.length - 1)];
  }

  function leaderInfo(verdict) {
    if (!verdict) return {};
    var leader = verdict.winner;
    return (verdict.inference && verdict.inference[leader]) || {};
  }

  function setModeBadge() {
    var badge = $("mode-badge");
    if (!badge) return;
    badge.textContent = state.mode === "live" ? "Live" : "Demo";
    badge.classList.toggle("live", state.mode === "live");
  }

  function renderRail() {
    var release = state.docket;
    $("release-name").textContent = release ? release.release : "checkout-nov-2026";
    $("release-state").textContent = release ? stateLabel(release.state) + " / EXIT " + release.exit_code : "Loading recorded file";
    $("aggregate-copy").textContent = release
      ? (release.cases || []).filter(function (c) { return c.state === "winner"; }).length + " of " + (release.cases || []).length + " cases cleared / exit " + release.exit_code
      : "No docket loaded";

    var list = $("case-list");
    list.innerHTML = "";
    if (!release || !release.cases || !release.cases.length) {
      list.innerHTML = '<p class="side-muted empty-note">No cases returned.</p>';
    } else {
      release.cases.forEach(function (item, index) {
        var button = document.createElement("button");
        button.type = "button";
        button.className = "case-row" + (index === state.index ? " is-active" : "");
        button.setAttribute("aria-current", index === state.index ? "true" : "false");
        var verdict = item.verdict || {};
        var inf = leaderInfo(verdict);
        var note = inf.lift != null
          ? pct(inf.lift) + " lift / " + (item.checks || []).filter(function (c) { return c.status === "pass"; }).length + " checks pass"
          : "No inference returned";
        button.innerHTML =
          '<span class="num">' + esc(item.id) + '</span>' +
          '<span class="name">' + esc(item.label) + '</span>' +
          '<span class="state ' + (item.state === "winner" ? "win" : item.state === "guardrail" ? "refuse" : "") + '">' + esc(stateLabel(item.state)) + '</span>' +
          '<span class="note">' + esc(note) + '</span>';
        button.addEventListener("click", function () {
          var decision = $("decision");
          decision.classList.add("is-changing");
          window.setTimeout(function () {
            state.index = index;
            renderRail();
            renderDecision();
            window.requestAnimationFrame(function () { decision.classList.remove("is-changing"); });
          }, 150);
        });
        list.appendChild(button);
      });
    }

    var ship = $("ship-button");
    ship.disabled = state.busy || state.mode !== "live" || !release || release.state !== "cleared";
    if (state.mode === "demo") {
      ship.textContent = "Ship release";
      ship.title = "The demo is a recorded blocked release. Connect Flagsmith to ship.";
    } else if (!release) {
      ship.title = "Run a live check first.";
    } else if (release.state !== "cleared") {
      ship.title = "The docket is not cleared.";
    } else {
      ship.title = "Ship the cleared release.";
    }
    $("run-button").textContent = state.busy ? "Checking..." : state.mode === "demo" ? "Replay recorded check" : (!release ? "Review preflight" : "Run check");
    $("run-button").disabled = state.busy;
    $("mode-copy").textContent = state.mode === "demo" ? "Recorded demo" : "Live Flagsmith";
    $("source-copy").textContent = state.mode === "demo"
      ? (state.demo && state.demo.source ? state.demo.source : "Recorded against self-hosted Flagsmith.")
      : (state.live ? state.live.base : "Not connected");
    setModeBadge();
  }

  function renderDecision() {
    var item = currentCase();
    var verdict = item && item.verdict ? item.verdict : null;
    var inf = leaderInfo(verdict);
    var checks = (item && item.checks) || [];

    $("case-kicker").textContent = item ? "Case " + item.id + " / " + item.label : "No case";
    $("case-name").textContent = item ? item.label : "No docket loaded";
    var badge = $("case-state");
    badge.textContent = item ? stateLabel(item.state) : "Unreachable";
    badge.className = "state-badge " + (item ? stateClass(item.state) : "unreachable");

    $("case-lift").innerHTML = (inf.lift != null ? pct(inf.lift).replace("%", "") : "n/a") + (inf.lift != null ? "<span>%</span>" : "");
    $("case-lift-label").textContent = item && item.state === "guardrail" ? "primary lift vs control" : "lift vs control";

    var interval = $("interval");
    var band = interval.querySelector(".interval-band");
    if (inf.ci_low != null && inf.ci_high != null && inf.lift != null) {
      var low = Number(inf.ci_low) * 100;
      var high = Number(inf.ci_high) * 100;
      var lift = Number(inf.lift) * 100;
      var lo = Math.min(0, low);
      var hi = Math.max(0, high);
      var span = hi - lo || 1;
      interval.hidden = false;
      interval.style.setProperty("--start", ((low - lo) / span * 100).toFixed(1) + "%");
      interval.style.setProperty("--end", ((high - lo) / span * 100).toFixed(1) + "%");
      interval.style.setProperty("--point", ((lift - lo) / span * 100).toFixed(1) + "%");
      band.classList.toggle("is-guardrail", item && item.state === "guardrail");
      $("interval-caption").textContent = "95% interval " + pct(inf.ci_low) + " to " + pct(inf.ci_high) + " / chance " + (inf.chance_to_win == null ? "n/a" : (Number(inf.chance_to_win) * 100).toFixed(1) + "%");
    } else {
      interval.hidden = true;
      $("interval-caption").textContent = "No inference returned.";
    }

    var reasons = (verdict && verdict.reasons) || [];
    if (!reasons.length) reasons = ["Flagsmith did not return a decision."];
    $("case-reasons").innerHTML = reasons.map(function (reason) {
      return "<p>" + esc(String(reason).replace(/^red:/, "Leading variant red:")) + "</p>";
    }).join("");

    var arms = (verdict && verdict.arms) || {};
    var shoppers = Object.keys(arms).reduce(function (total, key) { return total + (arms[key].n || 0); }, 0);
    var passes = checks.filter(function (c) { return c.status === "pass"; }).length;
    var blocks = checks.filter(function (c) { return c.status === "block"; }).length;
    $("case-stats").innerHTML =
      '<div><span>Chance to win</span><b>' + (inf.chance_to_win == null ? "n/a" : (Number(inf.chance_to_win) * 100).toFixed(1) + "%") + '</b></div>' +
      '<div><span>Shoppers</span><b>' + (shoppers ? num(shoppers) : "n/a") + '</b></div>' +
      '<div><span>Audit</span><b>' + passes + ' pass' + (blocks ? " / " + blocks + " block" : "") + '</b></div>' +
      '<div><span>Exit</span><b>' + (verdict ? verdict.exit_code : 3) + '</b></div>';

    $("checks-summary").textContent = checks.length ? passes + " pass" + (blocks ? " / " + blocks + " block" : "") : "No audit returned";
    $("check-list").innerHTML = checks.length
      ? checks.map(function (check) {
          return '<div class="check-row ' + esc(check.status) + '">' +
            '<b>' + esc(String(check.status).toUpperCase()) + '</b>' +
            '<span>' + esc(check.name) + '</span>' +
            '<em>' + esc(check.detail) + '</em></div>';
        }).join("")
      : '<p class="side-muted empty-note">No audit checks in this response.</p>';

    $("raw-json").textContent = JSON.stringify(item || {}, null, 2);
  }

  function renderRecord() {
    var block = $("record-block");
    var record = state.record;
    if (!record) {
      block.innerHTML = '<p class="side-strong">No record written</p>' +
        '<p class="side-muted">Only a cleared docket mutates flags. A refused release leaves no record.</p>';
      var download = $("download-button");
      var verify = $("verify-button");
      if (download) download.disabled = true;
      if (verify) verify.disabled = true;
      if ($("verify-result")) $("verify-result").textContent = "";
      return;
    }
    var signature = record.signature || {};
    var aggregate = record.aggregate || {};
    block.innerHTML =
      '<p class="side-strong">' + esc(record.release || "release") + '</p>' +
      '<dl class="record-mini">' +
        '<div><span>State</span><b>' + esc(aggregate.state || "unknown") + ' / exit ' + esc(aggregate.exit_code) + '</b></div>' +
        '<div><span>Key</span><b>' + esc(signature.key_id || "unsigned") + '</b></div>' +
        '<div><span>Experiments</span><b>' + esc((record.experiments || []).length) + '</b></div>' +
      '</dl>' +
      '<p class="side-note">' + esc(state.recordNote || "Signed record.") + '</p>' +
      '<div class="record-actions">' +
        '<button class="button button-paper small" id="download-button" type="button">Download JSON</button>' +
        '<button class="button button-line small" id="verify-button" type="button">Verify</button>' +
      '</div>' +
      '<p class="side-note' + (state.verifyText ? " ok" : "") + '" id="verify-result">' + esc(state.verifyText) + '</p>';
    $("download-button").addEventListener("click", downloadRecord);
    $("verify-button").addEventListener("click", verifyRecord);
  }

  function renderAll() {
    renderRail();
    renderDecision();
    renderRecord();
    renderFlow();
    renderPreflight();
  }

  function downloadRecord() {
    if (!state.record) return;
    var blob = new Blob([JSON.stringify(state.record, null, 2)], { type: "application/json" });
    var url = URL.createObjectURL(blob);
    var anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "docket-record.json";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  async function verifyRecord() {
    var result = $("verify-result");
    if (!state.record) return;
    if (state.mode === "demo") {
      result.textContent = "This demo record was verified by the CLI when it was generated. The demo signing key is not published.";
      result.className = "side-note";
      return;
    }
    var key = state.live && state.live.signingKey;
    if (!key) {
      result.textContent = "Add the signing key in the connection form to verify this record.";
      result.className = "side-note bad";
      return;
    }
    result.textContent = "Verifying...";
    result.className = "side-note";
    try {
      var response = await api("/api/v1/verify", { record: state.record, signing_key: key });
      var valid = response.data && response.data.valid;
      result.textContent = (valid ? "Valid signature. " : "Invalid signature. ") + (response.data && response.data.message || "");
      result.className = "side-note " + (valid ? "ok" : "bad");
    } catch (error) {
      result.textContent = error.message;
      result.className = "side-note bad";
    }
  }

  function api(path, body) {
    return fetch(API + path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body)
    }).then(async function (response) {
      var data = await response.json().catch(function () { return {}; });
      if (!response.ok) {
        var detail = data && data.error ? data.error : {};
        var err = new Error(detail.message || ("Request failed with " + response.status));
        err.status = response.status;
        err.detail = detail;
        throw err;
      }
      return data;
    });
  }

  async function loadDemo() {
    setChecking(true, "Opening the recorded file", [
      "Locating the docket",
      "Loading case evidence",
      "Restoring the record"
    ]);
    try {
      var response = await fetch("./demo.json", { cache: "no-store" });
      if (!response.ok) throw new Error("demo snapshot returned " + response.status);
      state.demo = await response.json();
      state.docket = state.demo.release;
      state.record = state.demo.record || null;
      state.recordNote = "Cleared one-case release, not the blocked release above.";
      state.mode = "demo";
      state.index = 0;
      state.flow = "decide";
      await wait(650);
      setChecking(false);
      renderAll();
      setHelp("Recorded file loaded. Press replay to inspect the decision.");
    } catch (error) {
      setChecking(false);
      setHelp("Could not load the demo snapshot. " + error.message, true);
    }
    try {
      var config = await fetch("./demo.toml", { cache: "no-store" });
      if (config.ok) {
        state.demoConfig = await config.text();
        if (!state.configTouched && !$("config-input").value) $("config-input").value = state.demoConfig;
        var base = state.demoConfig.match(/base\s*=\s*"([^"]+)"/);
        var env = state.demoConfig.match(/environment\s*=\s*"([^"]+)"/);
        if (base) $("base-input").value = base[1];
        if (env) $("environment-input").value = env[1];
      }
    } catch (error) {
      /* the form still works with a pasted config */
    }
  }

  function connectOpen() {
    $("connect-error").textContent = "";
    if (!$("config-input").value && state.demoConfig) $("config-input").value = state.demoConfig;
    if (typeof $("connect-dialog").showModal === "function") {
      $("connect-dialog").showModal();
    } else {
      $("connect-dialog").setAttribute("open", "");
    }
  }

  function connectClose() {
    if (typeof $("connect-dialog").close === "function") $("connect-dialog").close();
    else $("connect-dialog").removeAttribute("open");
  }

  function connectSubmit(event) {
    event.preventDefault();
    var base = $("base-input").value.trim();
    var environment = $("environment-input").value.trim();
    var token = $("token-input").value;
    var config = $("config-input").value;
    state.configTouched = true;
    var signingKey = $("signing-input").value;
    var events = $("events-input").value.trim();
    if (!base || !environment || !token || !config) {
      $("connect-error").textContent = "Base URL, environment key, token and release config are required.";
      return;
    }
    state.live = {
      base: base.replace(/\/+$/, ""),
      environment: environment,
      token: token,
      config: config,
      signingKey: signingKey,
      eventsUrl: events || base.replace(/\/+$/, "") + "/api/"
    };
    state.mode = "live";
    state.docket = null;
    state.record = null;
    state.verifyText = "";
    state.recordNote = "";
    state.flow = "check";
    renderAll();
    connectClose();
    setHelp("Connection saved. Review the read-only preflight, then run the check.");
    window.setTimeout(function () { var button = $("preflight-run"); if (button) button.focus(); }, 40);
  }

  async function liveCheck() {
    if (!state.live) {
      connectOpen();
      return;
    }
    state.busy = true;
    state.record = null;
    state.verifyText = "";
    var preflight = $("preflight");
    if (preflight) preflight.hidden = true;
    $("decision").classList.remove("is-preflight");
    setFlow("check");
    setChecking(true, "Reading the release", [
      "Contacting Flagsmith",
      "Auditing every case",
      "Assembling the docket"
    ]);
    setHelp("Reading Flagsmith. No flags are written by a check.");
    var started = Date.now();
    try {
      var response = await api("/api/v1/release/check", {
        base: state.live.base,
        environment: state.live.environment,
        token: state.live.token,
        config: state.live.config,
        events_url: state.live.eventsUrl
      });
      await wait(Math.max(0, 950 - (Date.now() - started)));
      state.docket = response.data;
      state.index = 0;
      state.busy = false;
      setChecking(false);
      setFlow("decide");
      renderAll();
      setHelp("Live check complete. " + state.docket.release + " is " + stateLabel(state.docket.state).toLowerCase() + ".");
      showToast("Docket assembled for " + state.docket.release + ".");
    } catch (error) {
      await wait(Math.max(0, 950 - (Date.now() - started)));
      state.busy = false;
      setChecking(false);
      setFlow("connect");
      renderRail();
      renderPreflight();
      setHelp(error.message, true);
      showToast(error.message, true);
    }
  }

  function run() {
    if (state.mode === "live" && !state.docket) {
      renderPreflight();
      var preflightButton = $("preflight-run");
      if (preflightButton) preflightButton.focus();
      setHelp("Review the read-only preflight before Docket contacts your instance.");
      return;
    }
    if (state.mode === "demo") {
      state.busy = true;
      setFlow("check");
      setChecking(true, "Replaying the recorded file", [
        "Opening the release",
        "Replaying the audit",
        "Restoring the decision"
      ]);
      renderRail();
      setHelp("Replaying the recorded check. No live writes.");
      window.setTimeout(function () {
        state.busy = false;
        state.docket = state.demo.release;
        state.record = state.demo.record || null;
        state.index = 0;
        setChecking(false);
        setFlow("decide");
        renderAll();
        setHelp("Recorded check replayed. This file is blocked and wrote nothing.");
        showToast("Recorded file restored. The release is still blocked.");
      }, 1650);
    } else {
      liveCheck();
    }
  }

  function shipOpen() {
    if (state.mode !== "live" || !state.docket || state.docket.state !== "cleared") return;
    $("confirm-input").value = "";
    $("confirm-check").checked = false;
    var cases = (state.docket.cases || []).length;
    var winners = (state.docket.cases || []).filter(function (item) { return item.state === "winner"; }).length;
    $("confirm-summary").innerHTML = "<b>" + esc(state.docket.release) + "</b><br>" + esc(cases) + " experiment(s) / " + esc(winners) + " winner(s) / writes each winner to 100%.";
    updateConfirm();
    $("confirm-error").textContent = "";
    $("confirm-submit").disabled = true;
    if (typeof $("confirm-dialog").showModal === "function") $("confirm-dialog").showModal();
    else $("confirm-dialog").setAttribute("open", "");
  }

  function confirmClose() {
    if (typeof $("confirm-dialog").close === "function") $("confirm-dialog").close();
    else $("confirm-dialog").removeAttribute("open");
  }

  async function shipSubmit(event) {
    event.preventDefault();
    if (!state.live || !state.docket) return;
    $("confirm-submit").disabled = true;
    $("confirm-error").textContent = "";
    if (!$("confirm-check").checked || $("confirm-input").value.trim() !== "SHIP") {
      $("confirm-error").textContent = "Tick the confirmation and type SHIP.";
      return;
    }
    state.busy = true;
    setFlow("ship");
    try {
      var response = await api("/api/v1/release/ship", {
        base: state.live.base,
        environment: state.live.environment,
        token: state.live.token,
        config: state.live.config,
        events_url: state.live.eventsUrl,
        confirm: true,
        signing_key: state.live.signingKey,
        actor: "console"
      });
      state.record = response.data;
      state.recordNote = "Signed record for this release.";
      state.verifyText = "";
      state.busy = false;
      confirmClose();
      renderAll();
      setFlow("ship");
      setHelp("Released and signed. Download the record from the side panel.");
      showToast("Release signed. Download the record.");
    } catch (error) {
      state.busy = false;
      setFlow("decide");
      if (error.status === 409 && error.detail && error.detail.docket) {
        state.docket = error.detail.docket;
        state.index = 0;
        renderAll();
        confirmClose();
        setHelp("Ship refused: the docket is not cleared.", true);
        showToast("Ship refused. The docket is not cleared.", true);
      } else {
        $("confirm-error").textContent = error.message;
        $("confirm-submit").disabled = false;
      }
    }
  }

  function forget() {
    state.live = null;
    state.mode = "demo";
    state.record = state.demo ? state.demo.record : null;
    state.recordNote = "Cleared one-case release, not the blocked release above.";
    state.docket = state.demo ? state.demo.release : null;
    state.index = 0;
    state.verifyText = "";
    state.flow = "decide";
    $("token-input").value = "";
    $("signing-input").value = "";
    renderAll();
    setHelp("Session cleared. Tokens and keys are gone from this tab.");
  }

  function bind() {
    $("connect-button").addEventListener("click", connectOpen);
    $("close-connect").addEventListener("click", connectClose);
    $("connect-form").addEventListener("submit", connectSubmit);
    $("demo-button").addEventListener("click", function () { connectClose(); forget(); });
    $("run-button").addEventListener("click", run);
    $("ship-button").addEventListener("click", shipOpen);
    $("forget-button").addEventListener("click", forget);
    $("close-confirm").addEventListener("click", confirmClose);
    $("cancel-confirm").addEventListener("click", confirmClose);
    $("confirm-input").addEventListener("input", updateConfirm);
    $("confirm-check").addEventListener("change", updateConfirm);
    $("preflight-run").addEventListener("click", function () { liveCheck(); });
    $("config-input").addEventListener("input", function () { state.configTouched = true; });
    $("confirm-form").addEventListener("submit", shipSubmit);
  }

  bind();
  loadDemo();
})();