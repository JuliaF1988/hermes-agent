# UP #198 integration acceptance

Companion: JuliaF1988/UniversalPipelineV1#199. Hermes code revision:
`160ae4e3ccb735654d8620f640626c184fd92c9f`; base:
`c4cbd7a24f0b544f6e4ff90bccd1dcc116a4d8ec`.

## Isolated checks

- Native MCP/skill suite: 682 passed across 62 test files.
- Optional skill v1.0.0 loads; native skill lint: zero findings.
- Real Hermes MCP registry/SDK: 35 UP tools discovered; authenticated read,
  ping, close/reconnect and reread passed against isolated Core.
- Ambiguous write transport failures: no automatic replay. Read-only tools
  retain reconnect recovery. Missing/invalid annotations fail closed.
- Production-compatible handler integration: 43 focused tests passed. The
  handler AST matches this PR; unrelated existing runtime fixes are retained.
- Secret comparison against the changes: zero matches. The config example
  contains only runtime variable references, never credential values.

## Production connection

One standard `mcp_servers.universalpipeline` entry was added to the existing
profile, with all ten existing MCP entries/settings preserved. Credentials are
host-local only. A private internal Docker network joins Hermes and Core; no
public MCP port, reverse-proxy or Authentik change was made.

The optional skill is installed in the active profile. Hermes was idle before
its minimal image update. Its pre-existing runtime integration at
`10af635121c7ab8f96290b4a71bde6b17352f322` was not replaced with repository main:
only the tested generic unknown-write-outcome handler was added.

Hermes' normal MCP registry requested persistent UP drain generation 12 at
`2026-09-12T11:42:11.759920+00:00`. The running ASR lease/job is allowed to
complete naturally. No cancel/retry, model restart or new input was submitted.
The protected ASR result was accepted at 13:32:44 UTC; drain completed at
13:32:46 UTC with all active safety counts zero. Only UP Core was then updated.
The real Hermes client rediscovered all 35 tools and read real Document and
Transcript work views; reconnect passed after the Core restart.

Through the same registry/SDK, a queued Work's priority went 100 → 900 → 100,
with authoritative scheduler reread. While drained, one permitted adaptive
policy field changed by one second, then the complete original policy was
restored. Explicit MCP resume was confirmed normal at 13:37:23 UTC. A normal
subsequent ASR node started with adaptive allocation; accepted upstream was not
replayed. No real interaction response was submitted or altered.

## Agent ergonomics: evidence boundary

The configured private Qwen endpoint is unreachable. No model restart, download
or paid replacement was attempted. Natural-language A–I cases are **PENDING**;
direct registry/SDK sequences do not prove model tool selection or reasoning.
The completed deterministic protocol tests had zero SQL/shell/Docker fallback.
Administrative deployment commands are not agent control-path fallbacks.

| Request | Deterministic evidence | Natural Qwen run |
|---|---|---|
| A system/progress/ETA | real shared Core read model PASS; unknown ETA null | pending |
| B find current transcript, then one status | real discovery/status PASS | pending |
| C document waiting reason | bounded reason projection PASS; real completed Document found | pending |
| D set priority, authoritative reread | real 100 → 900 → 100 and scheduler view PASS | pending |
| E hosts/runtime capacity | real shared snapshots and adaptive allocation PASS | pending |
| F accepted/paid replay | unknown billing stays null; no fabricated PASS | pending |
| G request drain, wait until complete | real request → complete with zero active gates PASS | pending |
| H explicit resume | real normal state and subsequent dispatch PASS | pending |
| I adaptive policy/reason | real reversible policy roundtrip PASS | pending |

No issue close or merge. See the UP companion acceptance document for final
production counts, fingerprints, drain timeline and rollback boundaries.
