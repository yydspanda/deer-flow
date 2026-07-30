"""Narrow pickle loader for the known local Pandas alert samples."""

from __future__ import annotations

import pickle
import warnings
from collections.abc import Collection
from pathlib import Path
from typing import Any

import pandas as pd

ALLOWED_PICKLE_GLOBALS = {
    ("pandas", "DataFrame"),
    ("pandas", "Index"),
    ("pandas", "RangeIndex"),
    ("pandas.core.frame", "DataFrame"),
    ("pandas.core.internals.managers", "BlockManager"),
    ("pandas._libs.internals", "_unpickle_block"),
    ("numpy.core.numeric", "_frombuffer"),
    ("numpy._core.numeric", "_frombuffer"),
    ("numpy", "dtype"),
    ("builtins", "slice"),
    ("numpy.core.multiarray", "_reconstruct"),
    ("numpy._core.multiarray", "_reconstruct"),
    ("numpy", "ndarray"),
    ("collections", "Counter"),
    ("pandas.core.indexes.base", "_new_Index"),
    ("pandas.core.indexes.base", "Index"),
    ("pandas.core.indexes.range", "RangeIndex"),
}

DEFAULT_REQUIRED_COLUMNS = frozenset(
    {
        "alert_id",
        "alert_full_data",
        "agent_response",
    }
)


class RestrictedDataFrameUnpickler(pickle.Unpickler):
    """Allow only globals required by the known Pandas DataFrame format."""

    def find_class(self, module: str, name: str) -> Any:
        if (module, name) not in ALLOWED_PICKLE_GLOBALS:
            raise pickle.UnpicklingError(f"blocked pickle global: {module}.{name}")
        return super().find_class(module, name)


def load_dataframe_pickle(
    path: Path,
    *,
    required_columns: Collection[str] = DEFAULT_REQUIRED_COLUMNS,
) -> pd.DataFrame:
    """Load a local sample and enforce its minimum DataFrame contract."""

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        with path.open("rb") as handle:
            frame = RestrictedDataFrameUnpickler(handle).load()
    if not isinstance(frame, pd.DataFrame):
        raise TypeError(f"expected pandas DataFrame, got {type(frame)!r}")
    missing = set(required_columns).difference(frame.columns)
    if missing:
        raise ValueError(f"missing required columns: {sorted(missing)}")
    return frame
