# ifcgref-corenetx

A Singapore-focused fork of [tudelft3d/ifcgref](https://github.com/tudelft3d/ifcgref) with a CORENET X / IFC-SG compliance check on top of the original geo-referencing inspector.

Inspect the geo-referencing data in your IFC model, see Corenet X IFC-SG compliance flags for Singapore models, and visualise the geometry on a real-world map.

## What this fork adds

- **CORENET X IFC-SG checklist** on the report page — three checks: CRS is SVY21 / EPSG:3414, `IfcGeographicElement` with `ObjectType='SITEBOUNDARY'` is present, and coordinates land within the SVY21 Singapore map. Failing checks show tips for fixing them in your BIM software.
- **IFC unit awareness** — placement coordinates are converted to metres from whatever the IFC project unit is (`MILLI`/`CENTI`/`METRE`, or `IfcConversionBasedUnit` like `INCH`/`FOOT`) before comparing against SG bounds.
- **3D viewer site-boundary toggle** — iOS-style switch to subset the loaded IFC down to just the `SITEBOUNDARY` geometry vs. showing the whole model.
- **50 MB upload cap** with three-layer enforcement and a privacy warning, to discourage uploading federated multi-discipline BIMs with sensitive data.
- **Magic-byte upload check** — rejects anything that doesn't start with `ISO-10303-21`, regardless of extension.
- **Production-ready config** — env-driven secrets, hardened session cookies, structured logging, gunicorn entry point.

## Running locally

```bash
# 1. Clone and enter the directory
git clone https://github.com/chunyen93/ifcgref-corenetx.git
cd ifcgref-corenetx

# 2. (Recommended) create a virtualenv
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy the env template and fill it in
cp .env.example .env
# Edit .env — set FLASK_SECRET_KEY (any random string for dev) and
# MAPTILER_KEY (free at https://www.maptiler.com/)

# 5. Load env and run
export $(grep -v '^#' .env | xargs)
python app.py
```

Open <http://localhost:5000/>.

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `FLASK_SECRET_KEY` | prod | `dev-secret-change-me` | Signs the session cookie. Generate with `python -c "import secrets; print(secrets.token_hex(32))"`. |
| `MAPTILER_KEY` | yes | empty | MapTiler API key for the 3D viewer basemap. Free tier is enough. |
| `FLASK_ENV` | no | empty | Set to `development` to enable debug mode and allow http session cookies. |
| `LOG_LEVEL` | no | `INFO` | Standard logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `PORT` | no | `5000` | Dev server port. Production hosts (Render, Heroku) inject their own. |

## Deploying

### Render.com (recommended)

The repo includes [render.yaml](render.yaml) — connect this repo at <https://dashboard.render.com/select-repo> and Render will read the config. On the first deploy:

1. Render auto-generates `FLASK_SECRET_KEY`.
2. You'll be prompted for `MAPTILER_KEY` (sync: false → set manually in the dashboard).
3. Build runs `pip install -r requirements.txt`; start runs `gunicorn app:app …`.
4. Free tier spins down on idle (~30 s wake on first hit).

### Any host with Python + gunicorn

```bash
gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120
```

A [Procfile](Procfile) is included for Heroku-style hosts.

> **Won't work on Netlify or Vercel** — both are serverless. This app needs persistent disk between requests (upload → parse → visualize) and 60-second-plus parsing time, neither of which fits serverless function limits.

## Privacy

This is **not** a local-only tool — uploaded IFCs are sent to and processed on the hosting server. Each new upload purges the previous file from the local cache, but earlier copies may persist in server logs / backups depending on how the host operates. **Don't upload confidential or NDA-restricted models** to a public-internet deployment. The 50 MB cap and the warnings on every page exist to nudge you toward site/coordination models, not federated full-discipline BIMs.

## Supported IFC versions

| IFC version | Notes |
|---|---|
| IFC 4.3 ADD2 | Full support |
| IFC 4 (all addenda) | Full support |
| IFC 2x3 | Geo-referencing via Pset_SiteCommon |

## Project layout

```
app.py              Flask routes, IFC parsing, CORENET X checks
georeference_ifc/   Vendored geo-reference helpers from upstream
static/             CSS, fonts, images, favicon
templates/          Jinja templates (upload, result, view3D, convert, survey)
uploads/            Runtime upload cache (gitignored)
requirements.txt    Pinned Python dependencies
render.yaml         Render.com one-click deploy config
Procfile            Heroku-style web process declaration
```

## Credits

Built on top of [tudelft3d/ifcgref](https://github.com/tudelft3d/ifcgref) by the 3D Geoinformation group at TU Delft (MIT). The CORENET X / IFC-SG layer follows the BCA CORENET X Pilot Mapping; refer to the BCA CORENET X website for the official submission requirements.

## License

MIT — see [LICENSE](LICENSE).
