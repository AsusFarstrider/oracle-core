# Notification Configuration

Canonical notification policy is owned by `domains/notifications.yaml`.
Household mode identity belongs to `household.yaml`, and provider event
mappings belong to `domains/home-assistant.yaml`. The running Brain consumes
only the immutable applied views from one configuration activation.

Notification definitions declare stable IDs, enabled state, Brain-owned
message text, enabled canonical targets, suppression modes, delivery expiry,
audio policy, and the bounded external-delivery policy admitted by the schema.
Targets resolve to enabled household source IDs; they are not installation or
device inventory IDs.

External delivery uses logical recipient groups and a selected provider bridge.
Concrete destinations and credentials stay inside that provider's separately
managed system and the selected Oracle secret generation. Oracle configuration
does not accept destination URLs, arbitrary provider commands, or raw secret
values.

A missing or disabled notification role is intentionally unavailable and does
not create an implicit provider or fallback registry. See
[../contracts/notifications.md](../contracts/notifications.md) for delivery,
suppression, retry, and audit behavior.
