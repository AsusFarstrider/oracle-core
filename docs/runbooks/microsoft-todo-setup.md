# Microsoft To Do Lists Provider Setup

This is an operator procedure for the optional `microsoft_todo` Lists provider.
It does not activate a provider by itself. Oracle never needs a Microsoft client
secret or an app-only Graph permission. The selected provider uses delegated
`Tasks.ReadWrite` plus `offline_access`; access tokens exist only in Brain
memory. The refresh credential is one canonical logical secret, not an MSAL
cache, environment variable, or file beside Oracle Memory.

1. Register a Microsoft identity **public client** application in a tenant that
   supports the intended account. Enable device-code/public-client flow and
   delegated Microsoft Graph `Tasks.ReadWrite`. For a personal Microsoft
   account, use `tenant: consumers`; otherwise use the selected tenant UUID or
   `organizations` if the deployment deliberately supports it. Record the
   non-secret application UUID. Do not request application permissions or
   unrelated Mail, Files, Notes, or sharing scopes.
2. Inspect the protected host-local configuration status and selected secret
   generation. Review the exact candidate `domains/lists.yaml` with
   `provider: <microsoft_todo definition ID>`, matching `tenant`, `client_id`,
   `refresh_token_secret`, policy, and any configured opaque Graph list IDs.
   Keep the role disabled until consent and the live canary are ready. Provider
   selection never follows credential presence.
3. From the trusted Oracle host, run the bundled consent helper with the
   protected configuration socket, the exact expected secret generation, and
   the intended logical secret ID. It displays only Microsoft's device code and
   verification URL, polls for delegated consent, and sends the refresh token
   directly to `scripts/oracle-config.py secret ... --value-stdin`. It prints no
   access or refresh token and writes no token file:

   ```text
   python3 scripts/microsoft-todo-consent.py \
     --tenant consumers --client-id <registered-application-uuid> \
     --logical-id MICROSOFT_TODO_REFRESH_TOKEN \
     --expected-secret-generation <selected-secret-generation-id> \
     --socket <protected-host-local-configuration-socket>
   ```

   For reauthorization, explicitly choose `--secret-operation rotate_secret`
   and pass the then-current expected secret generation. A canonical secret
   mutation is a managed activation transaction; follow its required
   verification/recovery procedure. Never hand-edit the companion or treat the
   helper as a background auto-rotator. In a standard installation, the
   protected `/run/oracle/control.sock` secret operation uses the existing
   complete-activation coordinator and separately protected secret companion.
   This is supported even though household YAML authoring is external read-only;
   it does not edit the immutable household artifact. The coordinator requests
   the Brain restart and finalizes credential retirement only after verified
   startup. Check canonical status for the new complete activation and secret
   generation before provider activation. A non-standard bootstrap without a
   configured secret-mutation authority must use its supported authoring
   procedure rather than a second credential store.
4. Review and activate the candidate only under the normal configuration and
   deployment gates. Check `/api/admin/health/lists` for the selected provider,
   then create one unmistakably temporary list and exercise list/task CRUD,
   complete/reopen-all and confirmed one-list delete-all. Read back provider
   state after every consequential step. Oracle deliberately has no public list
   deletion; remove the empty temporary list through the supported Microsoft
   To Do operator UI and verify its absence. Clean up on failure too.

Do not put a recurring task in the live CRUD canary list. Oracle may show a
recurring task, but it refuses individual mutation and refuses the entire
one-list bulk operation before any write if a recurring task applicable to that
operation is present. Recurrence creation or occurrence/series changes require
the provider UI and are not Stage 8 Oracle operations.

If refresh fails, Oracle reports `lists_reauthorization_required`. A missing
scope reports `lists_scope_denied`; throttling reports `lists_rate_limited`.
Neither causes an alternate provider selection or a hidden token write. A
replacement refresh token returned during access-token renewal is discarded;
Microsoft documents that ordinary refresh does not revoke the prior token.
If live behavior instead requires durable replacement for safe operation,
stop and seek a credential-authority decision before enabling the provider.
An isolated canary can keep its delegated token only in process memory and
leave Oracle configuration and secrets unchanged, but Microsoft account app
consent persists after its temporary list is deleted. The operator may revoke
that consent separately in Microsoft account settings if the app will not be
used. The 2026-09-22 isolated canary passed; it did not activate the provider.

Microsoft references: [device-code flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code),
[refresh-token lifecycle](https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens),
[To Do task-list API](https://learn.microsoft.com/en-us/graph/api/resources/todotasklist?view=graph-rest-1.0).
