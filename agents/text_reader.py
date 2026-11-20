"""Text reader: finds the free-text risk factors in a change request.

Two interchangeable readers return the same six named factors the Risk agent already uses:

* ``RegexReader``  - the heuristic baseline: regular expressions written from the wording in the change HISTORY.
* ``ClaudeReader`` - Claude reads the description and plans and returns, for each factor, an exact quote from the
                     change as evidence (or nothing). A quote that does not appear in the change text is rejected,
                     so every factor Claude reports can be checked against the record.

The Risk model is trained on the history with the SAME reader it later applies to new changes, so the learned
weights match how the factors were read. The factor definitions below are generic (from the policy's list of
warning signs); they were not written from evaluation changes.

Claude results are cached on disk by (model, text hash), so a rerun costs nothing.

  python -m agents.text_reader --sample 10      # cost check: Claude reads 10 history changes, prints tokens and $
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Iterable

from .risk import CLUES

FACTORS = {
    "irreversible": "Once done, the change cannot be fully reversed by its backout plan: rollback is impossible, needs "
                    "a full restore from backup, or data or configuration is lost or permanently removed.",
    "first_time": "This technique, tool, product or procedure is being used for the first time here.",
    "vendor": "An external vendor or third party performs or controls part of the work.",
    "big_bang": "Everything changes at once (all nodes, instances, regions or users together) with no staging.",
    "canary": "A staged rollout: a small part first (one node, a pilot group, a percentage of traffic), then the rest.",
    "rehearsed": "The same procedure was explicitly rehearsed end to end beforehand (a dry run or rehearsal), not "
                 "just a normal test plan.",
}
assert set(FACTORS) == set(CLUES), "the reader must return exactly the Risk agent's text factors"

SYSTEM = """You review IT change requests for a change advisory board. For each change, decide which of these \
risk factors the text explicitly supports:

""" + "\n".join(f"- {k}: {v}" for k, v in FACTORS.items()) + """

