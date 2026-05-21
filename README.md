# AxisCare → IRIS Provider Invoice Converter

Flask web app. Upload an AxisCare `ClaimBatchCreation` CSV; download a ZIP of
filled IRIS Provider Invoice PDFs.

## Behavior

- One invoice per **participant per calendar month** (grouped by `Visit Date`).
- Multiple CSV rows for the same participant on the same date are **merged** into
  a single line (billable hours and amounts summed).
- The template has **9 service lines per page**. If a (participant, month) group
  has more than 9 distinct service dates, the invoice is split into
  `..._pt1.pdf`, `..._pt2.pdf`, …; each part is one printable page.
- `Unit Type` is reported as `15 min`. Units are computed as
  `billable_hours × 4`, and `Unit Rate` is `csv_rate / 4` (e.g. $36/hr → $9.00).

## Run locally

    pip install -r requirements.txt
    python app.py
    # http://127.0.0.1:5000

## HIPAA compliance

This app processes PHI (participant names, member IDs, auth numbers, dates of
service). The code is structured to support a HIPAA-compliant deployment;
**compliance also requires operational controls that are not enforceable from
the code alone**.

### Code-level controls (in this repo)

- All processing is **in-memory only**. The uploaded CSV and generated PDFs are
  never written to disk by the app. There are no temp files.
- **No third-party requests** — no analytics, no external CDNs, no fonts loaded
  from the internet. The CSP forbids any cross-origin asset.
- **`Cache-Control: no-store`** on every response so PHI is not cached by
  browsers or intermediaries.
- **Secure headers**: HSTS, X-Content-Type-Options, X-Frame-Options,
  Referrer-Policy, strict CSP.
- **Optional HTTP Basic auth** via `APP_PASSWORD` (and optional `APP_USER`).
  Set it before running in any shared environment.
- **No PHI in logs**. Werkzeug access logs are silenced; errors never echo
  CSV contents back.
- **Generic download filenames** — the uploaded filename (which can itself be
  PHI) is never reflected back in the response.
- Werkzeug debugger is hard-disabled.

### Operational controls (your responsibility to configure)

These must be true in production to claim HIPAA compliance:

1. **Sign a BAA** with your hosting provider and any other vendor that touches
   the traffic (load balancer, WAF, log aggregator, backup service).
2. **TLS everywhere.** Terminate HTTPS in front of the app (reverse proxy, load
   balancer, or `gunicorn` behind nginx with a valid certificate). HSTS is set
   on responses but only takes effect over HTTPS.
3. **Authentication.** Set a strong `APP_PASSWORD` env var, or place the app
   behind your existing SSO/VPN. Do not run it open to the internet.
4. **Restrict access** to the host (security groups, VPN, IP allow-list) and
   to the file system (least-privilege OS user).
5. **Disable infrastructure logging of request bodies.** Confirm your reverse
   proxy / load balancer is not capturing POST payloads.
6. **Encrypt at rest.** The app does not write PHI to disk, but the host's
   disk should still be encrypted (filesystem-level or cloud volume).
7. **Audit logging.** Log authentication events (successful + failed) at the
   reverse proxy or auth layer. Retain per your policy.
8. **Patch management.** Keep `Flask`, `pypdf`, and the OS base image current.
9. **Workforce training & policies** — sanctions for misuse, breach
   notification process, designated Privacy/Security Officer.
10. **Run a production WSGI server** (e.g. `gunicorn`, `waitress`); the Flask
    dev server is not for production use.

Example production command (gunicorn behind a TLS-terminating proxy):

    APP_PASSWORD='<strong-random-secret>' \
    gunicorn --bind 127.0.0.1:8000 --workers 2 app:app

### What this app does NOT do

- It does not authenticate individual users (only a single shared password).
  For multi-user environments, front it with SSO.
- It does not write an audit trail of which user processed which CSV.
- It does not validate that the uploader is authorized for the participants
  listed in the CSV.

If you need any of the above, treat this app as a building block, not a
turnkey HIPAA solution.

## Files

- `app.py` — Flask app
- `invoice_generator.py` — CSV parsing, grouping, PDF fill (no Flask deps)
- `template.pdf` — IRIS Provider Invoice fillable PDF (pre-filled with provider info)
- `templates/index.html` — upload page
