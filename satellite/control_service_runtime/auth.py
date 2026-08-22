from __future__ import annotations


def is_authorized(api_key: str, header: str | None) -> bool:
    if not api_key:
        return False
    return header == f"Bearer {api_key}"
