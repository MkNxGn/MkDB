# Record Tracking & Patching

MkDB provides a "Record Tracking" system that allows you to fetch an object, modify its properties, and save only the changed fields back to the server using the `.patch()` method.

## Enabling Tracking

Tracking is disabled by default to keep the client lightweight. You can enable it globally when initializing the client:

```python
from mkdb_client import MkDBClient

# Enable global tracking
client = MkDBClient(track_records=True)

# Records returned by .get() will now be SnapshotBaseObjects
resp = client.get("users", "user_123")
user = resp.data 

user.name = "John Doe"
user.patch() # Sends only {"name": "John Doe"} to the server
```

## Using Custom Models

You can use your own classes with tracking by inheriting from `SnapshotBaseObject`. The client will instantiate your class using `YourClass(data)` and then bind the store/client metadata internally.

```python
from mkdb_client.responses import SnapshotBaseObject

class User(SnapshotBaseObject):
    def deactivate(self):
        self.active = False
        self.patch()

# Pass your class to the .get() method
user = client.get("users", "u1", as_type=User).data
user.deactivate()
```

### Metadata Injection
To avoid conflicts with your class's `__init__` method, the client injects metadata using a `.bind()` method (if present) or by directly setting attributes:
- `self.__id__`: The record's unique ID.
- `self.__store__`: The name of the store.
- `self.__client__`: The `MkDBClient` instance.

## How Patching Works

When `patch()` is called:
1. The object creates a "flattened" version of its current state (including nested objects using dot-notation).
2. It compares this against a snapshot taken when the object was first loaded (or last patched).
3. It identifies:
   - **Modified values**: Updated in the delta.
   - **New fields**: Added to the delta.
   - **Deleted fields**: Set to `None` in the delta (the server interprets `None` as a request to remove the key).
4. It sends this delta via a standard `write` request.

## Nested Object Support

If the store has `nested_queries_enabled: true` in its configuration, `patch()` works seamlessly with deep structures:

```python
# Initial record: {"settings": {"theme": "dark", "notifications": true}}
user = client.get("users", "u1", track_records=True).data

user.settings["theme"] = "light"
del user.settings["notifications"]

user.patch() 
# Sends: {"settings.theme": "light", "settings.notifications": None}
# The server merges this into: {"settings": {"theme": "light"}}
```

> **Note:** If `nested_queries_enabled` is `false` on the server, dot-notation keys like `settings.theme` will be treated as literal field names instead of path traversals.
