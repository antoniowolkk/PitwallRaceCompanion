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

  function tyreLevel(temp, psi, wet) {
    const [tLo, tHi] = wet ? state.cfg.tyre_temp_c_wet : state.cfg.tyre_temp_c;
    const [pLo, pHi] = wet ? state.cfg.tyre_pressure_psi_wet : state.cfg.tyre_pressure_psi;
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
    $("map-empty").hidden = Boolean(track && track.points);
    map.style.display = track && track.points ? "" : "none";
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

  // Personal turn tips: after a session with habits, the card asks before the local AI
  // writes anything; nothing runs until the driver clicks "Write tips".
  const HABIT = {
    lockup: "lockups", wheelspin: "wheelspin", off: "track limits", early_brake: "braking early",
    low_min_speed: "low minimum speed", coasting: "coasting", late_throttle: "late on the throttle",
  };
  let coach = null, coachTimer = null;

  function renderCoach(c) {
    coach = c;
    const box = $("coach"), o = c.offer;
    clearTimeout(coachTimer);
    box.hidden = !(o || c.running || c.error || c.done);
    if (box.hidden) return;
    box.dataset.level = c.error ? "bad" : c.running ? "idle" : o ? "warn" : "good";
    let text;
    if (c.running) text = `○ Writing tips with the local AI… ${c.progress}`;
    else if (c.error) text = `✕ ${c.error}`;
    else if (o) text = `▲ Habits found at ${o.track.replaceAll("_", " ")} (${o.kind}, ${o.laps} laps, ${o.condition}). Write personal tips with the local AI?`;
    else text = `● ${c.done}`;
    $("coach-text").textContent = text;
    const list = $("coach-turns");
    list.replaceChildren();
    for (const t of o && !c.running ? o.turns : []) {
      const li = document.createElement("li");
      const b = document.createElement("b");
      b.textContent = t.name || `Turn ${t.turn}`;
      li.append(b, ` ${t.habits.map((h) => HABIT[h] || h).join(", ")}`);
      list.append(li);
    }
    const live = state.status && state.status.acc === "connected";
    $("coach-note").textContent = o && !c.running
      ? `Uses the graphics card for about a minute${live ? "; ACC is running, so best after you quit it" : ""}. Used from your next session at this track.`
      : "";
    $("coach-actions").hidden = !o || c.running;
    if (!o && !c.running && !c.error) coachTimer = setTimeout(() => ($("coach").hidden = true), 20000);
  }

  async function coachPost(path) {
    if (!coach || !coach.offer) return;
    $("coach-actions").hidden = true;
    try {
      const res = await fetch(path, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session: coach.offer.session }),
      });
      if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    } catch (err) {
      renderCoach({ ...coach, error: `Could not start: ${err.message}` });
    }
  }
  $("coach-yes").addEventListener("click", () => coachPost("/api/coach/generate"));
  $("coach-no").addEventListener("click", () => coachPost("/api/coach/dismiss"));

  // Coach screen: the session so far (coach_view at every lap end and tip) and past sessions.
  const secs = (s) => (s == null ? "—" : fmtLap(Math.round(s * 1000)));
  const cell = (row, text, cls) => {
    const td = document.createElement("td");
    td.textContent = text;
    if (cls) td.className = cls;
    row.append(td);
  };
  const count = (n) => (n ? `▲ ${n}` : "–");

  function traitList(list, traits) {
    list.replaceChildren();
    for (const t of traits) {
      const li = document.createElement("li");
      li.dataset.level = t.level;
      const b = document.createElement("b");
      b.textContent = `${t.level === "good" ? "●" : "▲"} ${t.label}`;
      li.append(b, ` ${t.detail}`);
      list.append(li);
    }
  }

  function renderCoachView(v) {
    $("co-where").textContent = [v.track, v.car, v.session].filter(Boolean).join(" · ").replaceAll("_", " ");
    const clean = v.laps.filter((l) => l.clean).length;
    $("co-laps").textContent = v.laps.length || "—";
    $("co-clean").textContent = v.laps.length ? `${clean} clean` : "";
    $("co-best").textContent = secs(v.best_s);
    $("co-spread").textContent = v.spread_s == null ? "—" : `${v.spread_s.toFixed(2)} s`;
    $("co-style").textContent = v.style.text || "Drive a few laps: the style summary builds as you go.";
    traitList($("co-traits"), v.style.traits);

    const tips = $("co-tip-list");
    if (v.tips.length) {
      tips.replaceChildren(...[...v.tips].reverse().map((t) => {
        const li = document.createElement("li");
        const lap = document.createElement("span");
        lap.className = "lap";
        lap.textContent = `L${t.lap}`;
        li.append(lap, ` ${t.text}`);
        return li;
      }));
    }

    const laps = $("co-lap-table").tBodies[0];
    if (v.laps.length) {
      laps.replaceChildren(...v.laps.map((l) => {
        const tr = document.createElement("tr");
        cell(tr, l.lap + 1);
        cell(tr, secs(l.time_s), "num");
        cell(tr, l.tidy ? "● Tidy" : l.clean ? "○ Clean" : l.pit ? "– Pit" : l.partial ? "– Joined" : "✕ Invalid",
             l.tidy ? "good" : l.clean || l.pit || l.partial ? "" : "bad");
        cell(tr, count(l.lockups), l.lockups ? "warn" : "");
        cell(tr, count(l.wheelspin), l.wheelspin ? "warn" : "");
        cell(tr, count(l.cuts), l.cuts ? "warn" : "");
        cell(tr, `${Math.round(l.front_c)} / ${Math.round(l.rear_c)}`, "num");
        return tr;
      }));
    }

    const turns = $("co-turn-table").tBodies[0];
    if (v.turns.length) {
      turns.replaceChildren(...v.turns.map((t) => {
        const tr = document.createElement("tr");
        cell(tr, `T${t.turn}${t.name ? ` ${t.name}` : ""}`);
        cell(tr, t.passes, "num");
        cell(tr, t.best_s == null ? "—" : `${t.best_s.toFixed(2)} s`, "num");
        cell(tr, count(t.lockups), t.lockups ? "warn" : "");
        cell(tr, count(t.wheelspin), t.wheelspin ? "warn" : "");
        cell(tr, count(t.cuts), t.cuts ? "warn" : "");
        return tr;
      }));
    }
  }

  async function loadSessions() {
    try {
      const list = await (await fetch("/api/sessions")).json();
      const ol = $("co-past");
      if (!list.length) return;
      ol.replaceChildren(...list.map((s) => {
        const li = document.createElement("li");
        const head = document.createElement("b");
        const when = s.id.slice(0, 13).replace(/(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/, "$1-$2-$3 $4:$5");
        head.textContent = `${when} · ${s.track.replaceAll("_", " ")} · ${s.session} · ${s.laps} laps · best ${secs(s.best_s)}`;
        const p = document.createElement("p");
        p.textContent = s.style.text || "";
        const tips = document.createElement("p");
        tips.className = "sub";
        tips.textContent = s.habits
          ? `${s.habits} habits found · ${s.tips_written ? "● personal tips written" : "○ no personal tips yet"}`
          : s.session === "qualify" ? "No habits found" : "Not used for personal tips (only qualifying is)";
        li.append(head, p, tips);
        return li;
      }));
    } catch (err) {
      $("co-past").innerHTML = '<li class="empty">✕ Could not load past sessions.</li>';
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
      const [level, word] = tyreLevel(temp, psi, t.wet_tyres);
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
      case "coach":
        renderCoach(msg);
        return;
      case "coach_view":
        renderCoachView(msg);
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
      if (!$("coaching").hidden) loadSessions();
    };
    ws.onmessage = (e) => handle(JSON.parse(e.data));
    ws.onclose = () => {
      setPill("st-core", "bad", "✕", "Offline");
      setTimeout(connect, retry);
      retry = Math.min(retry * 2, 5000);
    };
  }

  // Screens: #live (default), #coach and #settings.
  const SCREENS = { live: "live", coach: "coaching", settings: "settings" };
  function route() {
    const screen = SCREENS[location.hash.slice(1)] ? location.hash.slice(1) : "live";
    for (const [name, id] of Object.entries(SCREENS)) $(id).hidden = name !== screen;
    document.body.dataset.screen = screen; // the Live screen fits the window; the others scroll
    if (screen === "coach") loadSessions();
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
    form.elements.coach.checked = s.coach;
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
    else if (t.name === "coach") save({ coach: t.checked });
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
