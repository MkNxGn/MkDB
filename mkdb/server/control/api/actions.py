from mkdb.db import mkdb
from mkdb.config.db import mkdb_config

def api_create_store(database: mkdb, data: dict):
    """Create a new store in the config."""
    store_name:str = data.get("name", "").strip()
    description:str = data.get("description", "").strip()
    
    if not store_name:
        raise ValueError("Store name is required")
    
    if database is None:
        raise RuntimeError("Database not initialized")
    
    try:
        database.config.new_store(store_name, description)
        database.setup()
        return {
            "store_name": store_name,
            "message": f"Store '{store_name}' created successfully"
        }
    except ValueError as e:
        raise e

def api_delete_store(database: mkdb, data: dict):
    """Delete a store from the config and archive its data directory."""
    import os
    import shutil

    store_name: str = data.get("name", "").strip()

    if not store_name:
        raise ValueError("Store name is required")

    if database is None:
        raise RuntimeError("Database not initialized")

    if store_name not in database.config.stores:
        raise ValueError(f"Store '{store_name}' does not exist")

    # Tear down the live store object first to release all file locks
    store_obj = database.stores.pop(store_name, None)
    if store_obj is not None:
        try:
            store_obj.teardown()
        except Exception:
            pass

    del database.config.stores[store_name]
    database.config.save()

    # Move store data directory to __archived__/<store_name>
    stores_dir = os.path.join(database.config.base_path, "stores")
    src = os.path.join(stores_dir, store_name)
    if os.path.isdir(src):
        archive_dir = os.path.join(stores_dir, "__archived__")
        os.makedirs(archive_dir, exist_ok=True)
        dst = os.path.join(archive_dir, store_name)
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.move(src, dst)

    return {
        "store_name": store_name,
        "message": f"Store '{store_name}' archived successfully"
    }

def api_list_stores(database: mkdb, data: dict):
    """List all stores from the config."""
    if database is None:
        raise RuntimeError("Database not initialized")
    
    return {
        "stores": [{"name": x, "description": database.config.stores[x].description} for x in database.config.stores],
        "count": len(database.config.stores)
    }

def api_update_store(database: mkdb, data: dict):
    """Update a store's description."""
    store_name: str = data.get("name", "").strip()
    description: str = data.get("description", "").strip()
    
    if not store_name:
        raise ValueError("Store name is required")
    
    if database is None:
        raise RuntimeError("Database not initialized")
    
    if store_name not in database.config.stores:
        raise ValueError(f"Store '{store_name}' does not exist")
    
    database.config.stores[store_name].description = description
    database.config.save()

    return {
        "store_name": store_name,
        "description": description,
        "message": f"Store '{store_name}' updated successfully"
    }

def api_generate_id(database: mkdb, data: dict):
    """Generate one or more unique record IDs for a store.

    Required: name (store name)
    Optional: count (int, 1–100, default 1)

    Delegates to store.generate_id() which respects entity_config.token_chars,
    entity_config.token_length, and entity_config.auto_expand.
    """
    store_name: str = data.get("name", "").strip()
    count: int = max(1, min(100, int(data.get("count", 1))))

    if not store_name:
        raise ValueError("Store name is required")
    if database is None:
        raise RuntimeError("Database not initialized")
    if store_name not in database.stores:
        raise ValueError(f"Store '{store_name}' does not exist")

    store_obj = database.stores[store_name]
    ids: list[str] = []
    for _ in range(count):
        ids.append(store_obj.generate_id())

    return {"ids": ids, "count": len(ids)}

def api_get_store_config(database: mkdb, data: dict):
    """Get detailed configuration for a store."""
    store_name: str = data.get("name", "").strip()
    
    if not store_name:
        raise ValueError("Store name is required")
    
    if database is None:
        raise RuntimeError("Database not initialized")
    
    if store_name not in database.config.stores:
        raise ValueError(f"Store '{store_name}' does not exist")
    
    store = database.config.stores[store_name]
    result = dict(store.json)
    result["entity_health"] = store.entity_config.health
    result["recursion_health"] = store.query_recursion_limit_health
    return result

