# OMNI-2204: Verify Omnigent Handles CBI Well

## Definition: What is CBI?

**CBI is NOT defined in the Omnigent codebase.** The issue description is a Slack permalink only (databricks.slack.com/archives/C02BBHRCMEV?ts=p1785461313168049), and the concrete requirement lives in that thread.

### Most Likely Candidate (High Confidence)
**CBI = Customer-Brought-In (credentials, keys, secrets, auth tokens)**

Evidence:
- Issue label: `focus: managed` — strongly suggests managed-deployment credential handling
- Omnigent has extensive infrastructure for injecting credentials into managed sandboxes (`omnigent/server/managed_hosts.py`)
- The docs/databricks.md section "BYO API key" vs "Foundation Models" explicitly contrasts bring-your-own vs managed credentials
- Credential injection is a critical seam in the managed-deployment story (see `managed_hosts.py:822–1244` for sandbox env injection patterns)
- No other meaning of "CBI" in Databricks context appears relevant to omnigent

### Alternative Candidates (Lower Confidence)
- **Cloud-Based Infrastructure** — less likely; omnigent already supports many cloud sandbox providers
- **Compliance / Boundary / Isolation** — possible but would be more clearly named "CBSI" or similar in the codebase
- **Custom** something — too vague without the Slack thread

---

## Current State: What Omnigent Does Today

### Managed Credentials (Working)
When a server launches a managed sandbox via `omnigent server`:

1. **Credentials stored server-side.** LLM keys, gateway URLs, git tokens live in environment on the omnigent server
2. **Injection at launch time.** Each sandbox gets a clean env with only the credentials needed (e.g., `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GIT_TOKEN`) injected via provider-specific secrets engines:
   - Modal: Modal Secrets
   - Daytona, Islo, E2B: SERVER env → sandbox env
   - CoreWeave (cwsandbox): Server env passed to provider
   - Databricks Apps: Workspace secrets referenced in config
3. **No credential persistence in sandbox.** The sandbox host runs `omnigent host` which authenticates back to the server via a short-lived token; user credentials never enter the sandbox (see `managed_hosts.py:17–22`)
4. **Credential rotation** — Supported by updating server env vars; no sandbox-side restarts needed

### Known Gaps / Ambiguities (to Verify)

1. **Databricks-managed credential injection** — When omnigent server runs on Databricks Apps with external API keys (BYO):
   - How are customer API keys (OpenAI, Anthropic, etc.) stored safely?
   - Are they Databricks secrets, server env vars, or config?
   - Are they accessible to users / agents, or opaque to them?
   - Does the gateway proxy hide the raw key?

2. **Credential validation at launch** — If a credential is invalid/expired:
   - Does the server validate before injection?
   - Does the sandbox fail gracefully or hang?
   - Is there a retry / refresh mechanism?

3. **Credential isolation between users** — In a multi-user server scenario:
   - Can user A's managed sandbox access user B's credentials?
   - Does the `owner` field in `hosts` table gate credential scope?
   - Are per-user credential stores supported?

4. **Credential audit trail** — For compliance deployments:
   - Which credentials flowed to which sandbox at which time?
   - Is this logged in MLflow Tracing / audit logs?
   - Are secrets redacted from logs?

5. **OAuth / Subscription credentials** (Anthropic Claude Max, OpenAI ChatGPT Plus, Cursor Pro):
   - The Databricks docs mention these are API-key-tier only for Gateway proxying
   - How does omnigent prevent OAuth credentials in managed sandboxes?
   - Is there a validation gate?

6. **Credential refresh** — For short-lived tokens (workspace PAT, OIDC tokens):
   - How does the managed sandbox refresh credentials mid-session?
   - If a token expires during a long-running agent, what happens?

---

## Verification Checklist

### Phase 1: Define the Requirement (BLOCKER)
- [ ] **Ask Zeyi (Rice) Fan** (Linear issue creator) or check the Slack thread for the exact requirement:
  - Q: What does "CBI" mean in this context? (Customer-Brought-In credentials / keys / auth, or something else?)
  - Q: In which deployment scenario does CBI apply? (Databricks Apps only? All managed sandboxes? OSS deployments with BYO keys?)
  - Q: What is the desired behavior? (e.g., "credentials should never be visible to agents", "rotation should be atomic", "audit trail required")
  - Q: Are there compliance/security constraints? (FedRAMP, SOC 2, data residency, etc.)
  - Q: What is the success criterion for "handles CBI well"?

### Phase 2: Code Audit (High Confidence Can Start Without Phase 1)
- [ ] **Credential injection on managed sandboxes:**
  - Read `omnigent/server/managed_hosts.py:822–1244` (env injection)
  - Verify credentials are never logged or exposed in debug output
  - Check each sandbox provider's credential path (Modal secrets, Daytona env, Databricks secrets API, etc.)

