"""scripts/validate_concepts.py and scripts/build_concept_graph.py, without network."""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import ClassVar

import pytest
from pydantic import SecretStr

from papermind.config import Settings
from papermind.graph.testing import sample_catalog, write_sample_files

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"_script_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def validate_cli() -> ModuleType:
    return load_script("validate_concepts")


@pytest.fixture(scope="module")
def build() -> ModuleType:
    return load_script("build_concept_graph")


# ---------------------------------------------------------------- validate_concepts


def test_validate_cli_on_valid_data(tmp_path, validate_cli, capsys):
    concepts_path, _ = write_sample_files(tmp_path)
    assert validate_cli.main(["--concepts", str(concepts_path)]) == 0
    out = capsys.readouterr().out
    assert "concepts   12" in out and "edges      11" in out and "max depth  3" in out
    assert "Result: 0 error(s), 0 warning(s)" in out


def test_validate_cli_reports_cycles_and_fails(tmp_path, validate_cli, capsys):
    concepts_path, _ = write_sample_files(tmp_path)
    payload = json.loads(concepts_path.read_text())
    by_id = {c["id"]: c for c in payload["concepts"]}
    by_id["exponential-function"]["requires"] = ["transformer"]
    concepts_path.write_text(json.dumps(payload))
    assert validate_cli.main(["--concepts", str(concepts_path)]) == 1
    out = capsys.readouterr().out
    assert "cycle: attention -> softmax -> exponential-function -> transformer -> attention" in out
    assert "Stats" in out  # still shown while errors remain


def test_validate_cli_strict_warnings_and_missing_files(tmp_path, validate_cli, capsys):
    concepts_path, _ = write_sample_files(tmp_path)
    payload = json.loads(concepts_path.read_text())
    payload["concepts"][0]["tier"] = "advanced"  # linear-algebra now outranks what requires it
    concepts_path.write_text(json.dumps(payload))
    assert validate_cli.main(["--concepts", str(concepts_path)]) == 0
    assert validate_cli.main(["--concepts", str(concepts_path), "--strict"]) == 1
    assert validate_cli.main(["--concepts", str(tmp_path / "missing.json")]) == 2
    assert "tier-inversion" in capsys.readouterr().out


# ---------------------------------------------------------------- build_concept_graph: merging


def test_find_path(build):
    graph = {"a": ["b"], "b": ["c"], "c": []}
    assert build.find_path(graph, "a", "c") == ["a", "b", "c"]
    assert build.find_path(graph, "c", "a") is None
    assert build.find_path(graph, "a", "a") == ["a"]


def test_merge_answer_decisions(build):
    catalog = sample_catalog()
    requires = {cid: list(catalog.prerequisites_of(cid)) for cid in catalog.concepts}
    decisions = build.merge_answer(
        requires,
        catalog.index,
        "attention",
        [
            "Softmax",  # exists
            "Transformers",  # transformer -> attention already: would close a cycle
            "Exponentials",  # already implied via softmax
            "Linear Algebra",  # implied via dot-product
            "Attention mechanism",  # itself
            "Mean and variance",  # new
            "quantum chromodynamics",  # unresolvable
        ],
    )
    actions = [(d.proposed, d.action) for d in decisions]
    assert actions == [
        ("Softmax", "exists"),
        ("Transformers", "cycle"),
        ("Exponentials", "implied"),
        ("Linear Algebra", "implied"),
        ("Attention mechanism", "self"),
        ("Mean and variance", "added"),
        ("quantum chromodynamics", "unresolved"),
    ]
    assert decisions[1].detail == "attention -> transformer -> attention"
    assert requires["attention"] == ["softmax", "dot-product", "mean-and-variance"]


def test_merge_checks_cycles_against_edges_added_earlier_in_the_run(build):
    catalog = sample_catalog()
    requires = {cid: list(catalog.prerequisites_of(cid)) for cid in catalog.concepts}
    first = build.merge_answer(requires, catalog.index, "derivative", ["Linear Algebra"])
    second = build.merge_answer(requires, catalog.index, "linear-algebra", ["Derivative"])
    assert first[0].action == "added"
    assert second[0].action == "cycle"
    assert second[0].detail == "linear-algebra -> derivative -> linear-algebra"


# ---------------------------------------------------------------- build_concept_graph: API calls (faked)