def api_update_store_config(database: mkdb, data: dict):
    """Update detailed configuration for a store."""
    store_name: str = data.get("name", "").strip()
    
    if not store_name:
        raise ValueError("Store name is required")
    
    if database is None:
        raise RuntimeError("Database not initialized")
    
    if store_name not in database.config.stores:
        raise ValueError(f"Store '{store_name}' does not exist")
    
    store = database.config.stores[store_name]
    
    # Update sub-configs using their constructors from JSON
    if "file_config" in data:
        from mkdb.config.db import file_config
        store.file_config = file_config(data["file_config"])
    if "entity_config" in data:
        from mkdb.config.db import entity_config
        store.entity_config = entity_config(data["entity_config"])
    if "ram_config" in data:
        from mkdb.config.db import ram_config
        store.ram_config = ram_config(data["ram_config"])
    if "rate_limit" in data:
        from mkdb.config.db import store_rate_limit
        store.rate_limit = store_rate_limit(data["rate_limit"])
    if "slow_query_threshold_ms" in data:
        store.slow_query_threshold_ms = float(data["slow_query_threshold_ms"])
    if "max_query_execution_time_ms" in data:
        store.max_query_execution_time_ms = float(data["max_query_execution_time_ms"])
    if "query_recursion_limit" in data:
        store.query_recursion_limit = int(data["query_recursion_limit"])
    if "client_id_header" in data:
        store.client_id_header = str(data["client_id_header"]).strip()
    if "protect_reads" in data:
        store.protect_reads = bool(data["protect_reads"])
    if "nested_queries_enabled" in data:
        store.nested_queries_enabled = bool(data["nested_queries_enabled"])
    if "description" in data:
        store.description = data["description"]
    if "schema_config" in data:
        from mkdb.config.db import schema_config, field_schema
        raw_fields = data["schema_config"].get("fields", {})
        new_schema = schema_config({})
        new_schema.fields = {
            k: field_schema(v) if isinstance(v, dict) else v
            for k, v in raw_fields.items()
        }
        store.schema_config = new_schema

    database.config.save()

    # If schema changed, rebuild live query engine indexes for the running store so
    # changes take effect immediately without needing a server restart.
    if "schema_config" in data and store_name in database.stores:
        import threading as _threading
        store_obj = database.stores[store_name]
        qe = getattr(store_obj, "query_engine", None)
        if qe is not None:
            def _do_rebuild():
                for field_name, fs in store.schema_config.fields.items():
                    q = getattr(fs, "queryable", False)
                    if q:
                        try:
                            qe.rebuild_index(field_name)
                        except Exception as _exc:
                            import logging as _log
                            _log.getLogger(__name__).warning(
                                "schema update: rebuild_index('%s') failed: %s", field_name, _exc
                            )
            _threading.Thread(target=_do_rebuild, daemon=True,
                              name=f"schema-rebuild-{store_name}").start()

    response: dict = {"store_name": store_name, "message": f"Store '{store_name}' config updated successfully"}
    if "entity_config" in data:
        from mkdb.config.db import TOKEN_HARD_LIMIT
        tl = store.entity_config.token_length
        if tl > TOKEN_HARD_LIMIT:
            response["warning"] = (
                f"Token length {tl} exceeds the recommended maximum of "
                f"{TOKEN_HARD_LIMIT} characters. Performance may degrade."
            )
    return response

def api_discover_store_fields(database: mkdb, data: dict):
    """Scan all stored records and add any unseen fields to schema_config."""
    store_name: str = data.get("name", "").strip()

    if not store_name:
        raise ValueError("Store name is required")
    if database is None:
        raise RuntimeError("Database not initialized")
    if store_name not in database.stores:
        raise ValueError(f"Store '{store_name}' does not exist or is not running")

    store_obj = database.stores[store_name]
    qe = getattr(store_obj, "query_engine", None)
    if qe is None:
        raise RuntimeError(f"Store '{store_name}' has no query engine")

    new_count = qe.discover_fields()
    return {
        "store_name": store_name,
        "new_fields": new_count,
        "message": (
            f"{new_count} new field(s) discovered and added to schema."
            if new_count else "No new fields found."
        ),
    }

def api_rebuild_store_indexes(database: mkdb, data: dict):
    """Rebuild all queryable-field indexes for a store from stored record data.

    Optionally rebuild only a single field when *field* is supplied.
    Runs synchronously — may take a while for large stores.
    """
    store_name: str = data.get("name", "").strip()
    field: str = data.get("field", "").strip()  # optional — omit to rebuild all

    if not store_name:
        raise ValueError("Store name is required")
    if database is None:
        raise RuntimeError("Database not initialized")
    if store_name not in database.stores:
        raise ValueError(f"Store '{store_name}' does not exist or is not running")

    store_obj = database.stores[store_name]
    qe = getattr(store_obj, "query_engine", None)
    if qe is None:
        raise RuntimeError(f"Store '{store_name}' has no query engine")

    schema = getattr(store_obj.config, "schema_config", None)
    if schema is None:
        raise RuntimeError(f"Store '{store_name}' has no schema_config")

    if field:
        # Single-field rebuild
        qe.rebuild_index(field)
        rebuilt = [field]
    else:
        # Rebuild every queryable field
        rebuilt = []
        errors = []
        for field_name, fs in schema.fields.items():
            q = getattr(fs, "queryable", False)
            if not q or q is False:
                continue
            try:
                qe.rebuild_index(field_name)
                rebuilt.append(field_name)
            except Exception as exc:
                errors.append(f"{field_name}: {exc}")
        if errors:
            raise RuntimeError("Some indexes failed to rebuild: " + "; ".join(errors))

    return {
        "store_name": store_name,
        "rebuilt": rebuilt,
        "count": len(rebuilt),
        "message": (
            f"{len(rebuilt)} index(es) rebuilt: {', '.join(rebuilt)}."
            if rebuilt else "No queryable fields to rebuild."
        ),
    }


