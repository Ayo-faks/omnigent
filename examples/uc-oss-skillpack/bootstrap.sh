#!/usr/bin/env bash
# Create the catalog / schema / volume the skillpack POC needs.
#
# Idempotent: re-running is safe (existing objects return HTTP 409, which this
# script treats as success). Talks to the UC OSS REST API directly with curl
# so it has no Python dependency.
#
# POC / not for production.
set -euo pipefail

UC_OSS_URI="${UC_OSS_URI:-http://localhost:8080}"
API="${UC_OSS_URI}/api/2.1/unity-catalog"

CATALOG="${SKILLPACK_CATALOG:-unity}"
SCHEMA="${SKILLPACK_SCHEMA:-omnigent}"
VOLUME="${SKILLPACK_VOLUME:-skillpacks}"
# Storage location INSIDE the container. ./data on the host is bind-mounted to
# /home/unitycatalog/etc/data (see docker-compose.yml), so the host sees these
# bytes under ./data/${VOLUME}.
VOLUME_STORAGE="file:///home/unitycatalog/etc/data/${VOLUME}"

# POST helper: succeeds on 2xx AND on 409 (already exists).
post() {
  local path="$1" body="$2" code
  code="$(curl -sS -o /tmp/skillpack_boot.out -w '%{http_code}' \
    -X POST "${API}${path}" \
    -H 'Content-Type: application/json' \
    -d "${body}")"
  if [[ "${code}" == 2* ]]; then
    echo "  ok (${code})"
  elif [[ "${code}" == "409" ]]; then
    echo "  already exists (409)"
  else
    echo "  FAILED (${code}): $(cat /tmp/skillpack_boot.out)" >&2
    return 1
  fi
}

echo "UC OSS server: ${UC_OSS_URI}"

echo "catalog: ${CATALOG}"
post "/catalogs" "{\"name\":\"${CATALOG}\",\"comment\":\"Omnigent skillpack POC\"}"

echo "schema: ${CATALOG}.${SCHEMA}"
post "/schemas" "{\"name\":\"${SCHEMA}\",\"catalog_name\":\"${CATALOG}\",\"comment\":\"Omnigent skillpack POC\"}"

echo "volume: ${CATALOG}.${SCHEMA}.${VOLUME} -> ${VOLUME_STORAGE}"
post "/volumes" "{\"catalog_name\":\"${CATALOG}\",\"schema_name\":\"${SCHEMA}\",\"name\":\"${VOLUME}\",\"volume_type\":\"EXTERNAL\",\"storage_location\":\"${VOLUME_STORAGE}\",\"comment\":\"Skill/knowledge pack blobs (POC)\"}"

echo
echo "Bootstrap complete."
echo "  volume       : ${CATALOG}.${SCHEMA}.${VOLUME}"
echo "  host storage : ./data/${VOLUME}"
echo
echo "Point the CLI at it with:"
echo "  export UC_OSS_URI=${UC_OSS_URI}"
echo "  export UC_OSS_VOLUME=${CATALOG}.${SCHEMA}.${VOLUME}"
echo "  export UC_OSS_VOLUME_LOCAL_PATH=\$(pwd)/data/${VOLUME}"
