from types import SimpleNamespace

import pytest

from papermind.llm.pricing import Usage, call_cost, cost_of, price_for


def test_opus_5_5_rates_per_million_tokens() -> None:
    p = price_for("claude-opus-5-5")
    assert (p.input, p.output, p.cache_write, p.cache_read) == (4.0, 20.0, 5.0, 0.20)
    # Cache writes are the 5-minute rate: 1.25x input.
    assert p.cache_write == pytest.approx(1.25 * p.input)


def test_cost_adds_every_token_class() -> None:
    usage = Usage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_input_tokens=1_000_000,
        cache_creation_input_tokens=1_000_000,
    )
    assert cost_of(usage, "claude-opus-5-5") == pytest.approx(4 + 20 + 5 + 0.2)


def test_dated_or_suffixed_ids_price_as_their_family() -> None:
    assert price_for("claude-opus-5-5-20261001") == price_for("claude-opus-5-5")
    # "claude-opus-5" must not swallow "claude-opus-5-5".
    assert price_for("claude-opus-5-5") != price_for("claude-opus-5")


def test_unknown_model_prices_as_the_default() -> None:
    assert price_for("some-future-model") == price_for("claude-opus-5-5")


def test_usage_sums_and_prompt_tokens() -> None:
    total = Usage(10, 1, 100, 5) + Usage(20, 2, 200, 0)
    assert total == Usage(30, 3, 300, 5)
    assert total.prompt_tokens == 30 + 300 + 5


def test_call_cost_prices_each_iteration_at_its_own_model() -> None:
    usage = SimpleNamespace(
        input_tokens=200,
        output_tokens=20,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
        iterations=[
            SimpleNamespace(type="message", model="claude-opus-5-5", input_tokens=100, output_tokens=0),
            SimpleNamespace(
                type="fallback_message", model="claude-opus-5", input_tokens=100, output_tokens=20
            ),
        ],
    )
    expected = cost_of(Usage(input_tokens=100), "claude-opus-5-5") + cost_of(
        Usage(input_tokens=100, output_tokens=20), "claude-opus-5"
    )
    assert call_cost(usage, "claude-opus-5-5") == pytest.approx(expected)


def test_call_cost_without_iterations_uses_the_totals() -> None:
    usage = SimpleNamespace(
        input_tokens=1000, output_tokens=100, cache_read_input_tokens=0, cache_creation_input_tokens=0
    )
    assert call_cost(usage, "claude-opus-5-5") == pytest.approx((1000 * 4 + 100 * 20) / 1e6)
