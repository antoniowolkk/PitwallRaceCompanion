"""Setup advisor (phase 5): driving symptoms become one small setup proposal, the driver decides.

Every pass through a turn carries its balance (tips.py: front minus rear slip angle while
cornering, per phase). After each lap the advisor looks at the last few clean laps, finds
the strongest symptom (understeer, oversteer, wheelspin) and maps it with setup_rules.toml
to one click on one setup value, on top of the setup the driver picked as base. That draft
is rewritten every lap in Pit Wall's own drafts folder, never in ACC's.

Apply writes a new version next to the base in ACC's setups folder (setups.py: one value
changed, everything else byte for byte). The driver loads it in the garage and says so;
after a few clean laps on it the advisor checks whether the symptom got better, and if it
got worse it suggests going back. Reject hides that change for a while. One change at a time.
"""

from __future__ import annotations

import json
import logging
import re
import statistics
import time
import tomllib
from pathlib import Path
from typing import Any

from . import setups
from .models import Snapshot
from .style import clean
from .tips import TurnCoach

log = logging.getLogger(__name__)

RULES_FILE = Path(__file__).with_name("setup_rules.toml")
SYMPTOMS = {
    "entry_oversteer": "Oversteer on corner entry",
    "fast_understeer": "Understeer in fast corners",
    "slow_understeer": "Understeer in slow corners",
    "lap_understeer": "Understeer everywhere",
    "exit_oversteer": "Oversteer on the throttle",
    "exit_wheelspin": "Wheelspin on exit",
}


def load_rules(path: Path = RULES_FILE) -> list[dict[str, Any]]:
    with path.open("rb") as f:
        return tomllib.load(f)["rule"]


def _mean(values: list[float], need: int) -> float | None:
    return round(statistics.mean(values), 2) if len(values) >= need else None


