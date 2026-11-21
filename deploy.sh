#!/usr/bin/env bash
# Deployment recipe for Google Cloud Run + Cloud SQL (PostgreSQL). NOT executed or tested by the developer (no cloud access):
# read it, set the variables, and run the steps one by one.
set -euo pipefail
PROJECT=${PROJECT:?set PROJECT}; REGION=${REGION:-europe-west1}; SERVICE=catalog-system
INSTANCE=catalog-db; DBNAME=catalog; DBUSER=catalog

gcloud config set project "$PROJECT"
gcloud services enable run.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com drive.googleapis.com sheets.googleapis.com cloudbuild.googleapis.com

# 1) PostgreSQL with automatic daily backups + point-in-time recovery
gcloud sql instances create "$INSTANCE" --database-version=POSTGRES_15 --tier=db-g1-small --region="$REGION" \
  --backup-start-time=02:00 --enable-point-in-time-recovery --retained-backups-count=14
DBPASS=$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 28)
gcloud sql databases create "$DBNAME" --instance="$INSTANCE"
gcloud sql users create "$DBUSER" --instance="$INSTANCE" --password="$DBPASS"
CONN="$PROJECT:$REGION:$INSTANCE"

# 2) Secrets (the Google token JSON comes from: python scripts/get_token.py > token.json)
printf 'postgresql://%s:%s@/%s?host=/cloudsql/%s' "$DBUSER" "$DBPASS" "$DBNAME" "$CONN" | gcloud secrets create catalog-database-url --data-file=-
head -c 48 /dev/urandom | base64 | gcloud secrets create catalog-secret-key --data-file=-
printf '%s' "${OWNER_PASSWORD:?set OWNER_PASSWORD}" | gcloud secrets create catalog-owner-password --data-file=-
gcloud secrets create catalog-google-token --data-file=token.json

# 3) Service account of the service may read the secrets and reach Cloud SQL
SA=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')-compute@developer.gserviceaccount.com
for S in catalog-database-url catalog-secret-key catalog-owner-password catalog-google-token; do
  gcloud secrets add-iam-policy-binding $S --member="serviceAccount:$SA" --role=roles/secretmanager.secretAccessor
done
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$SA" --role=roles/cloudsql.client

# 4) Deploy. 1 instance minimum + CPU always on: extraction runs in a background thread after the upload request returns.
gcloud run deploy "$SERVICE" --source . --region "$REGION" --allow-unauthenticated \
  --add-cloudsql-instances "$CONN" --memory 2Gi --cpu 2 --min-instances 1 --max-instances 2 --no-cpu-throttling \
  --timeout 3600 --concurrency 20 \
  --set-env-vars APP_ENV=production,STORAGE_BACKEND=drive,OWNER_USERNAME=${OWNER_USERNAME:-owner} \
  --set-secrets DATABASE_URL=catalog-database-url:latest,SECRET_KEY=catalog-secret-key:latest,OWNER_PASSWORD=catalog-owner-password:latest,GOOGLE_OAUTH_TOKEN_JSON=catalog-google-token:latest
gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)'
