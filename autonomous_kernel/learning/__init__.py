"""Question-bound learning loops. Perception and journals remain source truth."""

from .direction_assembly import (
    DirectionAssemblyError,
    assemble_and_record_direction_question,
    assemble_direction_question,
    direction_assembly_projection,
)
from .direction_loop import (
    DIRECTION_QUESTION_REF,
    DirectionLoopError,
    btc_spot_graph,
    frozen_direction_registry,
    process_canonical_direction_batch,
    process_canonical_direction_batches,
    question_learning_projection,
)
from .liquidity_assembly import (
    LiquidityAssemblyError,
    assemble_and_record_liquidity_question,
    assemble_liquidity_question,
    liquidity_assembly_projection,
)
from .magnitude_assembly import (
    MagnitudeAssemblyError,
    assemble_and_record_magnitude_question,
    assemble_magnitude_question,
    magnitude_assembly_projection,
)
from .magnitude_loop import (
    MAGNITUDE_QUESTION_REF,
    MagnitudeLoopError,
    process_canonical_magnitude_batch,
    process_canonical_magnitude_batches,
)
from .volatility_assembly import (
    VolatilityAssemblyError,
    assemble_and_record_volatility_question,
    assemble_volatility_question,
    volatility_assembly_projection,
)
from .volatility_loop import (
    VOLATILITY_QUESTION_REF,
    VolatilityLoopError,
    process_canonical_volatility_batch,
    process_canonical_volatility_batches,
)

__all__ = (
    "DIRECTION_QUESTION_REF",
    "LIQUIDITY_QUESTION_REF",
    "MAGNITUDE_QUESTION_REF",
    "VOLATILITY_QUESTION_REF",
    "DirectionAssemblyError",
    "DirectionLoopError",
    "LiquidityAssemblyError",
    "LiquidityLoopError",
    "MagnitudeAssemblyError",
    "MagnitudeLoopError",
    "VolatilityAssemblyError",
    "VolatilityLoopError",
    "assemble_and_record_direction_question",
    "assemble_and_record_liquidity_question",
    "assemble_and_record_magnitude_question",
    "assemble_and_record_volatility_question",
    "assemble_direction_question",
    "assemble_liquidity_question",
    "assemble_magnitude_question",
    "assemble_volatility_question",
    "btc_spot_graph",
    "direction_assembly_projection",
    "frozen_direction_registry",
    "liquidity_assembly_projection",
    "magnitude_assembly_projection",
    "volatility_assembly_projection",
    "process_canonical_direction_batch",
    "process_canonical_direction_batches",
    "process_canonical_liquidity_batch",
    "process_canonical_liquidity_batches",
    "process_canonical_magnitude_batch",
    "process_canonical_magnitude_batches",
    "process_canonical_volatility_batch",
    "process_canonical_volatility_batches",
    "question_learning_projection",
)
