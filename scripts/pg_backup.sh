#!/usr/bin/env bash
# Extra protection on top of Cloud SQL's automatic daily backups: an SQL export into a Cloud Storage bucket.
#   ./scripts/pg_backup.sh PROJECT INSTANCE DATABASE gs://your-backup-bucket
set -euo pipefail
PROJECT=$1; INSTANCE=$2; DB=$3; BUCKET=$4
gcloud sql export sql "$INSTANCE" "$BUCKET/catalog_$(date -u +%Y%m%d_%H%M%S).sql.gz" --database="$DB" --project="$PROJECT" --offload
