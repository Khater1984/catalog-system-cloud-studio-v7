# Catalog System — Cloud Studio v7

A multi-user product catalog management system that extracts structured data (models, specifications, images) from supplier PDF catalogs, organizes them into products and models, and generates branded datasheets and multi-level catalogs as PDF.

Originally a single-user local application, it now supports cloud deployment (Google Cloud Run + Cloud SQL + Google Drive) with authentication, deduplication, and centralized storage.

## Features

- **PDF Extraction**: Parse supplier catalogs for models, specifications, and images (with improvements for JPX images, series fields, and code cleaning).
- **Product Hierarchy**: Products → Models, with suggestion grouping, activation/deactivation, change history, and missing-data reports.
- **PDF Generation**: Datasheets, product catalogs, and master catalogs with full branding (logos, colors, fonts, watermarks, paper textures). Arabic RTL support via `arabic-reshaper` + `python-bidi`.
- **Cloud Ready**: Deployable on Cloud Run with Cloud SQL (PostgreSQL) and Google Drive storage. Multi-user authentication and role support.
- **Deduplication & Review**: Detect duplicate catalogs, review queues, and approval workflows.
- **Export Center & Studio**: Advanced product studio, export templates, and versioned PDF outputs.
- **Migration**: Tools to migrate from local SQLite + filesystem storage to PostgreSQL + Drive.

## Requirements

- **Python 3.11+** (Dockerfile uses 3.12; `pillow==13.0.0` requires ≥3.11)
- Internet connection for the first dependency install
- For cloud mode: Google Cloud project with Drive API + Sheets API enabled, OAuth credentials, Cloud SQL instance
- Optional: Arabic shaping libraries for proper RTL PDF output (`arabic-reshaper`, `python-bidi`)

## Quick Start (Local Mode)

```bash
# Clone the repository
git clone <your-repo-url>
cd catalog_system

# Create and activate a virtual environment (recommended)
python3.11 -m venv venv
source venv/bin/activate          # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run in classic local mode (no login, local files)
# On Linux/macOS:
./run.sh
# On Windows:
run.bat

# Or manually:
export AUTH_DISABLED=1 STORAGE_BACKEND=local
python app.py
```

Open http://127.0.0.1:5050 in your browser.

Local data is stored in the `data/` folder (database + images). This folder is gitignored — back it up separately.

## Cloud / Production Mode

See `README_CLOUD_AR.md` and `deploy.sh` for full instructions.

High-level steps:
1. Set up Google Cloud (Drive API, Sheets API, OAuth consent screen in Production).
2. Create OAuth Desktop client → download `credentials.json` (do **not** commit it).
3. Run `python scripts/get_token.py > token.json` once (local) to obtain the refresh token.
4. Configure environment variables (see `.env.example`).
5. Deploy via `deploy.sh` to Cloud Run + Cloud SQL.
6. Migrate existing data with `migrate_to_cloud.py`.

**Never commit** `.env`, `token.json`, `credentials.json`, or the `data/` directory.

## Running Tests

```bash
# Activate the virtual environment first
python -m unittest discover -s tests
```

Expected (from the v7 notes): ~86 tests, most passing. One test related to Arabic RTL may be skipped if the shaping libraries are not installed.

For PostgreSQL testing:
```bash
TEST_DATABASE_URL=postgresql://user:pass@localhost/testdb python -m unittest discover -s tests
```

**Note**: The large fixture `tests/fixtures/real/ideal_rebar.pdf` (~4 MB) is required by several tests (`test_ui.py`, `test_product_hub.py`, `test_real_catalog.py`). Do not remove it.

## Project Structure

| Path | Purpose |
|------|---------|
| `app.py` | Main Flask application entry point |
| `extractor.py` | PDF catalog extraction engine |
| `pdf_engine.py` / `pdfgen.py` | PDF generation (datasheets & catalogs) |
| `product_hub.py`, `product_studio.py` | Product management UI and logic |
| `db.py` | Database layer (SQLite local / PostgreSQL cloud) |
| `auth.py` | Authentication and multi-user support |
| `storage.py` | Storage backend abstraction (local / Google Drive) |
| `deploy.sh`, `Dockerfile` | Cloud deployment |
| `tests/` | Unit and integration tests + fixtures |
| `scripts/` | Utility scripts (token generation, backup, restore drill, catalog lab) |
| `.env.example` | Template for environment variables |

## Configuration

Copy `.env.example` to `.env` and fill in real values only on your local machine or in Secret Manager. Key variables:

- `SECRET_KEY`
- `DATABASE_URL` (PostgreSQL connection string for cloud)
- `STORAGE_BACKEND=local` or `drive`
- `OWNER_USERNAME` / `OWNER_PASSWORD` (first user bootstrap)
- `GOOGLE_OAUTH_TOKEN_JSON` (for Drive storage)

## Arabic Documentation

- `README_AR.txt` — Original local-mode guide
- `README_CLOUD_AR.md` — Cloud deployment guide
- `CHANGES_V7_AR.md` — Detailed changelog for v7
- `REPORT_AR.md` — Acceptance and testing report
- `README_AR.md` — Short Arabic summary (this release)

## License

Not specified in the original package. Add a `LICENSE` file if you intend to open-source the project.

## Important Notes

- Do **not** commit real secrets or the `data/` folder.
- The system is designed so that extraction does not guess; uncertain links go to a review queue.
- Arabic PDF rendering requires the optional reshaper libraries and has known limitations with mixed LTR/RTL content.
- For production, prefer the cloud configuration with proper secret management.
