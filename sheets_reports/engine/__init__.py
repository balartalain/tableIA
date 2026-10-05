"""El motor de consultas: pasos atómicos en `engine/steps/` y el contexto de la hoja."""
from sheets_reports.engine.steps import build_query_result, run_steps
from sheets_reports.engine.steps.aggregation import AGGREGATIONS, ResultTooLargeError, agg_name

__all__ = ["run_steps", "build_query_result", "AGGREGATIONS", "agg_name", "ResultTooLargeError"]