def symptoms(passes: list[dict[str, Any]], c: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Symptom -> {metric, limit, strength}; strength >= 1 means it is there."""
    need = c["min_samples"]

    def phase(name: str, rows: list[dict[str, Any]]) -> float | None:
        return _mean([p[f"bal_{name}"] for p in rows if p.get(f"bal_{name}") is not None], need)

    fast = [p for p in passes if (p.get("min_kmh") or 0) >= c["fast_kmh"]]
    slow = [p for p in passes if p.get("min_kmh") is not None and p["min_kmh"] < c["fast_kmh"]]
    entry, mid, exit_ = phase("entry", passes), phase("mid", passes), phase("exit", passes)
    found: dict[str, dict[str, Any]] = {}

    def add(name: str, metric: float | None, limit: float, unit: str = "deg") -> None:
        if metric is not None:
            found[name] = {"metric": metric, "limit": limit, "unit": unit, "strength": round(metric / limit, 2)}

    add("entry_oversteer", -entry if entry is not None else None, c["oversteer_deg"])
    add("exit_oversteer", -exit_ if exit_ is not None else None, c["oversteer_deg"])
    add("fast_understeer", phase("mid", fast), c["understeer_deg"])
    add("slow_understeer", phase("mid", slow), c["understeer_deg"])
    if None not in (entry, mid, exit_):
        add("lap_understeer", min(entry, mid, exit_), c["lap_understeer_deg"])
    if len(passes) >= need:
        add("exit_wheelspin", round(sum(p["wheelspin"] for p in passes) / len(passes), 2), c["wheelspin_rate"], "share")
    return found


class SetupAdvisor:
    def __init__(self, thresholds: dict[str, Any], turns: TurnCoach, root: Path | None, acc_root: Path | None,
                 rules: list[dict[str, Any]] | None = None):
        self.c = thresholds["setup"]
        self._turns = turns
        self.root = root  # Pit Wall data: setup state and drafts; None keeps it in memory
        self.acc_root = acc_root  # ACC's Setups folder; None: no files to work with
        self.rules = rules if rules is not None else load_rules()
        self.key: tuple[str, str] | None = None  # (car, track)
        self.state: dict[str, Any] = {}
        self.found: dict[str, dict[str, Any]] = {}
        self.draft: dict[str, Any] | None = None
        self.note = ""
        self._seen = 0
        self.dirty = True

    # --- state per car and track ------------------------------------------------------

    def _state_path(self) -> Path | None:
        if not self.root or not self.key:
            return None
        return self.root / "setup" / f"{re.sub(r'[^\w-]', '_', '__'.join(self.key))}.json"

    def _load(self) -> None:
        path = self._state_path()
        try:
            self.state = json.loads(path.read_text(encoding="utf-8")) if path and path.exists() else {}
        except (OSError, ValueError):
            log.warning("ignoring unreadable %s", path)
            self.state = {}
        self.state.setdefault("history", [])
        self.state.setdefault("rejected", {})

    def _save(self) -> None:
        path = self._state_path()
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self.state, indent=1), encoding="utf-8")

    def _folder(self) -> Path | None:
        if not self.acc_root or not self.key:
            return None
        return setups.track_folder(self.acc_root, *self.key)

    def _file(self, name: str | None) -> Path | None:
        folder = self._folder()
        return folder / name if folder and name and (folder / name).exists() else None

    def current_values(self) -> dict[str, int]:
        path = self._file(self.state.get("current"))
        if not path:
            return {}
        try:
            return setups.values(json.loads(setups.read_text(path)))
        except (OSError, ValueError):
            return {}

    # --- every snapshot ----------------------------------------------------------------

    def update(self, snap: Snapshot) -> None:
        if snap.car and snap.track and (snap.car, snap.track) != self.key:
            self.key = (snap.car, snap.track)
            self._load()
            self._seen = 0
            self.found, self.draft, self.note = {}, None, ""
            self.dirty = True
        laps = self._turns.laps
        if len(laps) < self._seen:  # new session
            self._seen = 0
            if self.state.get("check"):
                self.state["check"]["from"] = 0
        if len(laps) != self._seen:
            self._seen = len(laps)
            self._judge()
            self.dirty = True

    def _window(self, after: int = 0) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """The last clean laps (after lap index `after`) and their passes."""
        laps = [l for l in self._turns.laps[after:] if clean(l)][-self.c["window_laps"]:]
        numbers = {l["lap"] for l in laps}
        return laps, [p for p in self._turns.passes if p["lap"] in numbers]

    def _judge(self) -> None:
        check = self.state.get("check")
        laps, passes = self._window(check["from"] if check else 0)
        self.found = symptoms(passes, self.c) if len(laps) >= self.c["min_clean_laps"] else {}
        self.draft = None
        if self.state.get("waiting"):
            self.note = f"Load {self.state['current']} in the garage, then press \"Loaded\"."
        elif not self.state.get("current"):
            self.note = "Pick the setup you are driving as the base."
        elif len(laps) < self.c["min_clean_laps"]:
            self.note = f"Collecting: {len(laps)} of {self.c['min_clean_laps']} clean laps."
        elif check:
            self._check(check, len(laps))
        else:
            self.draft = self._propose()
            self.note = "" if self.draft else "Balance looks fine: no change to suggest."
            self._write_draft()

    def _propose(self) -> dict[str, Any] | None:
        values = self.current_values()
        lap = len(self._turns.laps)
        for name, s in sorted(self.found.items(), key=lambda kv: -kv[1]["strength"]):
            if s["strength"] < 1:
                continue
            for rule in self.rules:
                if rule["symptom"] != name or rule["param"] not in values:
                    continue
                key = f"{rule['param']}{rule['step']:+d}"
                new = values[rule["param"]] + rule["step"]
                if new < 0 or self.state["rejected"].get(key, -1) > lap:
                    continue
                return {
                    "symptom": name, "label": SYMPTOMS[name], **s,
                    "laps": self.c["window_laps"], "param": rule["param"],
                    "param_label": setups.PARAMS[rule["param"]].label,
                    "from": values[rule["param"]], "to": new, "step": rule["step"],
                    "why": rule["why"], "source": rule["source"], "base": self.state["current"],
                }
        return None

    def _write_draft(self) -> None:
        """The draft setup (current version plus the proposed click), rewritten every lap."""
        path = self._file(self.state.get("current"))
        if not self.root or not path or not self.draft:
            return
        try:
            text = setups.change(setups.read_text(path), setups.PARAMS[self.draft["param"]], self.draft["to"])
        except (OSError, ValueError) as e:
            log.warning("draft not written: %s", e)
            return
        out = self.root / "setup" / "drafts" / f"{re.sub(r'[^\w-]', '_', '__'.join(self.key or ('', '')))}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def _check(self, check: dict[str, Any], laps: int) -> None:
        """After a change: did its symptom get better on the new setup?"""
        after = self.found.get(check["symptom"])
        last = self.state["history"][-1]
        if after is None:
            self.note = "Checking the change: not enough cornering data yet."
            return
        # Compared as strength (metric / limit), so degrees and shares are judged alike.
        before, now = check["strength"], after["strength"]
        last["metric_after"], last["strength_after"] = after["metric"], now
        what = f"{SYMPTOMS[check['symptom']]} ({check['metric']} to {after['metric']} {after['unit']})"
        if now <= before - self.c["better_by"]:
            last["result"], self.note = "better", f"Better after the change: {what}."
        elif now >= before + self.c["better_by"]:
            last["result"], self.note = "worse", f"Worse after the change: {what}. Go back to {last['from_file']}?"
        else:
            last["result"], self.note = "same", f"No clear change: {what}."
        self.state.pop("check", None)
        self._save()

    # --- driver actions ----------------------------------------------------------------

    def set_base(self, name: str) -> None:
        if not self._file(name):
            raise ValueError(f"no setup called {name}")
        self.state.update(base=name, current=name, waiting=False)
        self.state.pop("check", None)
        self._save()
        self._judge()
        self.dirty = True

    def apply(self) -> str:
        """Write the draft as a new version in ACC's setups folder; returns its name."""
        d = self.draft
        path = self._file(self.state.get("current"))
        if not d or not path:
            raise ValueError("nothing to apply")
        out = setups.write_version(path, setups.PARAMS[d["param"]], d["to"])
        self.state["history"].append({
            "file": out.name, "from_file": path.name, "param": d["param"], "from": d["from"], "to": d["to"],
            "symptom": d["symptom"], "metric": d["metric"], "strength": d["strength"], "lap": len(self._turns.laps),
            "made": time.strftime("%Y-%m-%d %H:%M"), "result": None,
        })
        self.state.update(current=out.name, waiting=True)
        self.draft = None
        self._save()
        self._judge()
        self.dirty = True
        return out.name

    def loaded(self) -> None:
        """The driver loaded the new version: start counting laps for the check."""
        last = self.state["history"][-1] if self.state["history"] else None
        self.state["waiting"] = False
        if last and last["result"] is None:
            self.state["check"] = {"symptom": last["symptom"], "metric": last["metric"],
                                   "strength": last["strength"], "from": len(self._turns.laps)}
        self._save()
        self._judge()
        self.dirty = True

    def reject(self) -> None:
        d = self.draft
        if not d:
            raise ValueError("nothing to reject")
        self.state["rejected"][f"{d['param']}{d['step']:+d}"] = len(self._turns.laps) + self.c["reject_laps"]
        self._save()
        self._judge()
        self.dirty = True

    def go_back(self) -> None:
        """Make the version before the last change current again (the driver loads it)."""
        last = self.state["history"][-1] if self.state["history"] else None
        if not last:
            raise ValueError("nothing to go back to")
        self.state.update(current=last["from_file"], waiting=True)
        last["result"] = last.get("result") or "undone"
        self._save()
        self._judge()
        self.dirty = True

    def view(self) -> dict[str, Any]:
        files = setups.list_setups(self.acc_root, *self.key) if self.acc_root and self.key else []
        return {
            "type": "setup",
            "car": self.key[0] if self.key else None, "track": self.key[1] if self.key else None,
            "files": [p.name for p in files], "folder": str(self._folder() or ""),
            "base": self.state.get("base"), "current": self.state.get("current"),
            "waiting": bool(self.state.get("waiting")), "checking": bool(self.state.get("check")),
            "values": {k: {"label": setups.PARAMS[k].label, "value": v} for k, v in self.current_values().items()},
            "symptoms": [{"symptom": k, "label": SYMPTOMS[k], **v} for k, v in
                         sorted(self.found.items(), key=lambda kv: -kv[1]["strength"])],
            "draft": self.draft, "note": self.note,
            "history": self.state.get("history", [])[-10:][::-1],
        }