class FakeMessages:
    def __init__(self, answers: dict[str, list[str]]) -> None:
        self.answers = answers
        self.calls: list[dict] = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        request = kwargs["messages"][0]["content"]
        concept_id = request.split("[", 1)[1].split("]", 1)[0]
        FakeAnthropic.events.append(("start", concept_id))
        await asyncio.sleep(0.01)
        FakeAnthropic.events.append(("end", concept_id))
        parsed = kwargs["output_format"](concept=concept_id, prerequisites=self.answers.get(concept_id, []))
        usage = SimpleNamespace(
            input_tokens=100, output_tokens=20, cache_creation_input_tokens=0, cache_read_input_tokens=0
        )
        return SimpleNamespace(
            parsed_output=parsed, usage=usage, stop_reason="end_turn", _request_id="req_test"
        )


class FakeAnthropic:
    instances: ClassVar[list["FakeAnthropic"]] = []
    answers: ClassVar[dict[str, list[str]]] = {}
    events: ClassVar[list[tuple[str, str]]] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.messages = FakeMessages(self.answers)
        FakeAnthropic.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


@pytest.fixture
def fake_api(build, monkeypatch):
    FakeAnthropic.instances = []
    FakeAnthropic.events = []
    FakeAnthropic.answers = {
        "derivative": ["Linear Algebra", "limits"],
        "gradient-descent": ["Derivative", "Linear Algebra"],
    }
    monkeypatch.setattr(build, "AsyncAnthropic", FakeAnthropic)
    monkeypatch.setattr(build, "get_settings", lambda: Settings(anthropic_api_key=SecretStr("sk-test")))
    return FakeAnthropic


async def test_request_shape(build, fake_api):
    catalog = sample_catalog()
    client = fake_api()
    usage = build.UsageTotals()
    answer = await build.ask(
        client,
        model="claude-opus-5-5",
        system=build.SYSTEM_TEMPLATE.format(catalog=build.render_catalog(catalog)),
        spec=catalog.concepts["derivative"],
        area_name="Mathematics",
        usage=usage,
    )
    (call,) = client.messages.calls
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"] == {"effort": "low"}
    assert call["output_format"] is build.PrerequisiteAnswer
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "- Softmax [softmax] - ml, core" in call["system"][0]["text"]
    assert "Concept: Derivative [derivative]" in call["messages"][0]["content"]
    assert answer.prerequisites == ["Linear Algebra", "limits"]
    assert (usage.calls, usage.input_tokens, usage.request_ids) == (1, 100, ["req_test"])


async def test_dry_run_does_not_write(tmp_path, build, fake_api, capsys):
    concepts_path, _ = write_sample_files(tmp_path)
    before = concepts_path.read_text()
    args = build.argparse.Namespace(
        concepts=concepts_path, areas=None, only=["derivative"], limit=None, concurrency=2, dry_run=True
    )
    assert await build.run(args) == 0
    assert concepts_path.read_text() == before
    out = capsys.readouterr().out
    assert "+ added      'Linear Algebra' -> linear-algebra" in out
    assert "? unresolved 'limits'" in out
    assert "usage: 1 call(s), input 100, output 20" in out


async def test_run_merges_into_the_file_and_keeps_it_valid(tmp_path, build, fake_api):
    concepts_path, areas_path = write_sample_files(tmp_path)
    args = build.argparse.Namespace(
        concepts=concepts_path,
        areas=None,
        only=["derivative", "gradient-descent"],
        limit=None,
        concurrency=2,
        dry_run=False,
    )
    assert await build.run(args) == 0
    payload = json.loads(concepts_path.read_text())
    requires = {c["id"]: c["requires"] for c in payload["concepts"]}
    assert requires["derivative"] == ["linear-algebra"]
    # gradient-descent -> derivative exists; -> linear-algebra is now implied through derivative.
    assert requires["gradient-descent"] == ["derivative"]
    assert payload["concepts"][0].keys() == {
        "id",
        "name",
        "aliases",
        "area",
        "tier",
        "description",
        "requires",
    }

    from papermind.graph.seed import load_catalog

    assert load_catalog(concepts_path, areas_path).is_valid


async def test_missing_api_key_exits_before_any_call(tmp_path, build, monkeypatch):
    monkeypatch.setattr(build, "get_settings", lambda: Settings(anthropic_api_key=None, _env_file=None))
    args = build.argparse.Namespace(
        concepts=tmp_path / "c.json", areas=None, only=None, limit=1, concurrency=1, dry_run=True
    )
    assert await build.run(args) == 2


async def test_first_request_warms_the_cache_before_the_rest_fan_out(tmp_path, build, fake_api):
    concepts_path, _ = write_sample_files(tmp_path)
    args = build.argparse.Namespace(
        concepts=concepts_path, areas=None, only=None, limit=4, concurrency=4, dry_run=True
    )
    assert await build.run(args) == 0
    events = fake_api.events
    assert events[:2] == [("start", "linear-algebra"), ("end", "linear-algebra")]
    assert [kind for kind, _ in events[2:5]] == ["start", "start", "start"]  # the rest run concurrently