def api_export_store_json(database: mkdb, data: dict):
    """Export a store's complete configuration as JSON."""
    store_name: str = data.get("name", "").strip()
    
    if not store_name:
        raise ValueError("Store name is required")
    
    if database is None:
        raise RuntimeError("Database not initialized")
    
    if store_name not in database.config.stores:
        raise ValueError(f"Store '{store_name}' does not exist")
    
    return database.config.stores[store_name].json

# ── Database info ─────────────────────────────────────────────────────────────

def api_get_db_info(database: mkdb, data: dict):
    """Return basic database metadata for the dashboard."""
    if database is None:
        raise RuntimeError("Database not initialized")
    from mkdb.config.db import TOKEN_HARD_LIMIT, TOKEN_SOFT_LIMIT
    db_health = "ok"
    for sc in database.config.stores.values():
        h = sc.entity_config.health["status"]
        if h == "degraded":
            db_health = "degraded"
            break
        if h == "low" and db_health == "ok":
            db_health = "low"
    return {
        "name":        database.config.name,
        "base_path":   database.config.base_path,
        "store_count": len(database.config.stores),
        "health":      db_health,
    }


def api_get_dashboard(database: mkdb, data: dict):
    """Aggregate all dashboard data in one call: db info, server status, store metrics, event log."""
    if database is None:
        raise RuntimeError("Database not initialized")

    import time as _time
    from mkdb.server.coms.metrics import get_store_metrics
    from mkdb.server.event_log import get_events

    # ── DB info ───────────────────────────────────────────────────────────────
    db_health = "ok"
    for sc in database.config.stores.values():
        h = sc.entity_config.health["status"]
        if h == "degraded":
            db_health = "degraded"
            break
        if h == "low" and db_health == "ok":
            db_health = "low"

    # ── Server status ─────────────────────────────────────────────────────────
    srv_raw = database.get_server_status()

    # ── Per-store metrics + totals ────────────────────────────────────────────
    stores_data = []
    total_reads = total_writes = total_queries = total_errors = total_entries = 0
    total_bytes_in = total_bytes_out = 0

    for name, store_obj in database.stores.items():
        m  = get_store_metrics(name)
        rc = getattr(store_obj, "_ram_cache", None)
        entries = rc.size() if rc else 0
        est_bytes = rc.estimated_bytes() if rc else 0
        max_size  = rc.max_size if rc else 0

        r  = m.get("reads",   0)
        w  = m.get("writes",  0)
        q  = m.get("queries", 0)
        e  = m.get("errors",  0)
        bi = m.get("bytes_in",  0)
        bo = m.get("bytes_out", 0)

        total_reads    += r
        total_writes   += w
        total_queries  += q
        total_errors   += e
        total_bytes_in  += bi
        total_bytes_out += bo
        total_entries  += entries

        stores_data.append({
            "name":     name,
            "reads":    r,
            "writes":   w,
            "deletes":  m.get("deletes", 0),
            "queries":  m.get("queries", 0),
            "errors":   e,
            "bytes_in":  bi,
            "bytes_out": bo,
            "ram": {
                "entries":   entries,
                "est_bytes": est_bytes,
                "max_size":  max_size,
                "pct": round(entries / max_size * 100, 1) if max_size else 0,
            },
        })

    return {
        "db": {
            "name":        database.config.name,
            "base_path":   database.config.base_path,
            "store_count": len(database.config.stores),
            "health":      db_health,
        },
        "servers": {
            "http":    {"running": bool(srv_raw.get("http"))},
            "socket":  {"running": bool(srv_raw.get("socket"))},
            "control": {"running": bool(srv_raw.get("control"))},
        },
        "totals": {
            "reads":     total_reads,
            "writes":    total_writes,
            "queries":   total_queries,
            "errors":    total_errors,
            "bytes_in":  total_bytes_in,
            "bytes_out": total_bytes_out,
            "ram_entries": total_entries,
        },
        "stores":     stores_data,
        "events":     get_events(limit=40),
        "started_at": database._started_at,
        "ts":         round(_time.time(), 3),
    }


def api_get_event_log(database: mkdb, data: dict):
    """Return recent system events. Optional filter: level=info|warning|error|recovery."""
    from mkdb.server.event_log import get_events
    limit = max(1, min(200, int(data.get("limit", 50))))
    level = str(data.get("level", "")).strip()
    return {"events": get_events(limit=limit, level=level)}


# ── Auth management ───────────────────────────────────────────────────────────

