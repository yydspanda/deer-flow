# PingAn Shadow MCP Profiles

These profiles bind the same reviewed PI-01E routes to different evidence
classes. Neither file contains credentials.

- `extensions.simulated.json` starts the real PingAn MCP server code with fake
  transports. Every result must expose `mocked=true` and can prove only the
  external rehearsal gate.
- `extensions.internal.json` starts the same server code in internal
  mode. Secrets and endpoints are resolved from environment variables; every
  accepted result must expose `mocked=false`.

The batch runner requires one of these files explicitly and seals its SHA-256
into the manifest. The paired evaluator checks that the enabled MCP servers and
their declared Provider modes match the selected action bindings. It is not
valid to run a mock extensions profile with `internal_real`, or an internal
profile with `external_simulation`.

In live investigation mode, the runner also starts/connects these configured
servers before the first LLM call and requires each action config's exact
`(server, tool)` in MCP `list_tools()`. Therefore all environment variables used
by a server command must be present for preflight; a static JSON match alone is
not enough. `--plan-only` deliberately skips this runtime discovery.

Threat intelligence is intentionally absent until reviewed tenant network
ranges are available. Add the route, action config and MCP server together;
never add a route only to make coverage numbers look complete.
