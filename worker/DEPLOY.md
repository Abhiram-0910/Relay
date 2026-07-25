# Relay Worker — one-time provisioning

The Worker is the public front door; the GitHub Action is the compute. Three credentials, three
jobs (see ARCHITECTURE.md). Do these once. Secrets are set out-of-band — none of them belong in
git or in chat.

## 1. KV namespace
```bash
cd worker
wrangler kv namespace create RELAY_KV
```
Copy the printed `id` into `wrangler.jsonc` → `kv_namespaces[0].id` (replacing the placeholder).

## 2. Worker secrets
```bash
cd worker
wrangler secret put GH_PAT            # paste the fine-grained PAT (Actions: read/write)
wrangler secret put CALLBACK_SECRET   # paste a random value, e.g. `openssl rand -hex 32`
```
Keep the `CALLBACK_SECRET` value — it must be identical in step 4.

## 3. Deploy
```bash
cd worker
wrangler deploy
```
Note the deployed URL (e.g. `https://relay-worker.<subdomain>.workers.dev`).

## 4. GitHub Actions secrets (repo: Abhiram-0910/Relay)
```bash
gh secret set CALLBACK_SECRET   # SAME value as the Worker's CALLBACK_SECRET
gh secret set GEMINI_API_KEY    # Gemini free-tier key (self-correction)
```
(Or repo → Settings → Secrets and variables → Actions.)

## 5. Workflow must be on the default branch
`.github/workflows/generate.yml` has to be on `main` for `workflow_dispatch` to find it. Push it.

## 6. Verify end-to-end
```bash
scripts/verify_deployed.sh https://relay-worker.<subdomain>.workers.dev
```

## Credential map
| Credential | Where it lives | Direction | Job |
|---|---|---|---|
| `GH_PAT` | Worker secret | Worker → GitHub | trigger `workflow_dispatch` |
| `CALLBACK_SECRET` | Worker secret **and** GitHub Actions secret (same value) | Action → Worker | auth progress callbacks |
| `GEMINI_API_KEY` | GitHub Actions secret | Action → Gemini | self-correction |
| `GITHUB_TOKEN` | auto in Action | Action → GitHub | checkout only; never touches the Worker |