def api_get_auth_status(database: mkdb, data: dict):
    """Return current control-server auth configuration."""
    if database is None:
        raise RuntimeError("Database not initialized")
    from mkdb.server.control.server import active_session_count
    auth = database.config.servers.control_server.auth
    return {
        "enabled":        auth.enabled,
        "has_password":   bool(auth.password_hash),
        "session_ttl":    auth.session_ttl,
        "active_sessions": active_session_count(),
    }

def api_set_auth_password(database: mkdb, data: dict):
    """Set or change the control-server password and enable auth."""
    if database is None:
        raise RuntimeError("Database not initialized")
    from mkdb.server.control.server import hash_password
    password = data.get("password", "").strip()
    if not password:
        raise ValueError("Password cannot be empty")
    auth = database.config.servers.control_server.auth
    auth.password_hash = hash_password(password)
    auth.enabled = True
    database.config.save()
    return {"message": "Password updated and authentication enabled"}

def api_enable_auth(database: mkdb, data: dict):
    """Enable authentication on the control server."""
    if database is None:
        raise RuntimeError("Database not initialized")
    auth = database.config.servers.control_server.auth
    if not auth.password_hash:
        raise ValueError("Cannot enable auth: set a password first")
    auth.enabled = True
    database.config.save()
    return {"message": "Authentication enabled"}

def api_disable_auth(database: mkdb, data: dict):
    """Disable authentication on the control server."""
    if database is None:
        raise RuntimeError("Database not initialized")
    auth = database.config.servers.control_server.auth
    auth.enabled = False
    database.config.save()
    return {"message": "Authentication disabled"}

def api_set_auth_session_ttl(database: mkdb, data: dict):
    """Set session TTL in seconds (minimum 60)."""
    if database is None:
        raise RuntimeError("Database not initialized")
    ttl = data.get("ttl", 3600)
    if not isinstance(ttl, int) or ttl < 60:
        raise ValueError("TTL must be an integer ≥ 60 seconds")
    auth = database.config.servers.control_server.auth
    auth.session_ttl = ttl
    database.config.save()
    return {"message": f"Session TTL set to {ttl} seconds"}

def api_invalidate_all_sessions(database: mkdb, data: dict):
    """Force-expire every active session token."""
    from mkdb.server.control.server import invalidate_all_sessions
    count = invalidate_all_sessions()
    return {"message": f"Invalidated {count} session(s)"}

def api_query_store(database: mkdb, data: dict):
    """
    Execute a query against a store's query engine.

    Request data keys:
      store   (str, required) — store name
      filter  (dict, required) — query filter dict
      hydrate (bool, optional, default False) — return full records instead of just IDs
    """
    store_name  = str(data.get("store", "")).strip()
    filter_dict = data.get("filter", {})
    hydrate     = bool(data.get("hydrate", False))

    if not store_name:
        return {"success": False, "error": "'store' is required"}
    if not isinstance(filter_dict, dict):
        return {"success": False, "error": "'filter' must be a JSON object"}

    if store_name not in database.stores:
        return {"success": False, "error": f"Store '{store_name}' not found"}

    store_obj = database.stores[store_name]
    qe = getattr(store_obj, "query_engine", None)
    if qe is None:
        return {"success": False, "error": f"Store '{store_name}' has no query engine"}

    try:
        ids = qe.query(filter_dict)
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    if hydrate:
        records = [store_obj.read(rid) for rid in ids]
        return {"success": True, "count": len(records), "records": records}

    return {"success": True, "count": len(ids), "ids": ids}


# ── Database settings ─────────────────────────────────────────────────────────

def api_get_db_settings(database: mkdb, data: dict):
    """Return all editable database and server settings."""
    if database is None:
        raise RuntimeError("Database not initialized")
    cfg = database.config
    return {
        "name":          cfg.name,
        "base_path":     cfg.base_path,
        "storage_nodes": cfg.storage_nodes,
        "store_count":   len(cfg.stores),
        "servers": {
            "control": {
                "enabled": cfg.servers.control_server.enabled,
                "host":    cfg.servers.control_server.address.host,
                "port":    cfg.servers.control_server.address.port,
            },
            "socket": {
                "enabled":            cfg.servers.socket_server.enabled,
                "host":               cfg.servers.socket_server.address.host,
                "port":               cfg.servers.socket_server.address.port,
                "heartbeat_interval": cfg.servers.socket_server.heartbeat_interval,
                "max_clients":        cfg.servers.socket_server.max_clients,
                "recv_timeout":       cfg.servers.socket_server.recv_timeout,
            },
            "http": {
                "enabled":                 cfg.servers.http_server.enabled,
                "host":                    cfg.servers.http_server.address.host,
                "port":                    cfg.servers.http_server.address.port,
                "max_body_size":           cfg.servers.http_server.max_body_size,
                "cors_enabled":            cfg.servers.http_server.cors_enabled,
                "cors_origins":            cfg.servers.http_server.cors_origins,
                "cors_methods":            cfg.servers.http_server.cors_methods,
                "cors_headers":            cfg.servers.http_server.cors_headers,
                "cors_credentials":        cfg.servers.http_server.cors_credentials,
                "cors_max_age":            cfg.servers.http_server.cors_max_age,
                "max_requests_per_second": cfg.servers.http_server.max_requests_per_second,
            },
        },
    }