- [ ] **Databricks-specific credential storage:**
  - Read `omnigent/onboarding/sandboxes/databricks_apps.py` (if exists) and `deploy/databricks/README.md`
  - Trace how `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc. are consumed by the omnigent server
  - Verify if they come from workspace secrets (`databricks secrets`) or env vars

- [ ] **Multi-user isolation:**
  - Read `omnigent/stores/host_store.py` (host ownership model)
  - Check if credentials are scoped by `owner` field
  - Verify no cross-user credential leakage in session creation (`omnigent/stores/session_store.py`)

- [ ] **Audit & observability:**
  - Check `omnigent/telemetry/context.py` for trace attribute redaction (e.g., `gen_ai.request.api_key` should not appear)
  - Verify MLflow Tracing or audit logs don't capture raw credentials
  - Check sandbox launch logs for credential exposure

- [ ] **Validation on startup:**
  - Check `omnigent host` for env-var validation (e.g., does it fail fast if `OPENAI_API_KEY` is invalid?)
  - Look for retry / refresh logic on auth failure
  - Verify error messages don't leak sensitive data

### Phase 3: Testing (Can Parallelize with Phase 2)
- [ ] **Unit tests:**
  - `tests/onboarding/sandboxes/` — Check for managed-credential injection tests
  - `tests/server/test_managed_hosts.py` (if exists) — Verify env-injection tests cover credential names
  - `tests/stores/test_host_store.py` — Verify multi-user isolation

- [ ] **Integration tests:**
  - Launch a managed sandbox with a BYO credential (e.g., test OpenAI key)
  - Verify the credential is injected and the sandbox can call the LLM
  - Verify the credential does NOT appear in logs or traces
  - Launch two parallel sandboxes for different users; verify isolation

- [ ] **Databricks-specific e2e (if applicable):**
  - Deploy omnigent on a Databricks Apps instance
  - Create a managed session with Gateway endpoint (BYO key proxied through Gateway)
  - Verify the raw API key never touches the sandbox (only the Gateway endpoint URL and workspace PAT)
  - Capture the trace in MLflow; verify no secrets are exposed

### Phase 4: Documentation (Depends on Phase 1 Requirements)
- [ ] **Write or update documentation on:**
  - How credentials are stored and injected for managed deployments
  - Which credential types are supported per sandbox provider
  - Credential isolation model (per-user, per-host, per-session)
  - Audit trail and compliance readiness
  - Known limitations (e.g., OAuth subscriptions not supported in managed mode)

- [ ] **Add security advisory (if needed):**
  - Warn users against storing credentials in agent specs / logs
  - Document the secure path (env vars → sandbox injection → agent use)

---

## Blockers & Next Steps

**This task is BLOCKED until the Slack thread is accessible or the requirement is clarified by the issue creator.**

### Immediate Action: Unblock the Requirement
1. Contact **Zeyi (Rice) Fan** (Linear issue creator) and ask for the 5 questions above
2. If the Slack thread is accessible: Read `databricks.slack.com/archives/C02BBHRCMEV?ts=p1785461313168049` and post a summary here
3. If no access: Escalate to a team member who can read the thread (likely internal Databricks Slack)

### Contingency: Start Code Audit Without Requirement
If the requirement is still locked, we can proceed with the code audit in Phase 2 to:
- Document current credential-handling behavior
- Identify gaps or risks
- Propose design improvements (even if the requirement is unspecified)

---

## Key Files to Review (Pre-Phase 2)

1. **Managed credential injection:**
   - `omnigent/server/managed_hosts.py` (lines 17–22, 107, 481–508, 822–1244)
   - `omnigent/server/app.py` (lines 837+, managed-host setup)

2. **Databricks integration:**
   - `deploy/databricks/README.md` (sections on credentials and the Gateway)
   - `docs/databricks.md` (Auth tier compatibility, API key vs OAuth, BYO vs Foundation Models)
   - `omnigent/onboarding/sandboxes/` (all Databricks-related modules)

3. **Multi-user isolation & audit:**
   - `omnigent/stores/host_store.py` (ownership model)
   - `omnigent/stores/session_store.py` (session ownership)
   - `omnigent/telemetry/context.py` (trace redaction)

4. **Tests (to run / extend):**
   - `tests/onboarding/sandboxes/` (all)
   - `tests/server/` (managed-host tests if any)

---

## Success Criteria (Tentative)

Once the requirement is known, success = one of:
1. ✅ **PR opened** — If a small, obvious doc fix or config gap is found
2. ✅ **Verification plan published** — This document, with Phase 1 requirement now filled in
3. ✅ **Test suite added** — New tests for credential injection, isolation, audit trail
4. ✅ **Known gaps logged** — If current code doesn't meet the requirement, a clear list of what to fix

---

## References

- Linear issue: OMNI-2204
- Slack requirement: `databricks.slack.com/archives/C02BBHRCMEV?ts=p1785461313168049` (requires access)
- Key memory: `byo-lakebox-host-cuj.md` — Databricks Sandbox credential patterns
- Codebase docs: `docs/databricks.md`, `deploy/databricks/README.md`, `SECURITY.md`
