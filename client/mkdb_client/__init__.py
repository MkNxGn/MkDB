"""
mkdb_client — Python client SDK for MkDB.

Usage:
    from mkdb_client import MkDBClient

    client = MkDBClient(host="127.0.0.1", port=9001)
    client.connect()
    client.set("products", "p001", {"name": "Widget", "price": 9.99})
    record = client.get("products", "p001")
    client.close()
"""

from mkdb_client.client import MkDBClient
from mkdb_client.query_builder import Q
from mkdb_client.controller import MkDBController, ControllerError
from mkdb_client.exceptions import (
    MkDBError,
    MkDBConnectionError,
    MkDBAuthError,
    MkDBTimeoutError,
    MkDBServerError,
    MkDBTransportError,
    MkDBStoreNotFoundError,
    MkDBRecordNotFoundError,
    MkDBStoreExistsError,
    MkDBQueryError,
)
from mkdb_client.responses import (
    GetResponse,
    WriteResponse,
    DeleteResponse,
    QueryResponse,
    GenerateIdResponse,
)

__version__ = "0.1.0"
__all__ = [
    "MkDBClient",
    "MkDBController",
    "ControllerError",
    "MkDBError",
    "MkDBConnectionError",
    "MkDBAuthError",
    "MkDBTimeoutError",
    "MkDBServerError",
    "MkDBTransportError",
    "MkDBStoreNotFoundError",
    "MkDBRecordNotFoundError",
    "MkDBStoreExistsError",
    "MkDBQueryError",
    "GetResponse",
    "WriteResponse",
    "DeleteResponse",
    "QueryResponse",
    "GenerateIdResponse",
]
