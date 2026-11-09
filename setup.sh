#!/usr/bin/env bash
# One-time setup after `docker compose up -d`: creates the two n8n credentials from .env,
# imports the three workflows, publishes them and restarts n8n so the webhooks go live.
set -euo pipefail
EXEC=${EXEC:-"docker compose exec -T n8n"}       # override to run against a container you started yourself
RESTART=${RESTART:-"docker compose restart n8n"}
PG_HOST=${PG_HOST:-postgres}

$EXEC env PG_HOST="$PG_HOST" node -e '
const e = process.env;
if (!e.OPENROUTER_API_KEY || !e.POSTGRES_PASSWORD) { console.error("OPENROUTER_API_KEY and POSTGRES_PASSWORD must be set for the n8n container"); process.exit(1); }
const creds = [
  { id: "leadsdbcred00001", name: "Leads DB", type: "postgres",
    data: { host: e.PG_HOST, port: 5432, database: "leads", user: "n8n", password: e.POSTGRES_PASSWORD, ssl: "disable" } },
  { id: "openroutercred01", name: "OpenRouter", type: "httpHeaderAuth",
    data: { name: "Authorization", value: "Bearer " + e.OPENROUTER_API_KEY } },
];
require("fs").writeFileSync("/tmp/creds.json", JSON.stringify(creds), { mode: 0o600 });'
$EXEC n8n import:credentials --input=/tmp/creds.json
$EXEC rm -f /tmp/creds.json
$EXEC n8n import:workflow --separate --input=/import/workflows
for id in errorlog00000001 approvelink00001 leadintake000001; do
  $EXEC n8n publish:workflow --id="$id" >/dev/null
done
$RESTART >/dev/null
echo "Done. Open http://localhost:5678 to create the owner account, then run: python3 tests/run.py"
