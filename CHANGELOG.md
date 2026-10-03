# Changelog

## 0.1.1 (2026-10-03)

### Fixes

- **Demo:** the `logout-bug` example's planted root cause was not real. connect-redis 7 derives the Redis TTL from the cookie's expiry, so `ttl: 600` next to a 24h cookie `maxAge` never logged anyone out. The demo cookie now sets no `maxAge`, so the 600-second store ttl actually applies. `rolling: false` is no longer counted as a cause. A live run with real agents caught this.
- **`crosscheck doctor`:** fails when an agent CLI is installed but crashes (for example, a Codex install missing its native binary) instead of reporting it as ready.
- **Report:** no longer says an agent's coverage "never mentions" a folder when the agent described its coverage in prose rather than paths.
- **Offline demos:** runs with mock agents no longer make a real, billed clustering call under `--cluster auto`.

### Docs

- Install commands point at `mohsinm-dev/crosscheck`.
- Records the versions verified end to end: Claude Code 2.1.283 and Codex CLI 0.160.0.

## 0.1.0 (2026-10-03)

Initial release: blind parallel research by Claude Code and Codex, mechanical quote checks, anonymized cross-examination, rule-based resolution, an eval harness against a 2x-budget single-agent baseline, and the `/crosscheck:run`, `/crosscheck:setup` and `/crosscheck:eval` plugin commands.
