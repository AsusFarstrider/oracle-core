"""Oracle Memory durable operational storage helpers.

Memory owns transactions and records; domain owners decide behavior.
"""

from .store import DB_PATH

__all__ = ["DB_PATH"]
