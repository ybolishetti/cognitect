"""Architecture C — LayoutGenerator interface + implementations."""

from engine.generators.base import (
    GeneratorFactory,
    GeneratorFailure,
    GeneratorMetadata,
    LayoutGenerator,
)
from engine.generators.stub import STUB_VERSION, StubGenerator

__all__ = [
    "GeneratorFactory",
    "GeneratorFailure",
    "GeneratorMetadata",
    "LayoutGenerator",
    "StubGenerator",
    "STUB_VERSION",
]
