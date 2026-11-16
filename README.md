# Analytics Expert

A private web app that acts as your digital analytics and implementation consultant for **your own** GA4
properties and GTM containers. It runs on a server and you use it from any browser, so nothing needs to be
installed on your office laptop.

**Ask** (powered by Claude)
- Ask questions in plain English: "Why did conversions drop last week?", "Which landing pages underperform?"
- Get analysis with charts, period comparisons and recommendations.
- Ask for a written report (printable / save as PDF) or a dashboard of live GA4 widgets.
- Check GA4 property settings (retention, key events, custom definitions, enhanced measurement).

**GTM audits and fixes**
- Audit the live container: 20+ checks covering broken references, duplicate tags, invalid GA4 event and parameter
  names, missing measurement IDs, ecommerce data, Universal Analytics leftovers, risky custom HTML, consent,
  conversion linker, catch-all triggers, unused items, naming and folders. Each audit gets a health score.
- Automatic fixes for the mechanical problems, plus change sets the assistant drafts for anything else.
- Review a before/after diff, apply it to a **new** GTM workspace, check it in GTM Preview, then publish, with
  one-click rollback.
- Check a live page in a headless browser: which containers load, which GA4 events fire, what's in the dataLayer.
  GA4 hits from these checks are intercepted, so they never reach your reports.

## How changes stay safe

1. The assistant can only *propose* change sets. Applying and publishing are buttons in the UI, and the AI has
   no access to them.
2. GTM writes are off until you set `ALLOW_GTM_WRITES=true`.
3. Changes go into a fresh workspace, never into live or into your Default Workspace.
4. Before changing an item, the app re-reads it and stops if someone edited it after the audit.
5. It refuses to publish if there's an unpublished version in GTM, which would otherwise go live with it.
6. You have to type `PUBLISH` to publish. Every version is named and annotated, and rollback re-publishes the
   previous one.
7. If a step fails, the new workspace is deleted. Free GTM accounts allow only 3 workspaces.

## Setup

### 1. Claude API key
Create a key at console.anthropic.com and put it in `ANTHROPIC_API_KEY`.

### 2. Google access
In a Google Cloud project, enable **Google Analytics Data API**, **Google Analytics Admin API** and
**Tag Manager API**. Then choose one option:

- **Service account (recommended for a server).** Create a service account and download a JSON key. Add
  its email address as a user in GA4 (Property access management, **Viewer**) and in GTM (container user,
  **Publish** permission, or **Read** if you only want audits). Paste the key file's contents into `GOOGLE_SERVICE_ACCOUNT_JSON` (or set `GOOGLE_SERVICE_ACCOUNT_FILE` to its path).
- **Your own Google login.** Create an OAuth client of type *Desktop app*, download `client_secret.json`, then
  on a computer with a browser run `python scripts/google_login.py --client-secret client_secret.json`. Copy the
  resulting `data/google_token.json` to the server.

### 3. Run it
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium     # for live-site checks (optional)
cp .env.example .env                      # then fill it in
uvicorn app.main:app_factory --factory --port 8000
```
Open http://localhost:8000. Without `APP_PASSWORD`, the app only accepts connections from the same machine.

### Hosting on Railway (recommended, to use it from your office laptop)
Railway builds the app from this GitHub repo, keeps its data on a persistent volume, and gives you an
HTTPS address. Everything is done in the browser.

1. Sign up at railway.com with GitHub and choose the Hobby plan.
2. **New Project > Deploy from GitHub repo**, pick this repository (and this branch, until it's merged).
   Railway finds the `Dockerfile` and `railway.toml` by itself.
3. In the service, open **Variables** and add:
   - `ANTHROPIC_API_KEY` - your Claude API key
   - `APP_PASSWORD` - a long password for logging in
   - `GOOGLE_SERVICE_ACCOUNT_JSON` - open the service-account key file and paste its whole contents
   - `DATA_DIR` = `/data`
   - `ALLOW_GTM_WRITES` = `false` for now
4. Right-click the service > **Attach volume**, mount path `/data`. This keeps conversations, audits and
   change sets across deploys.
5. **Settings > Networking > Generate Domain**. Open that address from any browser and log in.

Pushing to the branch redeploys automatically. When you're ready for the app to apply and publish GTM
changes, set `ALLOW_GTM_WRITES` to `true`.

### Hosting anywhere else
Any machine that runs Docker works:
```bash
docker build -t analytics-expert .
docker run -d -p 8000:8000 --env-file .env -v $PWD/data:/data -e DATA_DIR=/data analytics-expert
```
Put HTTPS in front of it (Caddy or nginx, or a managed load balancer) and always set `APP_PASSWORD`. Data lives in
SQLite under `DATA_DIR`, so use a persistent disk. Serverless platforms such as Vercel wipe the disk and cap
request time, so they would need a hosted database and other changes.

## Limits worth knowing

- GA4 data comes from the Data API: thresholding, sampling and "(other)" rows apply and are flagged.
  Explorations and user-level data are not available. Those need the BigQuery export, which is currently off.
- GTM Preview / Tag Assistant can't be driven by API. Use the workspace link to preview manually; the live-site
  check covers what's published.
- Problems in the site's own code, such as a missing dataLayer push, can't be fixed from GTM. The assistant will
  tell you what the developers need to change.
- Fixes apply from the moment they're published. Data that was lost stays lost.
- The GTM API quota is low, so writes are spaced out (`GTM_WRITE_INTERVAL`) and a large change set takes a few minutes.

## Development
```bash
pip install pytest httpx
python -m pytest -q          # set CHROMIUM_PATH to run the browser test with an existing Chromium
```
Code map: `app/audit/` (rules, fixes, apply/publish), `app/agent/` (Claude loop, tools, prompt), `app/ga4.py`,
`app/gtm.py`, `app/verify.py` (headless site check), `app/main.py` (web server), `app/static/` (UI).
