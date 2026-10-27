"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const state = { tele: {}, cfg: null, status: null, dirty: false };

  const FLAGS = {
    none: "—", green: "Green", yellow: "Yellow", blue: "Blue", white: "White",
    black: "Black", chequered: "Chequered", penalty: "Penalty", orange: "Orange",
  };
  const LEVEL_ICON = { good: "●", warn: "▲", crit: "▲", cold: "▼", idle: "–" };

  // Shift lights: 20 cells lit from 55% to 98% of max rpm.
  const RPM_CELLS = 20, RPM_FROM = 0.55, RPM_TO = 0.98;
  const lights = [];
  for (let i = 0; i < RPM_CELLS; i++) {
    const cell = document.createElement("i");
    if (i >= 14) cell.classList.add("hi");
    if (i >= 18) cell.classList.add("max");
    lights.push(cell);
  }
  $("rpm-lights").append(...lights);
  let litCells = -1;

  function fmtLap(ms) {
    if (ms == null) return "—";
    const m = Math.floor(ms / 60000);
    const s = ((ms % 60000) / 1000).toFixed(3).padStart(6, "0");
    return `${m}:${s}`;
  }

  function setPill(id, level, icon, text) {
    const el = $(id);
    el.dataset.level = level;
    el.querySelector(".ico").textContent = icon;
    el.querySelector(".val").textContent = text;
  }

  function renderStatus() {
    const s = state.status;
    if (!s) return;
    const paused = s.acc === "connected" && state.tele.status === "pause";
    const acc = paused ? ["warn", "❚❚", "Paused"] : {
      connected: ["good", "●", "Connected"],
      waiting: ["idle", "○", "Waiting"],
      unavailable: ["bad", "✕", "Unavailable"],
    }[s.acc];
    const label = s.source && s.source !== "acc" ? `${acc[2]} (${s.source})` : acc[2];
    setPill("st-acc", acc[0], acc[1], label);
    setPill("st-ai", "idle", "–", s.ai === "off" ? "Off" : s.ai);
    setPill("st-voice", "idle", "–", s.voice === "off" ? "Off" : s.voice);
    document.body.dataset.acc = s.acc;

    const banner = $("banner");
    if (paused) {
      banner.hidden = false;
      banner.textContent = "Game paused. Values update again when you resume.";
    } else if (s.acc === "connected") {
      banner.hidden = true;
    } else {
      banner.hidden = false;
      banner.textContent = s.acc === "unavailable"
        ? `Telemetry unavailable: ${s.reason}. Run with --source mock to test without the game.`
        : "Waiting for ACC. Start a session from Steam; Pit Wall connects once you are on track.";
    }
  }

  function tyreLevel(temp, psi) {
    const [tLo, tHi] = state.cfg.tyre_temp_c;
    const [pLo, pHi] = state.cfg.tyre_pressure_psi;
    if (temp > tHi + 5 || psi > pHi + 0.5) return ["crit", "Hot"];
    if (temp > tHi || psi > pHi) return ["warn", "High"];
    if (temp < tLo || psi < pLo) return ["cold", "Cold"];
    return ["good", "OK"];
  }

  // Back to the "no data" look, so old numbers never sit under a Waiting banner.
  function clear() {
    for (const id of ["speed", "gear", "lap", "current", "flag-txt", "rpm", "fuel", "fuel-laps", "fuel-per-lap", "last", "best"]) {
      $(id).textContent = "—";
    }
    $("current-lbl").textContent = "current";
    $("flag").dataset.flag = "none";
    lights.forEach((cell) => cell.classList.remove("on"));
    litCells = 0;
    $("tile-fuel").dataset.level = "idle";
    $("fuel-state").textContent = "";
    $("fuel-bar").style.width = "0%";
    document.querySelectorAll(".tyre").forEach((el) => {
      el.dataset.level = "idle";
      el.querySelector(".t").textContent = "—";
      el.querySelector(".p").textContent = "—";
      el.querySelector(".state").textContent = "";
    });
    $("last-delta").textContent = " ";
    $("car-track").textContent = " ";
  }

  function render() {
    state.dirty = false;
    const t = state.tele;
    if (!state.cfg) return;
    if (t.speed_kmh === undefined) {
      clear();
      return;
    }

    $("speed").textContent = t.speed_kmh;
    $("gear").textContent = t.gear;
    $("lap").textContent = t.laps + 1;
    $("current").textContent = fmtLap(t.current_ms);
    $("current-lbl").textContent = t.lap_valid ? "current" : "current · invalid";
    $("flag").dataset.flag = t.flag;
    $("flag-txt").textContent = FLAGS[t.flag] || t.flag;

    // RPM
    $("rpm").textContent = t.rpm;
    const ratio = t.max_rpm ? t.rpm / t.max_rpm : 0;
    const lit = Math.max(0, Math.min(RPM_CELLS, Math.round(((ratio - RPM_FROM) / (RPM_TO - RPM_FROM)) * RPM_CELLS)));
    if (lit !== litCells) {
      lights.forEach((cell, i) => cell.classList.toggle("on", i < lit));
      litCells = lit;
    }

    // Fuel
    const fuelTile = $("tile-fuel");
    let fuelLevel = "good", fuelWord = "";
    if (t.fuel_laps_left < state.cfg.fuel_critical_laps) { fuelLevel = "crit"; fuelWord = "▲ Critical"; }
    else if (t.fuel_laps_left < state.cfg.fuel_warn_laps) { fuelLevel = "warn"; fuelWord = "▲ Low"; }
    fuelTile.dataset.level = fuelLevel;
    $("fuel").textContent = t.fuel_l.toFixed(1);
    $("fuel-laps").textContent = t.fuel_laps_left.toFixed(1);
    $("fuel-per-lap").textContent = t.fuel_per_lap_l ? t.fuel_per_lap_l.toFixed(2) : "—";
    const fs = $("fuel-state");
    fs.textContent = fuelWord;
    fs.dataset.level = fuelLevel;
    const pct = t.max_fuel_l ? Math.min(100, (t.fuel_l / t.max_fuel_l) * 100) : 0;
    $("fuel-bar").style.width = `${pct}%`;

    // Tyres
    document.querySelectorAll(".tyre").forEach((el) => {
      const i = Number(el.dataset.i);
      const temp = t.tyre_temp_c[i], psi = t.tyre_pressure_psi[i];
      const [level, word] = tyreLevel(temp, psi);
      el.dataset.level = level;
      el.querySelector(".t").textContent = temp.toFixed(0);
      el.querySelector(".p").textContent = psi.toFixed(1);
      el.querySelector(".state").textContent = `${LEVEL_ICON[level]} ${word}`;
    });

    // Laps
    $("last").textContent = fmtLap(t.last_ms);
    $("best").textContent = fmtLap(t.best_ms);
    const d = $("last-delta");
    if (t.last_ms != null && t.last_ms === t.best_ms) {
      d.textContent = "● Best lap";
      d.className = "sub delta-good";
    } else if (t.last_ms != null && t.best_ms != null) {
      const delta = (t.last_ms - t.best_ms) / 1000;
      d.textContent = `${delta <= 0 ? "▼" : "▲"} ${delta >= 0 ? "+" : ""}${delta.toFixed(3)} to best`;
      d.className = `sub ${delta <= 0 ? "delta-good" : "delta-bad"}`;
    } else {
      d.textContent = " ";
    }
    $("car-track").textContent = [t.car, t.track].filter(Boolean).join(" · ").replaceAll("_", " ");
  }

  // Radio: subtitle shows the latest call ~4 s, the log keeps the last 20, newest first.
  const PRIORITY = { 1: "▲ Urgent", 2: "● Call", 3: "○ Info" };
  const SUBTITLE_MS = 4000, LOG_MAX = 20;
  let subtitleTimer = null;

  function showSubtitle(call) {
    const box = document.querySelector(".subtitle");
    const p = $("subtitle");
    box.dataset.priority = call.priority;
    p.className = "";
    p.textContent = call.text;
    clearTimeout(subtitleTimer);
    subtitleTimer = setTimeout(() => {
      p.classList.add("fading");
      subtitleTimer = setTimeout(() => {
        delete box.dataset.priority;
        p.className = "quiet";
        p.textContent = "Radio quiet";
      }, 400);
    }, SUBTITLE_MS);
  }

  function addLog(call) {
    const list = $("radio-log");
    list.querySelector(".empty")?.remove();
    const li = document.createElement("li");
    li.dataset.priority = call.priority;
    for (const [cls, text] of [
      ["when", call.time], ["lap", `L${call.lap}`], ["prio", PRIORITY[call.priority] || ""], ["text", call.text],
    ]) {
      const span = document.createElement("span");
      span.className = cls;
      span.textContent = text;
      li.append(span);
    }
    list.prepend(li);
    while (list.children.length > LOG_MAX) list.lastElementChild.remove();
  }

  function schedule() {
    if (!state.dirty) {
      state.dirty = true;
      requestAnimationFrame(render);
    }
  }

  function handle(msg) {
    switch (msg.type) {
      case "config":
        state.cfg = msg;
        break;
      case "status":
        state.status = msg;
        if (msg.acc !== "connected") state.tele = {};
        renderStatus();
        break;
      case "telemetry":
        Object.assign(state.tele, msg.data);
        if ("status" in msg.data) renderStatus();
        break;
      case "radio":
        showSubtitle(msg);
        addLog(msg);
        return;
      case "radio_log":
        $("radio-log").replaceChildren();
        if (!msg.calls.length) $("radio-log").innerHTML = '<li class="empty">No calls yet.</li>';
        msg.calls.forEach(addLog);
        return;
    }
    schedule();
  }

  let retry = 500;
  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onopen = () => {
      retry = 500;
      setPill("st-core", "good", "●", "Online");
    };
    ws.onmessage = (e) => handle(JSON.parse(e.data));
    ws.onclose = () => {
      setPill("st-core", "bad", "✕", "Offline");
      setTimeout(connect, retry);
      retry = Math.min(retry * 2, 5000);
    };
  }

  connect();
})();
