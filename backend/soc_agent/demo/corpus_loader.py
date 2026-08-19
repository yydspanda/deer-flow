"""Restricted local DataFrame pickle loading for SOC DEV workbenches."""

from __future__ import annotations

import pickle
import warnings
from pathlib import Path
from typing import Any

_ALLOWED_PICKLE_GLOBALS = {
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


class _RestrictedDataFrameUnpickler(pickle.Unpickler):
    def find_class(self, module: str, name: str) -> Any:
        if (module, name) not in _ALLOWED_PICKLE_GLOBALS:
            raise pickle.UnpicklingError(f"blocked pickle global: {module}.{name}")
        return super().find_class(module, name)


def load_restricted_dataframe_pickle(path: Path) -> Any:
    """Load one trusted local pandas DataFrame without allowing arbitrary globals."""

    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"configured SOC DEV corpus does not exist: {resolved}")
    try:
        import pandas as pd
    except ImportError as exc:
        raise ValueError("SOC DEV corpus workbenches require the backend pingan-dev dependencies") from exc
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        with resolved.open("rb") as handle:
            frame = _RestrictedDataFrameUnpickler(handle).load()
    if not isinstance(frame, pd.DataFrame):
        raise ValueError(f"expected pandas DataFrame, got {type(frame)!r}")
    return frame


__all__ = ["load_restricted_dataframe_pickle"]
