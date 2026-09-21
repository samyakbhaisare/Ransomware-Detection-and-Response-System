from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

from rdrs.fileaccess import open_read_shared


MAX_SAMPLE_SIZE = 64 * 1024


def calculate_entropy(data: bytes) -> float:
    """Calculate Shannon entropy for a byte sequence.

    Returns a value between 0.0 and 8.0.
    """
    if not data:
        return 0.0

    counts = Counter(data)
    total = len(data)

    entropy = 0.0

    for count in counts.values():
        probability = count / total
        entropy -= probability * math.log2(probability)

    return max(0.0, min(8.0, entropy))


def calculate_file_entropy(
    path: str | Path,
    sample_size: int = MAX_SAMPLE_SIZE,
) -> float:
    """Calculate Shannon entropy using the first sample_size bytes.

    The file is opened with delete-sharing so that inspecting a file never
    prevents another process from renaming or deleting it (see
    :func:`rdrs.fileaccess.open_read_shared`).
    """
    file_path = Path(path)

    if sample_size <= 0:
        raise ValueError("sample_size must be positive")

    with open_read_shared(file_path) as handle:
        data = handle.read(sample_size)

    return calculate_entropy(data)


def file_entropy(
    path: str | Path,
    sample_size: int = MAX_SAMPLE_SIZE,
) -> float:
    """Compatibility interface for real-time file monitoring.

    Calculates Shannon entropy from the target file using
    the configured sample size.
    """
    return calculate_file_entropy(
        path=path,
        sample_size=sample_size,
    )

