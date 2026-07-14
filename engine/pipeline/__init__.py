"""Best-of-N pipeline -- composes generators, Layer A/B/C, and scoring.

See DRAFT_ARCH_C_6_BEST_OF_N.
"""

from engine.pipeline.best_of_n import BestOfNResult, PipelineFailure, run_best_of_n

__all__ = [
    "BestOfNResult",
    "PipelineFailure",
    "run_best_of_n",
]