def api_update_db_settings(database: mkdb, data: dict):
    """Update top-level database settings (name, storage_nodes) and save."""
    if database is None:
        raise RuntimeError("Database not initialized")
    changed = False
    if "name" in data:
        new_name = str(data["name"]).strip()
        if not new_name:
            raise ValueError("Database name cannot be empty")
        database.config.name = new_name
        changed = True
    if "storage_nodes" in data:
        nodes = data["storage_nodes"]
        if not isinstance(nodes, list):
            raise ValueError("storage_nodes must be a list")
        database.config.storage_nodes = [str(n) for n in nodes]
        changed = True
    if changed:
        database.config.save()
    return {"saved": changed, "message": "Settings saved." if changed else "No changes."}


# ── Server lifecycle ──────────────────────────────────────────────────────────

def api_get_server_status(database: mkdb, data: dict):
    """Return running/stopped status for all three servers."""
    if database is None:
        raise RuntimeError("Database not initialized")
    raw = database.get_server_status()
    return {
        "control": {"running": bool(raw.get("control"))},
        "socket":  {"running": bool(raw.get("socket"))},
        "http":    {"running": bool(raw.get("http"))},
    }


def api_update_server_config(database: mkdb, data: dict):
    """Persist host/port/enabled changes for a server to config.json.

    Required: server = "socket" | "http" | "control"
    Does NOT restart — call api_server_control afterwards if needed.
    """
    if database is None:
        raise RuntimeError("Database not initialized")
    server_name = str(data.get("server", "")).strip()
    if server_name not in ("socket", "http", "control"):
        raise ValueError("server must be one of: socket, http, control")

    cfg_map = {
        "socket":  database.config.servers.socket_server,
        "http":    database.config.servers.http_server,
        "control": database.config.servers.control_server,
    }
    srv_cfg = cfg_map[server_name]

    if "host" in data:
        srv_cfg.address.host = str(data["host"]).strip()
    if "port" in data:
        port = int(data["port"])
        if not (1 <= port <= 65535):
            raise ValueError("port must be between 1 and 65535")
        srv_cfg.address.port = port
    if "enabled" in data:
        srv_cfg.enabled = bool(data["enabled"])

    if server_name == "socket":
        if "heartbeat_interval" in data:
            srv_cfg.heartbeat_interval = float(data["heartbeat_interval"])
        if "max_clients" in data:
            srv_cfg.max_clients = int(data["max_clients"])
        if "recv_timeout" in data:
            srv_cfg.recv_timeout = float(data["recv_timeout"])
    elif server_name == "http":
        if "max_body_size" in data:
            srv_cfg.max_body_size = int(data["max_body_size"])
        if "cors_enabled" in data:
            srv_cfg.cors_enabled = bool(data["cors_enabled"])
        if "cors_origins" in data:
            val = data["cors_origins"]
            srv_cfg.cors_origins = val if isinstance(val, list) else [s.strip() for s in str(val).split(",") if s.strip()]
        if "cors_methods" in data:
            val = data["cors_methods"]
            srv_cfg.cors_methods = val if isinstance(val, list) else [s.strip() for s in str(val).split(",") if s.strip()]
        if "cors_headers" in data:
            val = data["cors_headers"]
            srv_cfg.cors_headers = val if isinstance(val, list) else [s.strip() for s in str(val).split(",") if s.strip()]
        if "cors_credentials" in data:
            srv_cfg.cors_credentials = bool(data["cors_credentials"])
        if "cors_max_age" in data:
            srv_cfg.cors_max_age = int(data["cors_max_age"])
        if "max_requests_per_second" in data:
            srv_cfg.max_requests_per_second = int(data["max_requests_per_second"])

    database.config.save()
    return {"message": f"{server_name} server config saved", "server": server_name}


def api_server_control(database: mkdb, data: dict):
    """Start, stop, or restart a named server.

    Required:
      server = "socket" | "http" | "control"
      action = "start"  | "stop"  | "restart"

    Control-server stop/restart is deferred 1.5 s so the response
    is delivered before the socket closes.
    """
    import threading
    if database is None:
        raise RuntimeError("Database not initialized")
    server_name = str(data.get("server", "")).strip()
    action      = str(data.get("op",  "")).strip()   # 'op' avoids collision with the top-level 'action' envelope key
    if server_name not in ("socket", "http", "control"):
        raise ValueError("server must be one of: socket, http, control")
    if action not in ("start", "stop", "restart"):
        raise ValueError("op must be start, stop, or restart")

    def _do():
        if action == "start":
            database.start_server(server_name)
        elif action == "stop":
            database.stop_server(server_name)
        elif action == "restart":
            database.restart_server(server_name)

    if server_name == "control" and action in ("stop", "restart"):
        threading.Timer(1.5, _do).start()
        return {"message": f"Control server {action} scheduled in 1.5 s", "deferred": True}

    _do()
    return {"message": f"{server_name} server {action}ed", "deferred": False}


