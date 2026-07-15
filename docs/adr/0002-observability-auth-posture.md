# Auth posture for the live observability surface

Status: accepted

Resolves issue #49. The decision shipped with the dashboard-production readiness arc
(2026-07-15) and is recorded here; see `docs/rse/specs/plan-dashboard-production-readiness.md`
decision 2 for the original rationale.

The unified science dashboard (`scripts/qa_server.py`, port 8767) keeps all read-only routes
open on the LAN and gates every mutating route (`POST /api/runs`,
`POST /api/runs/{id}/terminate`) behind a pre-shared bearer token (`DSA110_CONTROL_TOKEN`):

- **Fail-closed:** token unset in the server environment → every mutating request returns 403
  and the dashboard is effectively read-only. Constant-time comparison
  (`secrets.compare_digest`), non-ASCII header bytes rejected.
- **Audited:** one JSON line per mutating request (timestamp, action, remote address, payload;
  never the token) to `control/audit.jsonl`.
- **Token storage:** environment file on a real POSIX filesystem
  (`~/.config/dsa110/dashboard.env`, mode 600), loaded by the systemd unit — never under
  `/data` or `/stage` (fuseblk ignores chmod).
- **No shell execution anywhere:** the control API accepts only a structured request and
  builds argv itself. Shell-execution endpoints are banned from the science server and from
  the repo; the last one (`scripts/monitor_server.py::POST /exec`) was deleted under issue #62.
- **Exposure hardening (optional, documented):** the Cloudflare tunnel origin may add
  Cloudflare Access (SSO) before the URL is advertised beyond the group; LAN access needs no
  extra step (`docs/operations/dashboard.md`).

## Considered Options

- **Per-user identity / SSO on the server itself.** Rejected for now: single-operator
  instrument team; no identity store to integrate; Cloudflare Access provides the same
  boundary at the tunnel when needed. Revisit if the operator set grows.
- **mTLS between clients and the server.** Rejected: certificate lifecycle overhead for a
  LAN-first tool with one operator; token + fail-closed covers the threat (accidental or
  drive-by mutation), and the tunnel handles the WAN boundary.
- **Keeping a shared-secret shell hook (`POST /exec`) for ops tasks.** Rejected outright:
  arbitrary `shell=True` execution over HTTP is not salvageable by a secret; ops tasks go
  through SSH, and pipeline control goes through the structured, audited `/api/runs` surface.
