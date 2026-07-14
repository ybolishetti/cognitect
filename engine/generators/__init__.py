"""Architecture C — LayoutGenerator interface + implementations."""

from engine.generators.base import (
    GeneratorFactory,
    GeneratorFailure,
    GeneratorMetadata,
    LayoutGenerator,
)
from engine.generators.finetuned import FINETUNED_VERSION, FineTunedGenerator
from engine.generators.prompted import PromptedGenerator
from engine.generators.prompts import PROMPTED_VERSION
from engine.generators.stub import STUB_VERSION, StubGenerator

__all__ = [
    "GeneratorFactory",
    "GeneratorFailure",
    "GeneratorMetadata",
    "LayoutGenerator",
    "PromptedGenerator",
    "PROMPTED_VERSION",
    "FineTunedGenerator",
    "FINETUNED_VERSION",
    "StubGenerator",
    "STUB_VERSION",
]
