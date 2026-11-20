"""The Claude text reader, with a stand-in client (no API key, no network)."""
import json
from types import SimpleNamespace

from agents.connectors.local import LocalITSM
from agents.models import Change
from agents.orchestrator import ChangeOrchestrator
from agents.text_reader import FACTORS, ClaudeReader, RegexReader


class FakeMessages:
    """Returns a quote for 'irreversible' on every change (a real one, then a made-up one), nothing else."""
    def __init__(self, invent=False):
        self.calls, self.invent = 0, invent

    def create(self, **kw):
        self.calls += 1
        schema = kw["output_config"]["format"]["schema"]
        ids = schema["properties"]["changes"]["items"]["properties"]["id"]["enum"]
        prompt = kw["messages"][0]["content"]
        out = []
        for i in ids:
            block = prompt.split(f"### {i}\n", 1)[1]
            backout = block.split("Backout plan: ", 1)[1].split("\n", 1)[0]
            quote = "this text is not in the change" if self.invent else " ".join(backout.split()[:4])
            out.append({"id": i, **{f: "" for f in FACTORS}, "irreversible": quote})
        text = json.dumps({"changes": out})
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)],
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=200))


def reader(tmp_path, invent=False):
    r = ClaudeReader.__new__(ClaudeReader)
    r.client = SimpleNamespace(messages=FakeMessages(invent))
    r.model, r.effort, r.batch_size = "claude-opus-5-5", "medium", 10
    r.cache_path, r.cache = tmp_path / "cache.json", {}
    r.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cached_changes": 0, "read_changes": 0}
    r.rejected, r.errors = [], []
    return r


def chg(key, backout):
    return Change(key=key, short_description="Upgrade", description="d", implementation_plan="1. a\n2. b",
                  backout_plan=backout, test_plan="t")


def test_quotes_must_be_in_the_text(tmp_path):
    r = reader(tmp_path)
    got = r.read([chg("a", "Restore the database from last night's backup.")])
    assert got["a"] == {"irreversible": "Restore the database from"}
    r2 = reader(tmp_path / "x", invent=True)
    assert r2.read([chg("a", "Restore the database from last night's backup.")])["a"] == {}
    assert r2.rejected and r2.rejected[0]["factor"] == "irreversible"


def test_batches_cache_and_cost(tmp_path):
    r = reader(tmp_path)
    changes = [chg(f"c{i}", f"Roll back step {i} by redeploying") for i in range(23)]
    r.read(changes)
    assert r.client.messages.calls == 3 and r.usage["read_changes"] == 23
    r.read(changes)                                       # second time: all from the cache
    assert r.client.messages.calls == 3 and r.usage["cached_changes"] == 23
    assert abs(r.cost() - (3000 * 4 + 600 * 20) / 1e6) < 1e-9
    assert json.loads((tmp_path / "cache.json").read_text())


def test_api_failure_falls_back_to_regexes(tmp_path):
    r = reader(tmp_path)
    r.client.messages.create = lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    got = r.read([chg("a", "No rollback possible after the migration.")])
    assert got["a"] == {"irreversible": "No rollback"} and r.errors
    assert json.loads((tmp_path / "cache.json").read_text()) == {}      # fallbacks are not cached


def test_regex_reader_matches_risk_features():
    assert RegexReader().read([chg("a", "This step cannot be undone.")])["a"] == {"irreversible": "cannot be undone"}


def test_orchestrator_uses_the_reader_for_history_and_new_changes(tmp_path):
    r = reader(tmp_path)
    orch = ChangeOrchestrator(LocalITSM(), reader=r)
    assert len(orch.clues) == orch.model.n_train          # every history change was read once
    res = orch.run(write=False)
    a = res[0]
    assert a.risk["text_evidence"].get("irreversible") and "From the change text (Claude)" in a.summary
