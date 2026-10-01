"""Motor de ejecución: el ejecutor (pasos comunes) y los planes de resultado (PLANS)."""
from sheets_reports.engine.executor import run  # noqa: F401
from sheets_reports.engine.plans import PLANS, ResultTooLargeError  # noqa: F401
