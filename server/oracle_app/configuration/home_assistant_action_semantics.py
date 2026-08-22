from __future__ import annotations


IMPLEMENTED_HOME_ASSISTANT_ACTION_OPERATIONS = frozenset(
    {"cooler", "lock", "turn_off", "turn_on", "unlock", "warmer"}
)

DIRECT_HOME_ASSISTANT_ACTION_OPERATIONS = frozenset(
    {"lock", "turn_off", "turn_on", "unlock"}
)

CLIMATE_HOME_ASSISTANT_ACTION_OPERATIONS = frozenset({"cooler", "warmer"})
