from __future__ import annotations


RUNBOOK_POWER_EXPANSION_ACKNOWLEDGEMENT = "runbook_power_expansion"

KNOWN_SAFETY_ACKNOWLEDGEMENTS = frozenset(
    {
        "access_expansion",
        "credential_role_change",
        "identity_removal",
        "mutating_control_enablement",
        "public_health_enablement",
        RUNBOOK_POWER_EXPANSION_ACKNOWLEDGEMENT,
    }
)
