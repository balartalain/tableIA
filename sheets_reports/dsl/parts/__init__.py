"""Piezas del data_spec. Importar este paquete registra las del core en SPEC_PARTS; una pieza
nueva es una subclase de SpecPart decorada con @SPEC_PARTS.register (en un widget de
extensión, en su mismo archivo)."""
from sheets_reports.dsl.parts.base import (  # noqa: F401
    SPEC_PARTS,
    ColumnListPart,
    Rows,
    SpecPart,
    UnsupportedPart,
    bounds_text,
    column_message,
)
from sheets_reports.dsl.parts.columns import Columns, Dimensions, Pivots  # noqa: F401
from sheets_reports.dsl.parts.metrics import PIVOT_MULTIMETRIC_MSG, Metrics  # noqa: F401
from sheets_reports.dsl.parts.filtering import Filters, Having, Limit, OrderBy, Sort, TopN  # noqa: F401
from sheets_reports.dsl.parts.trend import TrendBy  # noqa: F401
