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
    const ai = {
      ready: ["good", "●", "Lines ready"],
      missing: ["warn", "▲", "Not generated"],
      lite: ["idle", "–", "Lite"],
    }[s.ai] || ["idle", "–", "Off"];
    setPill("st-ai", ...ai);
    const voice = {
      ready: ["good", "●", "On"],
      loading: ["idle", "○", "Loading"],
      missing: ["warn", "▲", "Not downloaded"],
      error: ["bad", "✕", "Error"],
    }[s.voice] || ["idle", "–", "Off"];
    setPill("st-voice", ...voice);
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

  // Track map: outline and turns come in a "track" message; the car dot and the
  // current / next turn follow lap_pos and x, y from telemetry.
  const SVG = "http://www.w3.org/2000/svg";
  let track = null; // { track, points, turns, length_m, official }
  let mapTurn; // turn highlighted on the map, so it is redrawn only on change

  const turnName = (t) => (t ? `T${t.number}${t.name ? ` ${t.name}` : ""}` : "—");
  const inside = (t, pos) => (t.start <= t.end ? pos >= t.start && pos <= t.end : pos >= t.start || pos <= t.end);
  const pointAt = (pos) => track.points[Math.floor(((pos % 1) + 1) % 1 * track.points.length) % track.points.length];

  function turnAt(pos) {
    const current = track.turns.find((t) => inside(t, pos)) || null;
    let next = null, gap = 2;
    for (const t of track.turns) {
      const ahead = (t.start - pos + 1) % 1;
      if (t !== current && ahead < gap) [next, gap] = [t, ahead];
    }
    return [current, next, gap];
  }

  function turnPath(t) {
    const n = track.points.length, from = Math.floor(t.start * n), span = (Math.floor(t.end * n) - from + n) % n;
    const pts = [];
    for (let i = 0; i <= span; i++) pts.push(track.points[(from + i) % n].join(","));
    return `M${pts.join("L")}`;
  }

  function drawTrack() {
    $("track-name").textContent = track ? track.track.replaceAll("_", " ") : "";
    const map = $("map"), labels = $("map-labels");
    labels.replaceChildren();
    $("map-line").setAttribute("d", "");
    $("map-turn").setAttribute("d", "");
    $("map-car").setAttribute("r", 0);
    mapTurn = undefined;
    if (!track || !track.points) {
      $("map-state").textContent = track ? "○ Learning the track: drive one full lap" : "○ No track yet";
      return;
    }
    const pts = track.points, xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
    const size = Math.max(maxX - minX, maxY - minY), pad = size * 0.07;
    map.setAttribute("viewBox", `${minX - pad} ${minY - pad} ${maxX - minX + 2 * pad} ${maxY - minY + 2 * pad}`);
    $("map-line").setAttribute("d", `M${pts.map((p) => p.join(",")).join("L")}Z`);
    $("map-car").dataset.r = size * 0.022;
    // Turn numbers sit just outside the line, pushed away from the middle of the map.
    const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2, font = size * 0.05;
    for (const t of track.turns) {
      const [x, y] = pointAt(t.start + ((t.end - t.start + 1) % 1) / 2);
      const d = Math.hypot(x - cx, y - cy) || 1;
      const text = document.createElementNS(SVG, "text");
      text.setAttribute("x", x + ((x - cx) / d) * font * 1.3);
      text.setAttribute("y", y + ((y - cy) / d) * font * 1.3);
      text.setAttribute("font-size", font);
      text.textContent = t.number;
      text.turn = t;
      labels.append(text);
    }
    $("map-state").textContent = {
      official: "● Official turn numbers",
      lovely: "● Turn names: Lovely Track Data (CC BY-NC-SA)",
    }[track.source] || "▲ Turns numbered from your lap";
  }

  function renderTrack(t) {
    if (!track || t.lap_pos === undefined) return;
    const [current, next, gap] = turnAt(t.lap_pos);
    $("turn-now").textContent = t.in_pit_lane ? "Pit lane" : current ? turnName(current) : "Straight";
    $("turn-next").textContent = turnName(next);
    $("turn-dist").textContent = next && track.length_m && !t.in_pit_lane ? `in ${Math.round(gap * track.length_m)} m` : "";
    if (!track.points) return;
    const car = $("map-car");
    car.setAttribute("cx", t.x);
    car.setAttribute("cy", t.y);
    car.setAttribute("r", car.dataset.r);
    if (current !== mapTurn) {
      mapTurn = current;
      $("map-turn").setAttribute("d", current ? turnPath(current) : "");
      for (const text of $("map-labels").children) text.classList.toggle("on", text.turn === current);
    }
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
    for (const id of ["turn-now", "turn-next"]) $(id).textContent = "—";
    $("turn-dist").textContent = "";
    $("map-car").setAttribute("r", 0);
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
    renderTrack(t);
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
      case "settings":
        applySettings(msg);
        return;
      case "track":
        track = msg;
        drawTrack();
        break;
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

  // Screens: #live (default) and #settings.
  function route() {
    const screen = location.hash === "#settings" ? "settings" : "live";
    for (const id of ["live", "settings"]) $(id).hidden = id !== screen;
    document.querySelectorAll(".tabs a.tab").forEach((a) => {
      const on = a.getAttribute("href") === `#${screen}`;
      a.classList.toggle("active", on);
      if (on) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
    if (screen === "live") schedule();
  }
  window.addEventListener("hashchange", route);
  route();

  // Settings: the server owns them; every change is sent at once and comes back to
  // every open dashboard as a "settings" message.
  const form = $("settings-form");
  let settings = null;

  function setState(level, text) {
    const el = $("settings-state");
    el.dataset.level = level;
    el.textContent = text;
  }

  function applySettings(s) {
    settings = s;
    form.querySelector(`input[name="lite"][value="${s.lite}"]`).checked = true;
    $("ai-count").textContent = s.ai_lines ? `${s.ai_lines} AI-written lines` : "none yet";
    const noLines = $("ai-missing");
    noLines.hidden = s.lite || s.ai_lines > 0;
    for (const name of ["voice", "engine", "calls", "subtitle_size"]) {
      const input = form.querySelector(`input[name="${name}"][value="${s[name]}"]`);
      if (input) input.checked = true;
    }
    form.elements.subtitles.checked = s.subtitles;
    form.elements.tips.checked = s.tips;
    if (document.activeElement !== $("volume")) $("volume").value = Math.round(s.volume * 100);
    $("volume-out").textContent = `${Math.round(s.volume * 100)}%`;
    form.querySelectorAll('input[name="subtitle_size"]').forEach((i) => (i.disabled = !s.subtitles));

    const missing = s.voice !== "off" && !s.downloaded[s.engine][s.voice];
    const note = $("voice-missing");
    note.hidden = !missing;
    if (missing) {
      note.innerHTML = "";
      note.append("▲ This voice is not downloaded yet. Run ");
      const code = document.createElement("code");
      code.textContent = `uv run pitwall voices download ${s.voice} --engine ${s.engine}`;
      note.append(code, " and choose it again.");
    }
    $("voice-test").disabled = s.voice === "off" || missing;

    document.body.dataset.subtitles = s.subtitles ? "on" : "off";
    document.body.dataset.subtitleSize = s.subtitle_size;
  }

  async function save(changes) {
    setState("idle", "○ Saving…");
    try {
      const res = await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(changes),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || res.statusText);
      applySettings(body);
      setState("good", "● Saved");
    } catch (err) {
      if (settings) applySettings(settings); // put the form back to what is really set
      setState("bad", `✕ Not saved: ${err.message}`);
    }
  }

  form.addEventListener("change", (e) => {
    const t = e.target;
    if (t.name === "volume") save({ volume: Number(t.value) / 100 });
    else if (t.name === "subtitles") save({ subtitles: t.checked });
    else if (t.name === "tips") save({ tips: t.checked });
    else if (t.name === "lite") save({ lite: t.value === "true" });
    else if (t.type === "radio") save({ [t.name]: t.value });
  });
  $("volume").addEventListener("input", (e) => {
    $("volume-out").textContent = `${e.target.value}%`;
  });
  form.addEventListener("submit", (e) => e.preventDefault());

  $("voice-test").addEventListener("click", async () => {
    const btn = $("voice-test");
    btn.disabled = true;
    try {
      const res = await fetch("/api/voice/test", { method: "POST" });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || res.statusText);
      setState("good", "● Radio check sent");
    } catch (err) {
      setState("bad", `✕ ${err.message}`);
    } finally {
      setTimeout(() => (btn.disabled = settings && settings.voice === "off"), 2000);
    }
  });

  connect();
})();
