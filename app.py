"""Streamlit app: upload an AxisCare CSV → download filled IRIS invoice PDFs.

HIPAA posture (code-level):
- All processing is in-memory; uploaded CSV bytes and generated PDFs are never
  written to disk by this app.
- No third-party network calls, no analytics, no external assets.
- Optional shared-password gate via APP_PASSWORD env var / Streamlit secret.
- Generic download filenames — never echoes uploaded filename back (it may
  contain PHI).
- Errors never include CSV contents.

Operational pieces (TLS termination, BAA with hosting provider, access control,
log forwarding) are deployment-time concerns. NOTE: Streamlit Community Cloud
does not currently sign BAAs and is not suitable for PHI. Host this on
infrastructure with which you have signed a BAA (a private Streamlit
deployment on AWS/Azure/GCP under a BAA, or self-hosted on hardened
infrastructure) — see README.
"""

from __future__ import annotations

import hmac
import io
import os
import secrets
import zipfile
from pathlib import Path

import streamlit as st

from invoice_generator import generate_invoices

BASE_DIR = Path(__file__).parent
TEMPLATE_PDF = BASE_DIR / "template.pdf"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def _expected_password() -> str | None:
    pw = os.environ.get("APP_PASSWORD")
    if pw:
        return pw
    try:
        return st.secrets.get("APP_PASSWORD")  # type: ignore[attr-defined]
    except (FileNotFoundError, KeyError, AttributeError):
        return None


def _check_password() -> bool:
    expected = _expected_password()
    if not expected:
        return True
    if st.session_state.get("authed"):
        return True

    st.title("AxisCare → IRIS Provider Invoice")
    st.write("Enter the access password to continue.")
    entered = st.text_input("Password", type="password", key="pw_input")
    if st.button("Sign in"):
        if hmac.compare_digest(entered or "", expected):
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    return False


def _render_app() -> None:
    st.set_page_config(
        page_title="AxisCare → IRIS Provider Invoice",
        page_icon=None,
        layout="centered",
    )

    st.title("AxisCare → IRIS Provider Invoice")
    st.write(
        "Upload an AxisCare **ClaimBatchCreation** CSV. You'll get back one IRIS "
        "Provider Invoice PDF per participant per service month."
    )

    with st.expander("Rules", expanded=False):
        st.markdown(
            "- One invoice per participant per calendar month.\n"
            "- Same-day visits for a participant are merged into a single line.\n"
            "- Up to 9 service dates per page; longer months overflow into "
            "`_pt2`, `_pt3`, …\n"
            "- Units are reported in 15-minute increments at the equivalent unit rate."
        )

    st.warning(
        "This tool handles Protected Health Information (PHI). Uploaded files "
        "are processed entirely in memory and are not written to disk or sent "
        "to any third party. Do not share generated PDFs outside of authorized "
        "billing workflows."
    )

    upload = st.file_uploader(
        "AxisCare CSV",
        type=["csv"],
        accept_multiple_files=False,
        help="Max 5 MB.",
    )

    if upload is None:
        return

    raw = upload.getvalue()
    if len(raw) > MAX_UPLOAD_BYTES:
        st.error("File is too large (max 5 MB).")
        return

    try:
        try:
            csv_text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            csv_text = raw.decode("latin-1")
    except UnicodeDecodeError:
        st.error("CSV file is not valid text.")
        return

    try:
        invoices = generate_invoices(csv_text, TEMPLATE_PDF)
    except Exception:
        st.error("Could not process CSV. Check the file format and try again.")
        return
    finally:
        del csv_text
        del raw

    if not invoices:
        st.error("No billable rows found in the CSV.")
        return

    token = secrets.token_hex(4)

    if len(invoices) == 1:
        only = invoices[0]
        st.success("Generated 1 invoice.")
        st.download_button(
            label="Download PDF",
            data=only.pdf_bytes,
            file_name=f"iris_invoice_{token}.pdf",
            mime="application/pdf",
        )
        return

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for inv in invoices:
            zf.writestr(inv.filename, inv.pdf_bytes)

    st.success(f"Generated {len(invoices)} invoices.")
    st.download_button(
        label="Download ZIP",
        data=buf.getvalue(),
        file_name=f"iris_invoices_{token}.zip",
        mime="application/zip",
    )

    with st.expander("Files in this ZIP"):
        for inv in invoices:
            st.text(inv.filename)


def main() -> None:
    if not _check_password():
        return
    _render_app()


if __name__ == "__main__":
    main()
else:
    # Streamlit imports the module rather than executing __main__; run on import.
    main()
