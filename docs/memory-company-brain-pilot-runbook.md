# Memory and Company Brain pilot runbook

This runbook stages one company on one Omnigent deployment, one qm memory service, and one isolated
Company Brain. Do not use this topology for a shared multi-company deployment.

## Safety boundary

Hindsight personal-memory writes remain disabled. Keep
`OMNIGENT_HINDSIGHT_WRITES_ENABLED` and `OMNIGENT_HINDSIGHT_CAPABILITY_REPORT` unset, do not add a
`hindsight` provider to an agent memory policy, and do not expose `hindsight_retain`. Reconsider this
only after the endpoint-bound capability gate proves individual and bank deletion, retention/TTL,
tenant partitioning, live export, backup deletion, and idempotent capture.

Use synthetic or explicitly consented pilot data. Take database and secret-manager backups before
provisioning, and pin the qm and Omnigent release artifacts used for the pilot.

## 1. Provision qm memory

Generate two independent secrets directly into the deployment secret manager:

- `MEMORY_TOMBSTONE_SECRET`: stable tombstone and privacy-token key, at least 32 random bytes;
- `MEMORY_SERVICE_SIGNING_SECRET`: request-signing key shared only with Omnigent, at least 32 random
  bytes.

`MEMORY_TOMBSTONE_SECRET` must differ from the database credential, request-signing key, and every
core secret. Do not rotate it in place: the database key guard deliberately fails startup when the
key changes, because old scope and operation tombstones would otherwise become unreachable.

Set `MEMORY_TOMBSTONE_SECRET` on qm core. If core and the standalone memory service use the same
Postgres memory tables, inject that same secret into the service as
`MEMORY_SERVICE_TOMBSTONE_SECRET`.

Configure the private standalone service:

```dotenv
MEMORY_SERVICE_DATABASE_URL=<postgres-url>
MEMORY_SERVICE_SIGNING_SECRET=<independent-request-signing-secret>
MEMORY_SERVICE_TOMBSTONE_SECRET=<same-value-as-MEMORY_TOMBSTONE_SECRET>
MEMORY_SERVICE_INTEGRATION_ID=wulo-work
MEMORY_SERVICE_ALLOWED_SCOPE_KINDS=personal
MEMORY_SERVICE_ALLOWED_SCOPE_PREFIXES=personal:<workspace-id>:user:
PORT=8080
```

The service must be reachable from Omnigent over private networking and HTTPS. Start it with
`npm run memory-service`, then require:

```bash
curl -fsS https://memory.example.com/healthz
```

A tombstone-key mismatch, missing secret, non-HTTPS remote URL, failed health check, or database
migration/backup failure stops the rollout.

## 2. Enable shadow recall

Configure Omnigent through its secret environment:

```dotenv
OMNIGENT_FEATURES=memory_runtime
OMNIGENT_QM_MEMORY_URL=https://memory.example.com
OMNIGENT_QM_MEMORY_SIGNING_SECRET=<same-value-as-MEMORY_SERVICE_SIGNING_SECRET>
```

Merge `memory_runtime` into an existing comma-separated `OMNIGENT_FEATURES` value rather than
removing other enabled features. The URL and signing secret must be present together.

Apply this policy only to the pilot agent:

```yaml
memory:
  mode: shadow
  max_context_chars: 6000
  providers:
    - provider: qm-notebook
      scopes: [personal]
      recall: ambient
      capture: off
      fail_open: true
      timeout_ms: 1000
      max_results: 8
      max_chars: 4000
```

Recreate Omnigent and start new pilot sessions. Shadow mode reads authorized personal scope and logs
retrieval metadata but never adds memory to the model prompt. In Omnigent logs, require records of
this form:

```text
memory recall operation=... mode=shadow results=... failures=... chars=... injected=False injection_supported=...
```

Observe an agreed pilot window before promotion. Review a sample of retrievals for relevance, verify
that no request crosses workspace/account scope, explain every provider failure or timeout, and
confirm captured model requests contain no memory block. Keep capture `off` throughout this phase.

Before promotion, run a synthetic erasure drill through `POST /v1/memory/erasures`. Require the
workflow to complete for `qm-notebook`, a subsequent read to return empty content, and a delayed
retry of a pre-erasure capture operation to return the erased-operation response without recreating
data.

## 3. Promote to read-only recall

Change only the pilot agent policy:

