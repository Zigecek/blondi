"""Stránkované Qt table modely pro CRUD tabulky (fotky, běhy, SPZ).

Každý model načítá data asynchronně přes ``DbQueryWorker``, takže UI thread
nikdy nečeká na DB. Podporují ``canFetchMore`` / ``fetchMore`` pro lazy
scroll pagination a ``sort`` přes kliknutí na header.
"""

from spot_operator.ui.common.table_models.paged_table_model import (
    PagedTableModel,
    apply_default_sort_indicator,
)
from spot_operator.ui.common.table_models.photos_model import PhotosModel
from spot_operator.ui.common.table_models.plates_model import PlatesModel
from spot_operator.ui.common.table_models.runs_model import RunsModel

__all__ = [
    "PagedTableModel",
    "PhotosModel",
    "PlatesModel",
    "RunsModel",
    "apply_default_sort_indicator",
]