Rules:
- For a factor that is present, return an exact quote (copied character for character, 3-25 words) from the \
change text that shows it. Otherwise return an empty string.
- Judge only what the text states. Do not infer a factor from the type of change alone.
- A backout plan that fully restores the previous state means the change is NOT irreversible."""

PRICE_PER_MTOK = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0)}   # (input, output) USD, Nov 2026


def change_text(ch) -> str:
    return "\n".join(f"{label}: {getattr(ch, attr) or ''}" for label, attr in (
        ("Short description", "short_description"), ("Description", "description"),
        ("Justification", "justification"), ("Implementation plan", "implementation_plan"),
        ("Backout plan", "backout_plan"), ("Test plan", "test_plan")))


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().strip(".\"'").lower()


class RegexReader:
    name = "heuristic"

    def read(self, changes: Iterable) -> dict[str, dict]:
        out = {}
        for ch in changes:
            text = " ".join([ch.description, ch.implementation_plan, ch.backout_plan, ch.test_plan])
            out[ch.key] = {}
            for n, rx in CLUES.items():
                m = rx.search(text)
                if m:
                    out[ch.key][n] = m.group(0)
        return out


class ClaudeReader:
    name = "claude"

    def __init__(self, model: str | None = None, effort: str = "medium", batch_size: int = 10,
                 cache: str | Path = "runs/claude-cache.json"):
        import anthropic  # lazy: offline runs do not need the SDK

        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            raise SystemExit("ANTHROPIC_API_KEY is not set in this terminal: export it first (see README).")
        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("CHG_CLAUDE_MODEL", "claude-opus-5-5")
        self.effort, self.batch_size = effort, batch_size
        self.cache_path = Path(cache)
        self.cache: dict[str, dict] = json.loads(self.cache_path.read_text()) if self.cache_path.exists() else {}
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cached_changes": 0, "read_changes": 0}
        self.rejected: list[dict] = []        # quotes not found in the change text
        self.errors: list[str] = []

    def _key(self, ch) -> str:
        return self.model + ":" + hashlib.sha256(change_text(ch).encode()).hexdigest()[:20]

    def _schema(self, ids: list[str]) -> dict:
        item = {"type": "object", "additionalProperties": False, "required": ["id", *FACTORS],
                "properties": {"id": {"type": "string", "enum": ids}, **{f: {"type": "string"} for f in FACTORS}}}
        return {"type": "object", "additionalProperties": False, "required": ["changes"],
                "properties": {"changes": {"type": "array", "items": item}}}

    def _call(self, batch: list) -> dict[str, dict]:
        ids = [f"C{i + 1}" for i in range(len(batch))]
        prompt = "Review these change requests.\n\n" + "\n\n".join(
            f"### {i}\n{change_text(ch)}" for i, ch in zip(ids, batch))
        resp = self.client.messages.create(
            model=self.model, max_tokens=16000, system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": self._schema(ids)}})
        self.usage["calls"] += 1
        self.usage["input_tokens"] += resp.usage.input_tokens
        self.usage["output_tokens"] += resp.usage.output_tokens
        if resp.stop_reason != "end_turn":
            raise RuntimeError(f"stop_reason={resp.stop_reason}")
        data = json.loads(next(b.text for b in resp.content if b.type == "text"))
        by_id = {d["id"]: d for d in data["changes"]}
        out = {}
        for i, ch in zip(ids, batch):
            d, found = by_id.get(i, {}), {}
            text = _norm(change_text(ch))
            for f in FACTORS:
                q = (d.get(f) or "").strip()
                if not q:
                    continue
                if len(_norm(q)) >= 3 and _norm(q) in text:
                    found[f] = q
                else:
                    self.rejected.append({"change": ch.key, "factor": f, "quote": q})
            out[self._key(ch)] = found
        return out

    def read(self, changes: Iterable) -> dict[str, dict]:
        changes = list(changes)
        todo = [ch for ch in changes if self._key(ch) not in self.cache]
        self.usage["cached_changes"] += len(changes) - len(todo)
        for s in range(0, len(todo), self.batch_size):
            batch = todo[s:s + self.batch_size]
            try:
                self.cache.update(self._call(batch))
                self.usage["read_changes"] += len(batch)
            except Exception as e:      # noqa: BLE001 - any API failure falls back to the regex reader
                self.errors.append(f"{type(e).__name__}: {str(e)[:200]}")
                fallback = RegexReader().read(batch)
                for ch in batch:
                    self.cache[self._key(ch)] = dict(fallback[ch.key], _fallback=True)
            self.save()
        return {ch.key: {k: v for k, v in self.cache[self._key(ch)].items() if not k.startswith("_")}
                for ch in changes}

    def save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps({k: v for k, v in self.cache.items() if not v.get("_fallback")},
                                              indent=1, sort_keys=True))

    def cost(self) -> float:
        pin, pout = PRICE_PER_MTOK.get(self.model, (0.0, 0.0))
        return (self.usage["input_tokens"] * pin + self.usage["output_tokens"] * pout) / 1e6

    def report(self) -> dict:
        return dict(self.usage, model=self.model, effort=self.effort, estimated_usd=round(self.cost(), 3),
                    rejected_quotes=len(self.rejected), errors=self.errors[:5], fallback_batches=len(self.errors))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", type=int, default=10, help="how many HISTORY changes to read (cost check)")
    ap.add_argument("--model")
    ap.add_argument("--effort", default="medium")
    a = ap.parse_args()
    from .connectors.local import LocalITSM
    from .history import HistoryAgent

    closed = HistoryAgent(LocalITSM()).closed[: a.sample]
    r = ClaudeReader(model=a.model, effort=a.effort, cache="runs/claude-sample-cache.json")
    found = r.read(closed)
    for ch in closed:
        print(f"{ch.key:10} {ch.short_description[:55]:55} {found[ch.key]}")
    rep = r.report()
    if rep["fallback_batches"]:
        raise SystemExit(f"Claude was not reached ({rep['errors'][0]}); the lines above are the regex fallback.")
    per = rep["estimated_usd"] / max(rep["read_changes"], 1)
    print(f"\n{json.dumps(rep)}\nAbout ${per:.4f} per change -> full M5 run (300 changes) about ${per * 300:.2f}.")


if __name__ == "__main__":
    main()