```yaml
memory:
  mode: read_only
  max_context_chars: 6000
  providers:
    - provider: qm-notebook
      scopes: [personal]
      recall: ambient
      capture: off
      fail_open: true
      timeout_ms: 1000
      max_results: 8
      max_chars: 4000
```

Start fresh sessions on a harness that supports framework instruction injection. Require
`mode=read_only`, `injected=True`, zero capture jobs, correct account scoping, and an answer that uses
a synthetic recalled fact without following instructions embedded in memory.

Rollback is an agent-policy change from `read_only` to `shadow` or `off`, followed by fresh
sessions. Rollback never deletes data and must not enable capture. Use the erasure workflow for a
data-subject deletion request.

## 4. Provision Company Brain

Create a private company-owned Git repository and separate OAuth clients for every enabled source.
Register these callbacks against the public Omnigent application host:

```text
https://APP_HOST/v1/company-brain/oauth/google/callback
https://APP_HOST/v1/company-brain/oauth/slack/callback
https://APP_HOST/v1/company-brain/oauth/notion/callback
```

Initialize the Docker profile:

```bash
cd deploy/docker
./bootstrap.sh --company-brain
```

Store all generated values and OAuth client secrets in the deployment secret manager. Complete the
Company Brain entries in `.env`, including:

```dotenv
GBRAIN_PUBLIC_URL=https://brain.example.com
OMNIGENT_COMPANY_BRAIN_REPO_URL=<private-git-url>
OMNIGENT_COMPANY_BRAIN_MCP_URL=https://brain.example.com/mcp
OMNIGENT_COMPANY_BRAIN_MCP_AUTH_REF=deployment-secret:company-brain-mcp
OMNIGENT_COMPANY_BRAIN_GIT_PUSH=1
OMNIGENT_COMPANY_BRAIN_NO_EMBEDDING=1
```

Also configure `GIT_TOKEN` and the selected `GOOGLE_*`, `SLACK_*`, and `NOTION_*` OAuth values.
Terminate TLS at the deployment ingress, route `GBRAIN_PUBLIC_URL` to loopback port 3131, and do not
publish the Postgres ports. The certificate must be valid for both the application and brain hosts.

Start and verify the profile:

```bash
docker compose --profile company-brain up -d --build
docker compose --profile company-brain ps
curl -fsS https://brain.example.com/health
docker compose --profile company-brain exec gbrain gbrain doctor
docker compose --profile company-brain exec gbrain gbrain sources status --json
```

Keep keyword-only retrieval until an embedding provider and dimensions pass `gbrain doctor`.

## 5. First sync and agent access

In **Settings > Company brain**, connect one provider, select only organization-shared resources,
review the transformed preview, and activate the source. Require all of the following:

- an atomic commit in the private brain repository;
- a successful sync run with no partial deletion publication;
- `company-shared` reports a recent `last_sync_at`, zero queue depth, no active sync, and no failed
  job;
- canonical source URLs survive in the indexed pages.

After `company-shared` exists, register one read-only agent client on the brain host:

```bash
gbrain agent register wulo-work \
  --harness claude-code \
  --preset daily-driver \
  --source company-shared \
  --federated-read company-shared \
  --scopes read \
  --surface starter \
  --url "$GBRAIN_PUBLIC_URL/mcp" \
  --show-token \
  --json
```

Send the token output directly to the deployment secret manager as
`OMNIGENT_COMPANY_BRAIN_MCP_TOKEN`; do not place it in shell history, Git, an agent bundle, or a
database row. Recreate only Omnigent after the secret is stored:

```bash
docker compose --profile company-brain up -d --no-deps --force-recreate omnigent
```

Create a pilot agent with `company_brain: true`. Ask a deterministic question covered by a synced
page, then require the MCP trace and final answer to name the same source ID, title, canonical URL,
and non-stale passage. Fetch the cited page and compare the quoted text with the committed Markdown.

## 6. Pilot evidence and rollback

Record release digests, migration heads, secret versions (never values), health output, source-status
JSON, first-sync commit SHA, citation trace, erasure receipt, and the shadow/read-only log samples.

Stop promotion and return the memory policy to `shadow` or `off` if scope, deletion, or injection
evidence is ambiguous. To remove Company Brain from agents, unset the managed MCP URL/token and
recreate Omnigent before stopping the `gbrain` profile. Preserve the Git repository and database
volumes for investigation; do not use `docker compose down -v` during a rollback.
