# Notification Admin API

These GET-only endpoints support System Mode and operator diagnostics. They do
not submit, retry, cancel, or configure notifications.

## `GET /api/admin/notifications`

Returns sanitized live Apprise health, notification and recipient-group
summaries, exact external receipt counts, and at most 25 recent receipts.
Under canonical composition, definition/group/provider state and health inputs
come exclusively from the immutable applied notification view; the response
also identifies the applied configuration revision.

## `GET /api/admin/notifications/deliveries`

Optional query parameters:

- `notification_type`: normalized Oracle notification id;
- `status`: `pending`, `accepted`, `retry_wait`, `failed`, `expired`, or
  `suppressed`;
- `limit`: clamped to 1 through 500, default 100;
- `offset`: clamped to zero or greater, default 0.

The response contains normalized filters, exact overall external receipt
counts, and sanitized matching rows.

Neither endpoint exposes message text, Apprise base URLs/configuration keys,
routing tags, downstream URLs/topics, credentials, attachments, or raw
provider responses.