# ── User management ───────────────────────────────────────────────────────────

def api_list_users(database: mkdb, data: dict):
    """List all database users and their store permissions."""
    if database is None:
        raise RuntimeError("Database not initialized")
    return [
        {
            "username":     uname,
            "has_password": bool(user.password_hash),
            "stores": {
                sname: {"read": perm.read, "write": perm.write}
                for sname, perm in user.stores.items()
            },
        }
        for uname, user in database.config.users.items()
    ]


def api_create_user(database: mkdb, data: dict):
    """Create a new data-plane user."""
    if database is None:
        raise RuntimeError("Database not initialized")
    from mkdb.server.control.server import hash_password
    from mkdb.config.db import db_user

    username = str(data.get("username", "")).strip()
    password = str(data.get("password", "")).strip()
    if not username:
        raise ValueError("Username cannot be empty")
    if not password:
        raise ValueError("Password cannot be empty")
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters")
    if username in database.config.users:
        raise ValueError(f"User '{username}' already exists")

    u = db_user({})
    u.username      = username
    u.password_hash = hash_password(password)
    database.config.users[username] = u
    database.config.save()
    return {"message": f"User '{username}' created"}


def api_delete_user(database: mkdb, data: dict):
    """Delete a data-plane user."""
    if database is None:
        raise RuntimeError("Database not initialized")
    username = str(data.get("username", "")).strip()
    if not username:
        raise ValueError("Username cannot be empty")
    if username not in database.config.users:
        raise ValueError(f"User '{username}' does not exist")
    del database.config.users[username]
    database.config.save()
    return {"message": f"User '{username}' deleted"}


def api_set_user_password(database: mkdb, data: dict):
    """Set or change a user's data-plane password."""
    if database is None:
        raise RuntimeError("Database not initialized")
    from mkdb.server.control.server import hash_password
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", "")).strip()
    if not username:
        raise ValueError("Username cannot be empty")
    if not password:
        raise ValueError("Password cannot be empty")
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters")
    user = database.config.users.get(username)
    if user is None:
        raise ValueError(f"User '{username}' does not exist")
    user.password_hash = hash_password(password)
    database.config.save()
    return {"message": f"Password updated for '{username}'"}


def api_set_user_store_access(database: mkdb, data: dict):
    """Grant or update a user's access to a store.
    Required: username, store
    Optional: read (bool, default True), write (bool, default False)
    """
    if database is None:
        raise RuntimeError("Database not initialized")
    from mkdb.config.db import store_permission
    username   = str(data.get("username", "")).strip()
    store_name = str(data.get("store", "")).strip()
    if not username:
        raise ValueError("Username cannot be empty")
    if not store_name:
        raise ValueError("Store name cannot be empty")
    user = database.config.users.get(username)
    if user is None:
        raise ValueError(f"User '{username}' does not exist")
    if store_name not in database.config.stores:
        raise ValueError(f"Store '{store_name}' does not exist")
    perm = user.stores.get(store_name) or store_permission({})
    if "read" in data:
        perm.read = bool(data["read"])
    if "write" in data:
        perm.write = bool(data["write"])
    user.stores[store_name] = perm
    database.config.save()
    return {"message": f"Access updated for '{username}' on '{store_name}'"}


def api_remove_user_store_access(database: mkdb, data: dict):
    """Remove a user's access to a specific store."""
    if database is None:
        raise RuntimeError("Database not initialized")
    username   = str(data.get("username", "")).strip()
    store_name = str(data.get("store", "")).strip()
    if not username or not store_name:
        raise ValueError("Username and store name are required")
    user = database.config.users.get(username)
    if user is None:
        raise ValueError(f"User '{username}' does not exist")
    user.stores.pop(store_name, None)
    database.config.save()
    return {"message": f"Store access '{store_name}' removed from '{username}'"}


def api_get_rate_limit_log(database: mkdb, data: dict):
    """Return recent rate-limit events from logs/rate_limit.jsonl.
    Optional: limit (int, default 200)
    """
    import os, json as _json
    if database is None:
        raise RuntimeError("Database not initialized")
    limit    = max(1, int(data.get("limit", 200)))
    log_path = os.path.join(database.config.base_path, "logs", "rate_limit.jsonl")
    if not os.path.exists(log_path):
        return {"events": [], "total": 0}
    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    events = []
    for line in lines[-limit:]:
        try:
            events.append(_json.loads(line.strip()))
        except Exception:
            pass
    return {"events": list(reversed(events)), "total": len(lines)}


