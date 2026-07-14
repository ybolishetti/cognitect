"""Tests for engine/generators/finetuned.py — FineTunedGenerator placeholder
(Architecture C, DRAFT 7).

10 tests, all fast, no network:
  1. Interface contract (name, ABC inheritance)
  2. Constructor safety (no raise on missing env — health-check contract)
  3. generate() raises NotImplementedError, never GeneratorFailure
  4. Version placeholder guard
  5. Factory wiring (by_name / from_env / unknown kind / all three names)
"""

from __future__ import annotations

import pytest

from engine.generators import (
    FINETUNED_VERSION,
    FineTunedGenerator,
    GeneratorFactory,
    GeneratorFailure,
    LayoutGenerator,
    PromptedGenerator,
    StubGenerator,
)


def test_name_is_finetuned():
    assert FineTunedGenerator().name == "finetuned"


def test_constructor_does_not_raise_on_missing_env(monkeypatch):
    monkeypatch.delenv("FINETUNED_MODEL_URL", raising=False)
    monkeypatch.delenv("FINETUNED_API_KEY", raising=False)
    FineTunedGenerator()  # must not raise


def test_generate_raises_not_implemented_error():
    with pytest.raises(NotImplementedError) as exc_info:
        FineTunedGenerator().generate(None)
    message = str(exc_info.value)
    assert "prompted" in message
    assert "engine/generators/finetuned.py" in message


def test_generate_does_not_raise_generator_failure():
    with pytest.raises(NotImplementedError):
        FineTunedGenerator().generate(None)
    # GeneratorFailure must NOT match — this is a config bug, not a
    # runtime candidate-generation failure, so best-of-N's
    # `except GeneratorFailure` block must not swallow it.
    try:
        FineTunedGenerator().generate(None)
    except GeneratorFailure:
        pytest.fail("FineTunedGenerator.generate() must not raise GeneratorFailure")
    except NotImplementedError:
        pass


def test_finetuned_version_is_placeholder_string():
    assert FINETUNED_VERSION.startswith("placeholder-")


def test_factory_by_name_finetuned_returns_finetuned_generator():
    gen = GeneratorFactory.by_name("finetuned")
    assert isinstance(gen, FineTunedGenerator)


def test_factory_from_env_finetuned(monkeypatch):
    monkeypatch.setenv("LAYOUT_GENERATOR", "finetuned")
    assert GeneratorFactory.from_env().name == "finetuned"


def test_factory_unknown_kind_still_raises_value_error():
    with pytest.raises(ValueError, match="Unknown LAYOUT_GENERATOR"):
        GeneratorFactory.by_name("nonsense")


def test_all_three_generator_names_route_correctly(monkeypatch):
    monkeypatch.setenv("COGNITECT_CLAUDE_API_KEY", "test-key")
    assert isinstance(GeneratorFactory.by_name("stub"), StubGenerator)
    assert isinstance(GeneratorFactory.by_name("prompted"), PromptedGenerator)
    assert isinstance(GeneratorFactory.by_name("finetuned"), FineTunedGenerator)


def test_finetuned_generator_is_a_layout_generator():
    assert isinstance(FineTunedGenerator(), LayoutGenerator)
