# ifcgref-corenetx

A web tool for **checking IFC geo-referencing against the Singapore CORENET X / IFC-SG standard**. Upload your IFC, get a pass/fail report on the three things that matter for BCA submission, and see the model placed on a real-world map.

Useful for BIM coordinators, modellers, and IFC consultants who need to verify a model is correctly geo-referenced **before** submitting to CORENET X.

## What it checks

The report page runs three checks against the BCA IFC-SG requirements:

1. **CRS is SVY21 (EPSG:3414)** — declared via `IfcProjectedCRS`.
2. **`IfcGeographicElement` with `ObjectType = SITEBOUNDARY` exists** — the IFC-SG site-boundary element is present in the model.
3. **Coordinates land inside the SVY21 Singapore map** — the model's real-world placement (`IfcSite.ObjectPlacement` or the SITEBOUNDARY element) falls within the SG E/N bounds. Coordinates are converted from the IFC project's unit (mm, cm, m, inch, foot…) before comparison.

Failing checks show inline fix tips. A footer card points to the BCA CORENET X submission requirements when anything is non-compliant.

## Other features

- **3D map viewer** — places the IFC model on a MapTiler basemap using the IFC's `IfcMapConversion` rotation/scale, with an iOS-style toggle to show the whole model or just the SITEBOUNDARY geometry.
- **50 MB upload cap** with magic-byte verification (`ISO-10303-21` header) to discourage uploading federated full-discipline BIMs and reject mis-typed files.
- **Server-upload privacy warnings** on every page — the IFC is processed on the host, not in your browser. Don't upload confidential models.
- **Production-ready** — env-driven secrets, gunicorn entry point, hardened session cookies, structured logging.

## Running locally

```bash
git clone https://github.com/chunyen93/ifcgref-corenetx.git
cd ifcgref-corenetx

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env: set FLASK_SECRET_KEY (any random string for dev) and
# MAPTILER_KEY (free at https://www.maptiler.com/)

export $(grep -v '^#' .env | xargs)
python app.py
```

Then open <http://localhost:5000/>.

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `FLASK_SECRET_KEY` | prod | `dev-secret-change-me` | Signs the session cookie. Generate with `python -c "import secrets; print(secrets.token_hex(32))"`. |
| `MAPTILER_KEY` | yes | empty | MapTiler API key for the 3D viewer's basemap. Free tier is enough. |
| `FLASK_ENV` | no | empty | Set to `development` to enable debug mode and allow http session cookies. |
| `LOG_LEVEL` | no | `INFO` | Standard logging level. |
| `PORT` | no | `5000` | Dev server port. Production hosts (Render, Heroku) inject their own. |

## Deploying

### Render.com (recommended)

The repo includes [render.yaml](render.yaml) — connect at <https://dashboard.render.com/select-repo> and Render reads the config. `FLASK_SECRET_KEY` is auto-generated; set `MAPTILER_KEY` in the dashboard when prompted. First build takes 5–10 minutes (ifcopenshell + pyproj wheels).

### Any host with Python + gunicorn

```bash
gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120
```

A [Procfile](Procfile) is included for Heroku-style hosts.

> **Won't work on Netlify or Vercel** — both are serverless. This app needs persistent disk between the upload and visualize requests, and IFC parsing can exceed serverless function timeouts.

## Supported IFC versions

| Version | Notes |
|---|---|
| IFC 4.3 ADD2 | Full support |
| IFC 4 (all addenda) | Full support |
| IFC 2x3 | Geo-referencing via Pset_SiteCommon |

## Project layout

```
app.py              Flask routes, IFC parsing, CORENET X checks
georeference_ifc/   Geo-reference helpers
static/             CSS, fonts, images, favicon
templates/          Jinja templates
uploads/            Runtime upload cache (gitignored)
requirements.txt    Pinned Python dependencies
render.yaml         Render.com deploy config
Procfile            Heroku-style web process declaration
```

## Privacy

This is **not** a local-only tool — uploaded IFCs are sent to and processed on the hosting server. Each new upload purges the previous file from the local cache, but earlier copies may persist in server logs or backups depending on how the host operates. **Don't upload confidential or NDA-restricted models** to a public-internet deployment. The 50 MB cap and the warnings on every page exist to nudge users toward site/coordination models, not federated full-discipline BIMs.

## License

[MIT](LICENSE). Includes geo-referencing helpers derived from [tudelft3d/ifcgref](https://github.com/tudelft3d/ifcgref) (MIT), see [LICENSE](LICENSE) for attribution.
