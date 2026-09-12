# Core MCP connection (operator deployment, not agent fallback)

Use the existing Hermes MCP loader. No custom client, service or database.
Merge only `mcp_servers.universalpipeline` from `mcp-config.example.yaml` into
the active profile's config.yaml; preserve every other server/settings entry.

Set `UP_MCP_URL` to the approved private Core `/mcp` endpoint and
`UP_MCP_TOKEN` to its existing Core bearer in the active profile's host-local
`.env` (0600). Never paste secrets into chat, skill, command arguments or Git.
Do not expose MCP through up.fachet.uk; its public boundary stays UI-only.
For Docker, use an approved private network to reach Core without publishing
another port. Back up host-local config and secret files privately first.

Install this optional skill in the active profile's `skills/mcp/` directory.
Use Hermes' existing `/reload-mcp` control, then begin a fresh session so its
stable toolset discovers the connection. No Core restart is needed for config.

Verify with the real Hermes MCP client: initialize, tools/list, get_agent_guide,
get_system_status, close/reconnect, repeat a read. No writes during discovery.
MCP `ping` is supported; GET /mcp may return 405 (no unsolicited SSE stream).
`skip_preflight` avoids mistaking that intentional response for a broken API.

Read-only role exercises: system/ETA, find transcript→status, document blocker,
hosts, replay uncertainty, adaptive reasons. Writes require explicit operator
intent: priority roundtrip, drain naturally complete, bounded policy roundtrip,
explicit resume. Never test these by cancelling compute.

Rollback: remove only this MCP entry and skill, restore the prior private
configuration/secret file, reload MCP. Do not touch other integrations or UP data.

Refs JuliaF1988/UniversalPipelineV1#198. The example contains references only.
