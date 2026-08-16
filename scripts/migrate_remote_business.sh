#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

SOURCE_CONTAINER="${SOURCE_MYSQL_CONTAINER:-deepsearch-mysql}"
MIGRATION_USER="kgharness_migrator"
MIGRATION_PASSWORD="$(openssl rand -hex 24)"

mysql_admin() {
  docker exec -i "$SOURCE_CONTAINER" sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD"'
}

cleanup() {
  printf "DROP USER IF EXISTS '%s'@'%%';\n" "$MIGRATION_USER" \
    | mysql_admin >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

mysql_admin <<SQL
DROP USER IF EXISTS '${MIGRATION_USER}'@'%';
CREATE USER '${MIGRATION_USER}'@'%' IDENTIFIED BY '${MIGRATION_PASSWORD}';
GRANT SELECT ON deepsearch_db.* TO '${MIGRATION_USER}'@'%';
SQL

docker compose run --rm \
  -e MYSQL_HOST=host.docker.internal \
  -e MYSQL_PORT=3306 \
  -e MYSQL_USER="$MIGRATION_USER" \
  -e MYSQL_PASSWORD="$MIGRATION_PASSWORD" \
  -e MYSQL_DATABASE=deepsearch_db \
  api python scripts/migrate_mysql_to_postgres.py