# ── Data security settings ────────────────────────────────────────────────────

def api_get_data_security(database: mkdb, data: dict):
    """Return the data-plane security settings."""
    if database is None:
        raise RuntimeError("Database not initialized")
    ds = database.config.data_security
    return {
        "protect_reads":            ds.protect_reads,
        "disable_default_password": ds.disable_default_password,
    }


def api_set_data_security(database: mkdb, data: dict):
    """Update data-plane security settings.
    Optional: protect_reads (bool), disable_default_password (bool)
    """
    if database is None:
        raise RuntimeError("Database not initialized")
    ds = database.config.data_security
    if "protect_reads" in data:
        ds.protect_reads = bool(data["protect_reads"])
    if "disable_default_password" in data:
        ds.disable_default_password = bool(data["disable_default_password"])
    database.config.save()
    return {
        "protect_reads":            ds.protect_reads,
        "disable_default_password": ds.disable_default_password,
    }


# ── Control-plane user management (RBAC) ─────────────────────────────────────

def api_list_control_users(database: mkdb, data: dict):
    """List all control/SDK users and their roles."""
    if database is None:
        raise RuntimeError("Database not initialized")
    from mkdb.config.db import CONTROL_ROLES
    return [
        {"username": uname, "role": cu.role, "has_password": bool(cu.password_hash)}
        for uname, cu in database.config.control_users.items()
    ]


def api_create_control_user(database: mkdb, data: dict):
    """Create a control/SDK user.
    Required: username, password, role ('viewer' | 'operator' | 'admin')
    """
    if database is None:
        raise RuntimeError("Database not initialized")
    from mkdb.server.control.server import hash_password
    from mkdb.config.db import control_user, CONTROL_ROLES
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", "")).strip()
    role     = str(data.get("role", "viewer")).strip()
    if not username:
        raise ValueError("Username cannot be empty")
    if not password or len(password) < 6:
        raise ValueError("Password must be at least 6 characters")
    if role not in CONTROL_ROLES:
        raise ValueError(f"Role must be one of: {', '.join(CONTROL_ROLES)}")
    if username in database.config.control_users:
        raise ValueError(f"Control user '{username}' already exists")
    cu = control_user({})
    cu.username      = username
    cu.password_hash = hash_password(password)
    cu.role          = role
    database.config.control_users[username] = cu
    database.config.save()
    return {"message": f"Control user '{username}' created with role '{role}'"}


def api_delete_control_user(database: mkdb, data: dict):
    """Delete a control/SDK user."""
    if database is None:
        raise RuntimeError("Database not initialized")
    username = str(data.get("username", "")).strip()
    if not username:
        raise ValueError("Username cannot be empty")
    if username not in database.config.control_users:
        raise ValueError(f"Control user '{username}' does not exist")
    del database.config.control_users[username]
    database.config.save()
    return {"message": f"Control user '{username}' deleted"}


def api_set_control_user_password(database: mkdb, data: dict):
    """Set or change a control user's password."""
    if database is None:
        raise RuntimeError("Database not initialized")
    from mkdb.server.control.server import hash_password
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", "")).strip()
    if not username:
        raise ValueError("Username cannot be empty")
    if not password or len(password) < 6:
        raise ValueError("Password must be at least 6 characters")
    cu = database.config.control_users.get(username)
    if cu is None:
        raise ValueError(f"Control user '{username}' does not exist")
    cu.password_hash = hash_password(password)
    database.config.save()
    return {"message": f"Password updated for control user '{username}'"}


def api_set_control_user_role(database: mkdb, data: dict):
    """Change a control user's role."""
    if database is None:
        raise RuntimeError("Database not initialized")
    from mkdb.config.db import CONTROL_ROLES
    username = str(data.get("username", "")).strip()
    role     = str(data.get("role", "")).strip()
    if not username:
        raise ValueError("Username cannot be empty")
    if role not in CONTROL_ROLES:
        raise ValueError(f"Role must be one of: {', '.join(CONTROL_ROLES)}")
    cu = database.config.control_users.get(username)
    if cu is None:
        raise ValueError(f"Control user '{username}' does not exist")
    cu.role = role
    database.config.save()
    return {"message": f"Role for '{username}' updated to '{role}'"}


