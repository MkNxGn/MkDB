"""
BlobStore — stores oversized record values as individual binary/text files.

Blob files live at: {store_path}/blobs/{record_id}.dat
The pseudo-segment string returned is "blobs/{record_id}.dat", which
LogManager.read() recognises and handles by reading the file directly.
"""

import os


def is_blob(segment_str: str) -> bool:
    """Return True if this segment string represents a blob file path."""
    return segment_str.startswith("blobs/")


def write_blob(store_path: str, record_id: str, data_str: str) -> tuple:
    """
    Write data_str to {store_path}/blobs/{record_id}.dat.

    Returns (path_str, 0, byte_size) where path_str is the pseudo-segment
    string "blobs/{record_id}.dat".
    """
    blobs_dir = os.path.join(store_path, "blobs")
    os.makedirs(blobs_dir, exist_ok=True)
    # Security: strip path separators from record_id
    safe_id = record_id.replace("/", "_").replace("\\", "_").replace("..", "_")
    file_path = os.path.join(blobs_dir, f"{safe_id}.dat")
    encoded = data_str.encode("utf-8")
    with open(file_path, "wb") as fh:
        fh.write(encoded)
    path_str = f"blobs/{safe_id}.dat"
    return (path_str, 0, len(encoded))


def read_blob(store_path: str, path_str: str) -> str:
    """
    Read and return the full content of a blob file.
    path_str is the pseudo-segment string "blobs/{record_id}.dat".
    """
    # Security: validate path stays within store_path/blobs/
    full_path = os.path.normpath(os.path.join(store_path, path_str))
    blobs_dir = os.path.normpath(os.path.join(store_path, "blobs"))
    if not full_path.startswith(blobs_dir + os.sep) and full_path != blobs_dir:
        raise ValueError(f"Blob path traversal rejected: {path_str!r}")
    with open(full_path, "rb") as fh:
        return fh.read().decode("utf-8")
