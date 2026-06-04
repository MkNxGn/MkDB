"""
Delta helpers — flatten nested dicts into dot-notation keys for MkDB writes.

Example:
    flatten({"product": {"name": "Widget", "price": 9.99}})
    # → {"product.name": "Widget", "product.price": 9.99}
"""


def flatten(d: dict, prefix: str = "", sep: str = ".") -> dict:
    """Recursively flatten a nested dict into dot-notation keys."""
    result = {}
    for key, value in d.items():
        full_key = f"{prefix}{sep}{key}" if prefix else key
        if isinstance(value, dict):
            result.update(flatten(value, full_key, sep))
        else:
            result[full_key] = value
    return result
