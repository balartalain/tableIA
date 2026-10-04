"""
Motor de ejecución simplificado: PipelineExecutor + steps.

    FilterStep → AggregationOrPivotStep → CalculatedMetricsStep → WindowFunctionsStep
    → SortLimitStep
"""
from sheets_reports.engine.pipeline import (
    AggregationOrPivotStep,
    CalculatedMetricsStep,
    FilterStep,
    PipelineContext,
    PipelineExecutor,
    PipelineStep,
    ResultTooLargeError,
    SortLimitStep,
    WindowFunctionsStep,
)


__all__ = [
    "PipelineExecutor",
    "PipelineContext",
    "PipelineStep",
    "FilterStep",
    "AggregationOrPivotStep",
    "CalculatedMetricsStep",
    "WindowFunctionsStep",
    "SortLimitStep",
    "ResultTooLargeError",
]
