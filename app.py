"""Flask web app: upload an AxisCare CSV → download a ZIP of filled IRIS invoice PDFs.

HIPAA posture (code-level):
- All processing is in-memory; uploaded CSV bytes and generated PDFs are never written
  to disk by this app.
- No third-party network calls, no analytics, no external CDNs.
- Optional shared-password gate (HTTP Basic) via APP_PASSWORD (and optional APP_USER).
- Response carries `Cache-Control: no-store` so PHI is not cached by intermediaries.
- Access logs (Werkzeug) are configured to omit query strings and user-controlled detail.
- Generic download filename — never echoes uploaded filename back (it may contain PHI).
- Errors never include CSV contents.

Operational pieces (TLS termination, BAA with hosting provider, access control on the
host, log forwarding, backup hygiene) are deployment-time concerns — see README.
"""

from __future__ import annotations

import hmac
import io
import logging
import os
import secrets
import zipfile
from pathlib import Path

from flask import Flask, Response, abort, render_template, request, send_file

from invoice_generator import generate_invoices

BASE_DIR = Path(__file__).parent
TEMPLATE_PDF = BASE_DIR / "template.pdf"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def _quiet_access_logs() -> None:
    """Strip request-line detail from Werkzeug access logs.

    Werkzeug logs the request line (method + path) by default. Path and query
    are not expected to carry PHI in this app, but we still suppress access
    logging entirely to minimize the risk of any PHI ever entering log files.
    """
    logging.getLogger("werkzeug").setLevel(logging.ERROR)


def create_app() -> Flask:
    _quiet_access_logs()

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

    expected_password = os.environ.get("APP_PASSWORD")
    expected_user = os.environ.get("APP_USER", "user")
    auth_enabled = bool(expected_password)

    def _check_auth() -> bool:
        if not auth_enabled:
            return True
        auth = request.authorization
        if auth is None or auth.username is None or auth.password is None:
            return False
        user_ok = hmac.compare_digest(auth.username, expected_user)
        pw_ok = hmac.compare_digest(auth.password, expected_password)
        return user_ok and pw_ok

    @app.before_request
    def _require_auth():
        if _check_auth():
            return None
        return Response(
            "Authentication required.",
            status=401,
            headers={"WWW-Authenticate": 'Basic realm="IRIS Invoice Tool"'},
        )

    @app.after_request
    def _security_headers(response: Response) -> Response:
        # No-store everywhere — responses may contain PHI. Override anything
        # Flask's send_file may have set (defaults to "no-cache" which is weaker).
        response.headers["Cache-Control"] = "no-store"
        response.headers.setdefault("Pragma", "no-cache")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'self'",
        )
        # HSTS is safe to send; it is only honored over HTTPS.
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
        )
        return response

    @app.get("/healthz")
    def healthz():
        return Response("ok", mimetype="text/plain")

    @app.get("/")
    def index():
        return render_template("index.html", auth_required=auth_enabled)

    @app.post("/generate")
    def generate():
        upload = request.files.get("csv")
        if upload is None or not upload.filename:
            abort(400, "Please choose a CSV file.")

        raw = upload.read()
        # Drop the FileStorage reference; we never want this object to be reused.
        upload.close()

        try:
            csv_text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                csv_text = raw.decode("latin-1")
            except UnicodeDecodeError:
                abort(400, "CSV file is not valid text.")
        finally:
            # Best-effort clear of the raw bytes buffer.
            del raw

        try:
            invoices = generate_invoices(csv_text, TEMPLATE_PDF)
        except Exception:
            # Never echo CSV contents back.
            abort(400, "Could not process CSV. Check the file format and try again.")
        finally:
            del csv_text

        if not invoices:
            abort(400, "No billable rows found in the CSV.")

        # Generic, non-PHI download name (token disambiguates repeat downloads).
        token = secrets.token_hex(4)

        if len(invoices) == 1:
            only = invoices[0]
            return send_file(
                io.BytesIO(only.pdf_bytes),
                mimetype="application/pdf",
                as_attachment=True,
                download_name=f"iris_invoice_{token}.pdf",
            )

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for inv in invoices:
                zf.writestr(inv.filename, inv.pdf_bytes)
        buf.seek(0)

        return send_file(
            buf,
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"iris_invoices_{token}.zip",
        )

    return app


app = create_app()


if __name__ == "__main__":
    # `debug` is intentionally False — the Werkzeug debugger exposes arbitrary
    # code execution and must never run against PHI.
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    app.run(host=host, port=port, debug=False)
