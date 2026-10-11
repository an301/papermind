#!/usr/bin/env bash
# Set up PaperMind on a fresh Ubuntu server (22.04 or 24.04, 8 GB RAM or more).
# Run it from the cloned repo, as root:
#
#   git clone https://github.com/an301/papermind.git /opt/papermind
#   cd /opt/papermind && sudo bash infra/setup-server.sh
#
# Safe to run again: it keeps an existing .env, rebuilds and restarts, and
# re-seeding the concept graph is idempotent. DEPLOY.md explains each step.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_DIR="$(pwd)"
COMPOSE=(docker compose --env-file "$REPO_DIR/.env" -f "$REPO_DIR/infra/docker-compose.yml")
STARTER_PAPERS=(1706.03762 1810.04805 1512.03385 1412.6980 1502.03167 2010.11929 2106.09685 2305.18290)

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this as root: sudo bash infra/setup-server.sh" >&2
  exit 1
fi

say "1/6  Docker"
if command -v docker >/dev/null 2>&1; then
  echo "Docker is already installed."
else
  curl -fsSL https://get.docker.com | sh
fi

say "2/6  Swap"
# The stack needs about 5 GB; swap keeps a memory spike (an ingestion while
# readers are asking) from killing a container on an 8 GB machine.
if swapon --show | grep -q .; then
  echo "Swap is already on."
else
  fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab
  echo "Added 4 GB of swap."
fi

say "3/6  Settings (.env)"
if [ -f .env ]; then
  echo "Keeping the existing .env. Edit it and run this script again to change settings."
else
  read -rp "Anthropic API key (sk-ant-...): " ANTHROPIC_KEY
  [ -n "$ANTHROPIC_KEY" ] || { echo "An Anthropic API key is required." >&2; exit 1; }
  read -rp "Domain for the site, e.g. papermind.example.com (leave blank to start on this server's IP): " DOMAIN
  read -rp "Access code readers will type (leave blank to generate one): " ACCESS_CODE
  if [ -z "$ACCESS_CODE" ]; then
    ACCESS_CODE="read-$(openssl rand -hex 3)-$(openssl rand -hex 3)"
  fi
  read -rp "Most to spend on Claude per day, in US dollars [5]: " BUDGET
  umask 077
  cat >.env <<EOF
# PaperMind server settings. Change a value, then: sudo bash infra/setup-server.sh
ANTHROPIC_API_KEY=$ANTHROPIC_KEY
NEO4J_PASSWORD=$(openssl rand -hex 16)

# Your domain (Caddy gets the HTTPS certificate). Blank = plain http on the IP.
SITE_ADDRESS=$DOMAIN

# Guards for a public server (papermind/api/guards.py)
ACCESS_CODE=$ACCESS_CODE
QUESTIONS_PER_HOUR=20
PAPERS_PER_DAY=5
DAILY_BUDGET_USD=${BUDGET:-5}
ALLOW_PAPER_DELETE=false

# The answer writer: claude, or mistral with MISTRAL_BASE_URL pointing at a vLLM server.
WRITER_BACKEND=claude
EOF
  echo "Wrote .env (readable by root only)."
fi

say "4/6  Build and start (the first build takes 5-10 minutes)"
"${COMPOSE[@]}" up -d --build

say "5/6  Load the concept graph"
for attempt in $(seq 1 30); do
  if "${COMPOSE[@]}" run --rm api python scripts/seed_graph.py; then
    break
  fi
  if [ "$attempt" -eq 30 ]; then
    echo "Neo4j did not come up. See: ${COMPOSE[*]} logs neo4j" >&2
    exit 1
  fi
  echo "Neo4j is still starting; trying again in 5 s."
  sleep 5
done

say "6/6  Start on boot, and the 'papermind' command"
install -m 644 infra/papermind.service /etc/systemd/system/papermind.service
sed -i "s#WorkingDirectory=.*#WorkingDirectory=$REPO_DIR#" /etc/systemd/system/papermind.service
systemctl daemon-reload
systemctl enable papermind.service >/dev/null
cat >/usr/local/bin/papermind <<EOF
#!/usr/bin/env bash
# docker compose for this PaperMind install, e.g. "papermind ps", "papermind logs -f api".
exec docker compose --env-file "$REPO_DIR/.env" -f "$REPO_DIR/infra/docker-compose.yml" "\$@"
EOF
chmod 755 /usr/local/bin/papermind

PAPER_COUNT="$("${COMPOSE[@]}" run --rm -T api sh -c 'ls /app/data/papers | wc -l' 2>/dev/null | tr -d '[:space:]' || echo 0)"
if [ "${PAPER_COUNT:-0}" = "0" ]; then
  read -rp "Add ${#STARTER_PAPERS[@]} well-known ML papers (Transformer, BERT, ResNet, ...) to the library now? About 10 minutes. [Y/n] " STARTERS
  if [ "${STARTERS:-Y}" != "n" ] && [ "${STARTERS:-Y}" != "N" ]; then
    args=()
    for id in "${STARTER_PAPERS[@]}"; do args+=(--arxiv "$id"); done
    "${COMPOSE[@]}" run --rm api python -m papermind.ingest "${args[@]}" || echo "Some papers failed; add them from the app later."
  fi
fi

SITE="$(grep '^SITE_ADDRESS=' .env | cut -d= -f2-)"
CODE="$(grep '^ACCESS_CODE=' .env | cut -d= -f2-)"
if [ -n "$SITE" ]; then
  URL="https://$SITE"
else
  URL="http://$(curl -fsS https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"
fi
say "Done."
echo "Open:         $URL"
echo "Access code:  $CODE"
echo "Status:       papermind ps        Logs: papermind logs -f api"
