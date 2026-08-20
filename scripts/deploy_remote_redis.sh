#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
remote_host="${REMOTE_HOST:-ubuntu@119.91.123.102}"
remote_dir="${REMOTE_DIR:-kgharness-redis}"
compose_file="$project_dir/docker/compose.redis.yaml"
env_file="$project_dir/.env"

if [[ ! -f "$compose_file" || ! -f "$env_file" ]]; then
  echo "Missing docker/compose.redis.yaml or .env" >&2
  exit 1
fi

redis_password="$(sed -n 's/^REDIS_PASSWORD=//p' "$env_file" | tail -n 1)"
if [[ ! "$redis_password" =~ ^[A-Za-z0-9]{32,}$ ]]; then
  echo "REDIS_PASSWORD must be a 32+ character alphanumeric value" >&2
  exit 1
fi

echo "Preparing Redis deployment on $remote_host"
ssh "$remote_host" "install -d -m 700 '$remote_dir'"
ssh "$remote_host" "umask 077; cat > '$remote_dir/compose.redis.yaml'" < "$compose_file"
printf '%s\n' \
  "REDIS_PASSWORD=$redis_password" \
  "REDIS_MAXMEMORY=512mb" \
  "REDIS_CONTAINER_MEMORY=640m" \
  | ssh "$remote_host" "umask 077; cat > '$remote_dir/.env'"

ssh "$remote_host" "cd '$remote_dir' && \
  docker compose --env-file .env -f compose.redis.yaml up -d --force-recreate && \
  docker compose --env-file .env -f compose.redis.yaml ps && \
  docker compose --env-file .env -f compose.redis.yaml port redis 6379 && \
  docker exec kgharness-redis sh -c 'redis-cli -a \"\$REDIS_PASSWORD\" --no-auth-warning ping'"

echo "Redis deployment completed on 119.91.123.102:16379"
