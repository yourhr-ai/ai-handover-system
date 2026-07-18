"""Shared decimal storage-size conversions used for billing and display."""

DECIMAL_BYTES_PER_GB = 1_000_000_000


def bytes_to_gb(size_bytes: int) -> float:
    """Convert bytes to decimal gigabytes (1 GB = 1,000,000,000 bytes)."""
    return max(0, int(size_bytes)) / DECIMAL_BYTES_PER_GB


def format_gb(value: float, decimal_places: int = 12) -> str:
    """Format decimal GB without scientific notation or redundant zeroes."""
    return f"{max(0.0, float(value)):.{decimal_places}f}".rstrip("0").rstrip(".") or "0"
