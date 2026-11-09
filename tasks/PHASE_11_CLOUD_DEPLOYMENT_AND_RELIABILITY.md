# Phase 11 — Cloud Deployment and Reliability

## Goal
Prove that the same Project Aegis application can be deployed and operated securely on a modest VPS or equivalent host before any unattended live-trading consideration.

Cloud deployment is not a prerequisite for local development or the initial 30–60 trading-day laptop paper-trading experiment. It is a prerequisite for considering unattended live operation.

## Tasks
- [ ] Review actual CPU, RAM, storage, network, and process metrics from local paper trading before sizing infrastructure.
- [ ] Keep the application portable across supported development and deployment environments; remove machine-specific paths and assumptions.
- [ ] Define a reproducible deployment process from a reviewed Git commit/release.
- [ ] Separate source code, runtime configuration, secrets, persistent data, and logs.
- [ ] Define secure remote dashboard access; do not expose administrative interfaces publicly without authentication and transport security.
- [ ] Configure process supervision, health checks, restart policy, and controlled shutdown.
- [ ] Define database backup, restore, retention, and recovery procedures; test restores.
- [ ] Add monitoring and alerts for process health, data-feed health, stale data, database health, disk space, and reconciliation status.
- [ ] Test network interruption, host restart, deployment rollback, provider outage, and database recovery.
- [ ] Verify that deployment and restart cannot change execution mode or enable live orders.
- [ ] Use GitHub for source control and CI/CD orchestration only; the VPS or host supplies runtime compute.
- [ ] Document deployment costs and obtain explicit approval before incurring any paid hosting or service charges.
- [ ] Define least-privilege operating-system and application permissions for the deployment.
- [ ] Protect backups and secrets against unauthorized access; document backup encryption and retention.
- [ ] Define recovery-point and recovery-time objectives based on the paper-trading workload.
- [ ] Verify deployment artifacts and dependencies against the reviewed release; document rollback triggers.
- [ ] Test that restored backups preserve ledger consistency, experiment identity, and idempotency state.

## Acceptance criteria
- A reviewed release can be deployed reproducibly without copying local secrets or development databases.
- Paper mode remains the only permitted execution mode.
- Restart and recovery preserve the paper ledger and do not create duplicate simulated orders.
- Backups can be restored and verified.
- Monitoring detects the defined critical failure conditions.
- Remote access is authenticated and secured.
- Resource sizing is supported by measured workload data.
- No live broker connection, live credentials, or real order placement is introduced.
- Recovery-point and recovery-time objectives are documented and demonstrated through restore tests.
- Secrets are not embedded in source code, deployment artifacts, logs, or backups in plaintext.
- Deployment rollback cannot silently downgrade database state or discard committed paper-ledger transactions.
- Host access, service permissions, and network exposure follow documented least-privilege controls.
