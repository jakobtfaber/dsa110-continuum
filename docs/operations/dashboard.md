# Dashboard operations (qa_server + pipeline control)

The dashboard on port 8767 (`scripts/qa_server.py`) is the single operator surface: read-only
monitoring is open; every mutating route (`POST /api/runs`, `POST /api/runs/{id}/terminate`)
requires a bearer token and is audited. The Dagster tracer-bullet on :3212 remains a separate
read-only metadata view (see `outputs/observability-dashboard-2026-07-14/REPRODUCE.md`).

## Control token

Generate once, store in the environment file the systemd unit loads:

```bash
/opt/miniforge/envs/casa6/bin/python -c "import secrets; print(secrets.token_urlsafe(32))"
sudo mkdir -p /data/dsa110-proc/products/control
printf 'DSA110_CONTROL_TOKEN=%s\n' '<token>' | sudo tee /data/dsa110-proc/products/control/dashboard.env
sudo chown ubuntu:ubuntu /data/dsa110-proc/products/control/dashboard.env
sudo chmod 600 /data/dsa110-proc/products/control/dashboard.env
```

Fail-closed: if `DSA110_CONTROL_TOKEN` is unset in the server environment, every mutating
request returns 403 and the dashboard is effectively read-only. Never commit the env file.

## Service installation (one-time, requires sudo)

```bash
sudo cp ops/systemd/dsa110-dashboard.service ops/systemd/dsa110-autopipeline.service \
        ops/systemd/dsa110-autopipeline.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dsa110-dashboard
sudo systemctl enable --now dsa110-autopipeline.timer
```

Health checks: `systemctl status dsa110-dashboard`, `systemctl list-timers 'dsa110-*'`,
`curl -s http://127.0.0.1:8767/health`.

The timer runs `scripts/auto_pipeline.py` daily at 02:00 UTC, which launches
`batch_pipeline.py --date <yesterday UTC> --retry-failed --quarantine-after-failures 3
--photometry-workers 4` through the same registry the dashboard uses. If a launcher-owned run
is still active the timer tick reports `skipped_running` to the journal and does not queue.

## Manual intervention runbook

1. Open `http://lxd110h17:8767/` → Pipeline control panel.
2. Fill in date (and hours / flags as needed), paste the control token.
3. **Dry-run preview** — shows `batch_pipeline.py --dry-run`'s full rebuild/skip/quarantine
   plan; writes nothing.
4. **Launch** — the run appears in the runs table; click its ID for live log tail
   (`/control/runs/{run_id}`, 15 s refresh).
5. **Terminate** — SIGTERMs the whole process group (batch driver, CASA workers, WSClean),
   escalating to SIGKILL after 10 s. Verify with `pgrep -g <pid>` if paranoid.
6. Per-date QA and provenance: `/runs/{date}` (verdict, gate reasons, per-epoch QA).
7. Light curves: `/sources/lightcurve?ra=<deg>&dec=<deg>` or the dashboard lookup form.

Equivalent curl:

```bash
curl -s -X POST http://127.0.0.1:8767/api/runs \
  -H "Authorization: Bearer $DSA110_CONTROL_TOKEN" -H 'Content-Type: application/json' \
  -d '{"date":"2026-01-25","start_hour":22,"end_hour":23,"force_recal":true,"dry_run":true}'
```

## Audit and registry

- Run registry: `/data/dsa110-proc/products/control/runs.sqlite3` (`runs` table; each row
  carries the full request + argv JSON, pid, log path, status, exit code).
- Per-run logs: `/data/dsa110-proc/products/control/run_<id>.log`.
- Audit log: `/data/dsa110-proc/products/control/audit.jsonl` — one JSON line per mutating
  request (timestamp, action, remote address, payload). The token is never written.
- Env overrides: `DSA110_CONTROL_DIR`, `DSA110_REPO_ROOT`, `DSA110_PIPELINE_PYTHON`.

## Network posture

The Cloudflare tunnel (see `outputs/observability-dashboard-2026-07-14/CLOUDFLARE.md`) points
at this origin. Mutating routes are safe to expose only because of the token gate; for
defense in depth, enable Cloudflare Access (SSO) on the tunnel hostname before advertising
the URL beyond the group. LAN access needs no extra step. `scripts/monitor_server.py` and its
`POST /exec` remain unmounted and must stay that way (issue #62 tracks retirement).
