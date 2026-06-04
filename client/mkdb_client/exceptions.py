"""
MkDB client exception hierarchy.

MkDBError
├── MkDBConnectionError      – low-level connect/disconnect failures
├── MkDBAuthError            – authentication or permission denied
├── MkDBTimeoutError         – request timed out waiting for a response
├── MkDBTransportError       – HTTP-level transport error (HTTP transport only)
└── MkDBServerError          – server returned an error payload (data-plane)
    ├── MkDBStoreNotFoundError   – named store does not exist
    ├── MkDBRecordNotFoundError  – record ID not found in store
    ├── MkDBStoreExistsError     – store already exists (create conflict)
    └── MkDBQueryError           – query filter is invalid or malformed
"""


class MkDBError(Exception):
    """Base class for all MkDB client exceptions."""


class MkDBConnectionError(MkDBError, ConnectionError):
    """Raised when a connection cannot be established or is lost."""


class MkDBAuthError(MkDBError, PermissionError):
    """Raised when the server rejects credentials or denies access."""


class MkDBTimeoutError(MkDBError, TimeoutError):
    """Raised when a response is not received within the timeout window."""


class MkDBTransportError(MkDBError):
    """Raised on HTTP-level errors when using the HTTP transport."""


class MkDBServerError(MkDBError):
    """Raised when the server returns an error status in a data-plane response."""


class MkDBStoreNotFoundError(MkDBServerError, KeyError):
    """Raised when the requested store does not exist on the server."""


class MkDBRecordNotFoundError(MkDBServerError, KeyError):
    """Raised when the requested record ID does not exist in the store."""


class MkDBStoreExistsError(MkDBServerError):
    """Raised when trying to create a store that already exists."""


class MkDBQueryError(MkDBServerError, ValueError):
    """Raised when the server rejects a query filter as invalid or malformed."""
