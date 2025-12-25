
---

## `app.py` (FULL)
```python
import json
import io
import re
import zipfile
from datetime import datetime, date, timedelta

import pandas as pd
import streamlit as st


# ----------------------------
# Helpers
# ----------------------------
def load_seed(path="data/demo_seed.json"):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def days_until(d: date) -> int:
    return (d - date.today()).days


def status_from_expiry(expiry: date, expiring_window_days: int = 30) -> str:
    du = days_until(expiry)
    if du < 0:
        return "EXPIRED"
    if du <= expiring_window_days:
        return "EXPIRING"
    return "VALID"


def badge(status: str) -> str:
    if status == "VALID":
        return "✅ VALID"
    if status == "EXPIRING":
        return "🟠 EXPIRING"
    if status == "EXPIRED":
        return "🔴 EXPIRED"
    if status == "MISSING":
        return "⚫ MISSING"
    return status


def ensure_state(seed):
    if "data" not in st.session_state:
        st.session_state.data = seed

    if "approval_log" not in st.session_state:
        st.session_state.approval_log = []

    if "reminder_log" not in st.session_state:
        st.session_state.reminder_log = []

    # Core intake queues
    if "inbox_submissions" not in st.session_state:
        st.session_state.inbox_submissions = []  # supplier submitted, pending compliance approval


def certificates_df(expiring_window_days: int):
    certs = st.session_state.data["certificates"]
    rows = []
    for c in certs:
        expiry = parse_date(c["expiry_date"])
        status = status_from_expiry(expiry, expiring_window_days)
        rows.append({
            "Certificate ID": c["id"],
            "Supplier": c["supplier"],
            "Material": c["material"],
            "Cert Body": c["cert_body"],
            "Country": c["country"],
            "Issue Date": c["issue_date"],
            "Expiry Date": c["expiry_date"],
            "Days Until Expiry": days_until(expiry),
            "Status": status,
            "File": c["file_name"]
        })
    df = pd.DataFrame(rows)
    df = df.sort_values(by=["Days Until Expiry"], ascending=True, kind="stable")
    return df


def supplier_status_df(df_certs: pd.DataFrame):
    # Supplier status = worst status among their certs
    order = {"EXPIRED": 3, "EXPIRING": 2, "VALID": 1}
    inv = {v: k for k, v in order.items()}

    sup = []
    for s in st.session_state.data["suppliers"]:
        name = s["name"]
        sub = df_certs[df_certs["Supplier"] == name]

        if len(sub) == 0:
            status = "MISSING"
            days_min = None
        else:
            worst = sub["Status"].map(order).max()
            status = inv[int(worst)]
            days_min = sub["Days Until Expiry"].min()

        sup.append({
            "Supplier": name,
            "Category": s["category"],
            "Country": s["country"],
            "Compliance Status": status,
            "Nearest Expiry (days)": days_min
        })
    return pd.DataFrame(sup)


def make_audit_pack_zip(selected_rows: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "README_AUDIT_PACK.txt",
            "Demo Audit Pack\n\nThis is a demo export. In production, this would include the actual PDF certificates and evidence logs.\n"
        )
        for _, r in selected_rows.iterrows():
            fname = f"{r['Certificate ID']}__{r['Supplier'].replace(' ', '_')}__{r['Material'].replace(' ', '_')}.txt"
            contents = (
                f"Certificate ID: {r['Certificate ID']}\n"
                f"Supplier: {r['Supplier']}\n"
                f"Material: {r['Material']}\n"
                f"Cert Body: {r['Cert Body']}\n"
                f"Country: {r['Country']}\n"
                f"Issue Date: {r['Issue Date']}\n"
                f"Expiry Date: {r['Expiry Date']}\n"
                f"Status: {r['Status']}\n"
                f"File: {r['File']}\n"
            )
            z.writestr(fname, contents)
    buf.seek(0)
    return buf.read()


def add_approval(action: str, supplier: str, material: str, certificate_id: str, note: str = ""):
    st.session_state.approval_log.insert(0, {
        "Time": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "Action": action,
        "Supplier": supplier,
        "Material": material,
        "Certificate ID": certificate_id,
        "Note": note
    })


def add_reminder(supplier: str, reason: str, channel: str):
    st.session_state.reminder_log.insert(0, {
        "Time": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "Supplier": supplier,
        "Reason": reason,
        "Channel": channel
    })


# ----------------------------
# "OCR-like" intake simulation
# ----------------------------
def guess_from_filename(filename: str, supplier_names: list[str]) -> dict:
    """
    Simulate OCR extraction:
    - try to guess supplier by substring match
    - guess country by common tokens (KSA, UAE, Qatar...)
    - guess expiry date if pattern like 2026-01-31 or 31-01-2026 exists
    - guess cert body by token "Authority" or "Halal"
    """
    lower = filename.lower()

    # Supplier guess
    supplier_guess = ""
    for s in supplier_names:
        if s.lower().replace(" ", "") in lower.replace(" ", ""):
            supplier_guess = s
            break
    if not supplier_guess and supplier_names:
        supplier_guess = supplier_names[0]

    # Country guess
    country_map = {
        "uae": "UAE", "dubai": "UAE", "abu": "UAE",
        "ksa": "KSA", "saudi": "KSA", "riyadh": "KSA",
        "qatar": "Qatar", "doha": "Qatar",
        "oman": "Oman", "kuwait": "Kuwait", "bahrain": "Bahrain"
    }
    country_guess = "UAE"
    for k, v in country_map.items():
        if k in lower:
            country_guess = v
            break

    # Expiry date guess (very basic patterns)
    expiry_guess = date.today() + timedelta(days=365)
    # pattern 2026-01-31
    m1 = re.search(r"(20\d{2})[-_/](0[1-9]|1[0-2])[-_/](0[1-9]|[12]\d|3[01])", filename)
    if m1:
        y, mo, d = int(m1.group(1)), int(m1.group(2)), int(m1.group(3))
        try:
            expiry_guess = date(y, mo, d)
        except ValueError:
            pass
    # pattern 31-01-2026
    m2 = re.search(r"(0[1-9]|[12]\d|3[01])[-_/](0[1-9]|1[0-2])[-_/](20\d{2})", filename)
    if m2:
        d, mo, y = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
        try:
            expiry_guess = date(y, mo, d)
        except ValueError:
            pass

    # Material guess (use filename chunks)
    base = re.sub(r"\.[a-zA-Z0-9]+$", "", filename).replace("_", " ").replace("-", " ")
    tokens = [t for t in base.split() if len(t) > 2]
    material_guess = " ".join(tokens[:4]) if tokens else "Halal Certificate"

    # Cert body guess (placeholder)
    cert_body_guess = "Halal Authority (extracted)"

    # Cert number guess (fake-ish but consistent)
    cert_no_guess = f"HA-{date.today().year}-{abs(hash(filename)) % 10000:04d}"

    return {
        "Supplier": supplier_guess,
        "Country": country_guess,
        "Material": material_guess[:60],
        "Cert Body": cert_body_guess,
        "Certificate No": cert_no_guess,
        "Expiry Date": expiry_guess
    }


def roi_calc_ui():
    st.subheader("ROI Calculator (Simple)")
    st.caption("A conservative estimator to explain value in business terms: time + incidents avoided.")

    c1, c2, c3 = st.columns(3)

    with c1:
        suppliers = st.number_input("Number of suppliers", min_value=5, max_value=2000, value=60, step=5)
        certs = st.number_input("Number of halal certificates", min_value=10, max_value=20000, value=180, step=10)
        hourly_cost = st.number_input("Internal cost per hour (USD)", min_value=5, max_value=250, value=25, step=5)

    with c2:
        chase_hours_week = st.slider("Hours/week chasing suppliers", 0, 60, 6)
        rework_hours_week = st.slider("Hours/week fixing errors / re-submissions", 0, 60, 3)
        audit_hours_month = st.slider("Hours/month preparing for audits", 0, 120, 12)

    with c3:
        delay_incidents_year = st.slider("Shipment / approval delays per year", 0, 24, 2)
        avg_delay_cost = st.number_input("Avg cost per delay incident (USD)", min_value=0, max_value=500000, value=8000, step=1000)
        compliance_incidents_year = st.slider("Compliance issues / near-misses per year", 0, 24, 1)
        avg_compliance_cost = st.number_input("Avg cost per compliance issue (USD)", min_value=0, max_value=500000, value=15000, step=1000)

    st.divider()
    st.markdown("### Assumptions (kept conservative)")
    st.write("- Basic reduces admin time modestly; Core reduces chasing and audit prep significantly.")

    basic_time_reduction = 0.20   # 20% reduction in admin time
    core_chase_reduction = 0.70   # 70% less chasing
    core_rework_reduction = 0.45  # 45% less rework
    core_audit_reduction = 0.50   # 50% less audit prep time
    core_incident_reduction = 0.20  # 20% fewer incidents

    annual_chase_cost = chase_hours_week * 52 * hourly_cost
    annual_rework_cost = rework_hours_week * 52 * hourly_cost
    annual_audit_cost = audit_hours_month * 12 * hourly_cost
    annual_time_cost = annual_chase_cost + annual_rework_cost + annual_audit_cost

    annual_incident_cost = (delay_incidents_year * avg_delay_cost) + (compliance_incidents_year * avg_compliance_cost)
    baseline_total = annual_time_cost + annual_incident_cost

    basic_total = (annual_time_cost * (1 - basic_time_reduction)) + annual_incident_cost

    core_time = (
        (annual_chase_cost * (1 - core_chase_reduction)) +
        (annual_rework_cost * (1 - core_rework_reduction)) +
        (annual_audit_cost * (1 - core_audit_reduction))
    )
    core_total = core_time + (annual_incident_cost * (1 - core_incident_reduction))

    # Placeholder pricing (edit anytime)
    basic_price = 1500
    core_price = 3000

    basic_savings = baseline_total - basic_total
    core_savings = baseline_total - core_total

    basic_net = basic_savings - basic_price
    core_net = core_savings - core_price

    k1, k2, k3 = st.columns(3)
    k1.metric("Estimated annual cost today", f"${baseline_total:,.0f}")
    k2.metric("Estimated savings (Basic)", f"${basic_savings:,.0f}", f"Net after fee: ${basic_net:,.0f}")
    k3.metric("Estimated savings (Core)", f"${core_savings:,.0f}", f"Net after fee: ${core_net:,.0f}")

    st.divider()
    st.markdown("### Payback (months)")
    pb1, pb2 = st.columns(2)
    with pb1:
        if basic_savings > 0:
            months = (basic_price / basic_savings) * 12
            st.write(f"**Basic payback:** ~{months:.1f} months")
        else:
            st.write("**Basic payback:** N/A")
    with pb2:
        if core_savings > 0:
            months = (core_price / core_savings) * 12
            st.write(f"**Core payback:** ~{months:.1f} months")
        else:
            st.write("**Core payback:** N/A")

    st.caption("Tip: In GCC meetings, set one realistic incident cost (delayed shipment / relabelling). ROI becomes obvious fast.")


# ----------------------------
# UI
# ----------------------------
st.set_page_config(page_title="Halal Compliance Demo (Basic vs Core)", layout="wide")

seed = load_seed()
ensure_state(seed)

st.title("Halal Compliance Demo")
st.caption("Demo: **Basic vs Core** for non-technical buyers (GCC-friendly).")

with st.sidebar:
    st.subheader("Plan Mode")
    plan = st.radio("Select plan to demo", ["Basic", "Core"], index=1, horizontal=True)

    st.divider()
    st.subheader("Alert Settings")
    expiring_window = st.slider("Expiring window (days)", min_value=7, max_value=90, value=30, step=1)

    st.divider()
    if st.button("Reset demo data"):
        for k in ["data", "approval_log", "reminder_log", "inbox_submissions"]:
            if k in st.session_state:
                del st.session_state[k]
        st.rerun()

# Build dataframes
df_c = certificates_df(expiring_window)
df_s = supplier_status_df(df_c)

# KPI row
k1, k2, k3, k4 = st.columns(4)
valid = (df_c["Status"] == "VALID").sum()
expiring = (df_c["Status"] == "EXPIRING").sum()
expired = (df_c["Status"] == "EXPIRED").sum()
total = len(df_c)

k1.metric("Certificates", int(total))
k2.metric("✅ Valid", int(valid))
k3.metric("🟠 Expiring", int(expiring))
k4.metric("🔴 Expired", int(expired))

st.divider()

left, right = st.columns([1.35, 1])

with left:
    st.subheader("Certificate Vault")
    st.write("All halal documents in one place, sorted by urgency.")

    df_disp = df_c.copy()
    df_disp["Status Badge"] = df_disp["Status"].apply(badge)
    show_cols = ["Certificate ID", "Supplier", "Material", "Country", "Expiry Date", "Days Until Expiry", "Status Badge", "File"]
    st.dataframe(df_disp[show_cols], use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Audit Pack Export (Basic & Core)")
    st.write("Select certificates and export an audit pack (demo ZIP).")

    selectable = df_disp[["Certificate ID", "Supplier", "Material", "Cert Body", "Country", "Issue Date", "Expiry Date", "Status", "File"]].copy()
    selectable.insert(0, "Select", False)

    edited = st.data_editor(
        selectable,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Select": st.column_config.CheckboxColumn(required=False),
        },
        disabled=[c for c in selectable.columns if c != "Select"],
        key="audit_selector"
    )

    chosen = edited[edited["Select"] == True]
    if len(chosen) == 0:
        st.info("Select 1+ certificates to enable the Audit Pack download.")
    else:
        zip_bytes = make_audit_pack_zip(chosen.drop(columns=["Select"]))
        st.download_button(
            "Download Audit Pack (ZIP)",
            data=zip_bytes,
            file_name="audit_pack_demo.zip",
            mime="application/zip"
        )

with right:
    st.subheader("Supplier Compliance View")
    st.write("A simple view: who is compliant vs at-risk.")

    df_s2 = df_s.copy()
    df_s2["Status Badge"] = df_s2["Compliance Status"].apply(badge)
    st.dataframe(
        df_s2[["Supplier", "Category", "Country", "Status Badge", "Nearest Expiry (days)"]],
        use_container_width=True,
        hide_index=True
    )

    st.divider()
    st.subheader("Alerts")
    urgent = df_disp[df_disp["Status"].isin(["EXPIRING", "EXPIRED"])][
        ["Supplier", "Material", "Expiry Date", "Days Until Expiry", "Status"]
    ]
    if len(urgent) == 0:
        st.success("No urgent issues right now.")
    else:
        for _, r in urgent.iterrows():
            msg = f"**{r['Supplier']}** — {r['Material']} | Expires **{r['Expiry Date']}** ({int(r['Days Until Expiry'])} days) — **{r['Status']}**"
            if r["Status"] == "EXPIRED":
                st.error(msg)
            else:
                st.warning(msg)

st.divider()

# ----------------------------
# Basic vs Core sections
# ----------------------------
if plan == "Basic":
    st.header("Basic Mode: Certificate Management & Alerts")
    st.write("Basic is intentionally simple: **store certificates + expiry alerts + audit downloads**.")
    st.info("Switch to **Core** to show supplier photo/PDF intake, approvals log, and automated reminders.")

else:
    st.header("Core Mode: Supplier Control & Audit Readiness")

    tab1, tab2, tab3 = st.tabs(["Supplier Intake (OCR-style)", "Approvals Log", "Reminder Centre"])

    with tab1:
        st.subheader("Supplier Intake (Photo/PDF → Pre-filled Form → Confirm → Approve)")
        st.write(
            "Suppliers do what they already do: **take a photo or upload a PDF**. "
            "The system **pre-fills** a form (simulated here) and they just confirm."
        )

        supplier_names = [s["name"] for s in st.session_state.data["suppliers"]]

        colA, colB = st.columns([1, 1])

        with colA:
            st.markdown("#### 1) Supplier uploads a photo / PDF")
            intake_file = st.file_uploader(
                "Upload certificate image or PDF",
                type=["png", "jpg", "jpeg", "pdf"],
                help="In real product, this could come via WhatsApp link or email forwarding too."
            )

            # Optional preview for images
            if intake_file and intake_file.type in ["image/png", "image/jpeg"]:
                st.image(intake_file, caption="Uploaded photo (demo preview)", use_container_width=True)

            st.markdown("#### 2) System pre-fills the fields (simulated)")
            if intake_file:
                guessed = guess_from_filename(intake_file.name, supplier_names)
            else:
                guessed = {
                    "Supplier": supplier_names[0] if supplier_names else "",
                    "Country": "UAE",
                    "Material": "Halal Certificate",
                    "Cert Body": "Halal Authority (extracted)",
                    "Certificate No": f"HA-{date.today().year}-0001",
                    "Expiry Date": date.today() + timedelta(days=365)
                }

            # Editable confirmation table (supplier confirms)
            form_df = pd.DataFrame([
                {"Field": "Supplier", "Value": guessed["Supplier"]},
                {"Field": "Country", "Value": guessed["Country"]},
                {"Field": "Material / Ingredient", "Value": guessed["Material"]},
                {"Field": "Certification Body", "Value": guessed["Cert Body"]},
                {"Field": "Certificate Number", "Value": guessed["Certificate No"]},
                {"Field": "Expiry Date (YYYY-MM-DD)", "Value": guessed["Expiry Date"].strftime("%Y-%m-%d")},
            ])

            st.caption("Supplier confirms (or edits) what was extracted.")
            edited_form = st.data_editor(
                form_df,
                use_container_width=True,
                hide_index=True,
                disabled=["Field"],
                key="ocr_form"
            )

            st.markdown("#### 3) Submit to compliance team")
            note = st.text_input("Supplier note (optional)", value="", placeholder="e.g., Renewal issued recently, replacing older cert.")
            if st.button("Submit for Approval"):
                # Convert edited table back to dict
                vals = {row["Field"]: str(row["Value"]) for _, row in edited_form.iterrows()}

                try:
                    expiry_parsed = parse_date(vals["Expiry Date (YYYY-MM-DD)"])
                except Exception:
                    st.error("Expiry Date must be in YYYY-MM-DD format.")
                    st.stop()

                submission = {
                    "Submitted": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
                    "Supplier": vals["Supplier"],
                    "Country": vals["Country"],
                    "Material": vals["Material / Ingredient"],
                    "Cert Body": vals["Certification Body"],
                    "Certificate No": vals["Certificate Number"],
                    "Expiry Date": expiry_parsed.strftime("%Y-%m-%d"),
                    "File": intake_file.name if intake_file else "(demo) supplier_certificate.pdf",
                    "State": "PENDING",
                    "Note": note
                }
                st.session_state.inbox_submissions.insert(0, submission)
                st.success("Submitted. Compliance team can now approve/reject on the right.")

        with colB:
            st.markdown("#### Compliance Inbox (Approve / Reject)")
            inbox = st.session_state.inbox_submissions

            if len(inbox) == 0:
                st.info("No pending submissions yet. Upload a certificate on the left to create one.")
            else:
                inbox_df = pd.DataFrame(inbox)
                st.dataframe(inbox_df, use_container_width=True, hide_index=True)

                st.caption("Approve / Reject the most recent submission (top row).")
                first = inbox[0]

                c1, c2 = st.columns(2)
                with c1:
                    if st.button("✅ Approve latest"):
                        new_id = f"CERT-{len(st.session_state.data['certificates'])+1:03d}"
                        st.session_state.data["certificates"].append({
                            "id": new_id,
                            "supplier": first["Supplier"],
                            "material": first["Material"],
                            "cert_body": first["Cert Body"],
                            "country": first["Country"],
                            "issue_date": date.today().strftime("%Y-%m-%d"),
                            "expiry_date": first["Expiry Date"],
                            "status": "VALID",
                            "file_name": first["File"]
                        })
                        add_approval(
                            "APPROVED",
                            first["Supplier"],
                            first["Material"],
                            new_id,
                            f"Approved from supplier intake. CertNo={first.get('Certificate No','')}"
                        )
                        st.session_state.inbox_submissions.pop(0)
                        st.success("Approved and added to Certificate Vault.")
                        st.rerun()

                with c2:
                    if st.button("❌ Reject latest"):
                        add_approval("REJECTED", first["Supplier"], first["Material"], "(pending)", "Rejected (demo)")
                        st.session_state.inbox_submissions.pop(0)
                        st.warning("Rejected. Supplier would be asked to re-submit (demo).")
                        st.rerun()

    with tab2:
        st.subheader("Approvals & Change Log")
        st.write("Core keeps a simple record of decisions for audit defensibility.")
        if len(st.session_state.approval_log) == 0:
            st.info("No approvals yet. Approve/reject a submission to generate log entries.")
        else:
            st.dataframe(pd.DataFrame(st.session_state.approval_log), use_container_width=True, hide_index=True)

    with tab3:
        st.subheader("Reminder Centre (Demo)")
        st.write("Core automatically chases suppliers for missing/expiring docs. Here it’s simulated.")

        urgent_suppliers = df_disp[df_disp["Status"].isin(["EXPIRING", "EXPIRED"])]["Supplier"].unique().tolist()
        if len(urgent_suppliers) == 0:
            st.success("No urgent suppliers to chase today.")
        else:
            st.info("Suppliers needing attention: " + ", ".join(urgent_suppliers))

        col1, col2, col3 = st.columns(3)
        with col1:
            chase_supplier = st.selectbox("Supplier to remind", options=[s["name"] for s in st.session_state.data["suppliers"]])
        with col2:
            reason = st.selectbox("Reason", ["Missing certificate", "Certificate expiring soon", "Certificate expired", "Audit evidence request"])
        with col3:
            channel = st.selectbox("Channel", ["Email", "WhatsApp", "SMS"], index=1)

        if st.button("Send Reminder (Demo)"):
            add_reminder(chase_supplier, reason, channel)
            st.success(f"Reminder logged: {chase_supplier} via {channel} — {reason}")

        st.divider()
        st.subheader("Reminder Log")
        if len(st.session_state.reminder_log) == 0:
            st.info("No reminders sent yet.")
        else:
            st.dataframe(pd.DataFrame(st.session_state.reminder_log), use_container_width=True, hide_index=True)

st.divider()

# ----------------------------
# Value section (ROI)
# ----------------------------
st.header("Value")
roi_calc_ui()