def api_get_store_metrics(database: mkdb, data: dict):
    """
    Return live request, bandwidth, RAM-cache, and per-client metrics for a store.

    Request:  {"name": "store_name"}
    Response: {
        "reads": int, "writes": int, "deletes": int, "queries": int,
        "errors": int, "rate_limited": int,
        "bytes_in": int, "bytes_out": int,
        "started_at": float,
        "transport": {"http": {...}, "socket": {...}},
        "clients": [ {"key": str, "reads": int, ...}, ... ],
        "rate_limited_ips": [ {"ip": str, "count": int}, ... ],
        "ram_cache": {"entries": int, "estimated_bytes": int, "max_size": int},
    }
    """
    if database is None:
        raise RuntimeError("Database not initialized")
    import time
    from mkdb.server.coms.metrics import get_store_metrics

    name = str(data.get("name", "")).strip()
    if not name:
        raise ValueError("'name' is required")

    m = get_store_metrics(name)

    # Flatten clients dict into a sorted list (most active first)
    clients_list = sorted(
        [{"key": k, **v} for k, v in m.get("clients", {}).items()],
        key=lambda c: c.get("reads", 0) + c.get("writes", 0) + c.get("queries", 0),
        reverse=True,
    )

    # Flatten rate_limited_ips into a sorted list
    rl_ips = sorted(
        [{"ip": ip, "count": cnt} for ip, cnt in m.get("rate_limited_ips", {}).items()],
        key=lambda x: x["count"],
        reverse=True,
    )

    # RAM cache stats from the live store object
    ram_info = {"entries": 0, "estimated_bytes": 0, "max_size": 0}
    store_obj = database.stores.get(name)
    if store_obj is not None:
        rc = getattr(store_obj, "_ram_cache", None)
        if rc is not None:
            ram_info["entries"]         = rc.size()
            ram_info["estimated_bytes"] = rc.estimated_bytes()
            ram_info["max_size"]        = rc.max_size
        ram_info["ttl"]        = getattr(getattr(store_obj, "_ram_cache", None), "ttl", 0)
        ram_info["clean_type"] = getattr(getattr(store_obj, "_ram_cache", None), "clean_type", "") #type: ignore

    # Slow query log — newest first, convert deque to plain list for JSON
    slow_log_raw = m.get("slow_query_log", [])
    slow_query_log = list(reversed(list(slow_log_raw)))

    # Error log — newest first
    error_log_raw = m.get("error_log", [])
    error_log = list(reversed(list(error_log_raw)))

    # Threshold from live config (so the UI shows the current setting)
    slow_query_threshold_ms = 0.0
    if store_obj is not None:
        slow_query_threshold_ms = getattr(store_obj.config, "slow_query_threshold_ms", 0.0)

    return {
        "reads":          m.get("reads", 0),
        "writes":         m.get("writes", 0),
        "deletes":        m.get("deletes", 0),
        "queries":        m.get("queries", 0),
        "errors":         m.get("errors", 0),
        "slow_queries":   m.get("slow_queries", 0),
        "rate_limited":   m.get("rate_limited", 0),
        "bytes_in":       m.get("bytes_in", 0),
        "bytes_out":      m.get("bytes_out", 0),
        "started_at":     m.get("started_at", time.time()),
        "transport":      m.get("transport", {}),
        "clients":        clients_list,
        "rate_limited_ips": rl_ips,
        "ram_cache":      ram_info,
        "slow_query_log":          slow_query_log,
        "slow_query_threshold_ms": slow_query_threshold_ms,
        "error_log":               error_log,
    }


def api_reset_store_metrics(database: mkdb, data: dict):
    """Clear all in-memory metrics for a store."""
    if database is None:
        raise RuntimeError("Database not initialized")
    from mkdb.server.coms.metrics import reset_store_metrics
    name = str(data.get("name", "")).strip()
    if not name:
        raise ValueError("'name' is required")
    reset_store_metrics(name)
    return {"message": f"Metrics reset for store '{name}'"}


def api_get_all_store_metrics(database: mkdb, data: dict):
    """
    Return request counts, bandwidth, and live RAM-cache stats for every store.

    Response: list of {
        name, reads, writes, deletes, queries, errors, rate_limited,
        bytes_in, bytes_out,
        ram_cache: {entries, max_size, estimated_bytes, ttl, clean_type}
    }
    """
    if database is None:
        raise RuntimeError("Database not initialized")
    from mkdb.server.coms.metrics import get_store_metrics

    result = []
    for name, store_obj in database.stores.items():
        m  = get_store_metrics(name)
        rc = getattr(store_obj, "_ram_cache", None)
        ram_info = {
            "entries":         rc.size()            if rc else 0,
            "estimated_bytes": rc.estimated_bytes() if rc else 0,
            "max_size":        rc.max_size          if rc else 0,
            "ttl":             rc.ttl               if rc else 0,
            "clean_type":      rc.clean_type        if rc else "",
        }
        result.append({
            "name":         name,
            "reads":        m.get("reads",        0),
            "writes":       m.get("writes",       0),
            "deletes":      m.get("deletes",      0),
            "queries":      m.get("queries",      0),
            "errors":       m.get("errors",       0),
            "rate_limited": m.get("rate_limited", 0),
            "bytes_in":     m.get("bytes_in",     0),
            "bytes_out":    m.get("bytes_out",    0),
            "ram_cache":    ram_info,
        })
    return result
