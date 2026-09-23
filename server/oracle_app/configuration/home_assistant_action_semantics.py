from __future__ import annotations


IMPLEMENTED_HOME_ASSISTANT_ACTION_OPERATIONS = frozenset(
    {"arm", "close", "cooler", "disarm", "invoke", "lock", "open", "turn_off", "turn_on", "unlock", "warmer"}
)

DIRECT_HOME_ASSISTANT_ACTION_OPERATIONS = frozenset(
    {"arm", "close", "disarm", "invoke", "lock", "open", "turn_off", "turn_on", "unlock"}
)

CLIMATE_HOME_ASSISTANT_ACTION_OPERATIONS = frozenset({"cooler", "warmer"})
