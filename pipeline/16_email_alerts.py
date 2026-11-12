"""
16_email_alerts.py - email the management alerts
================================================

Sends the alerts to act on now (step 15, fact_alerts_official.csv, PRIORITY 1) as the report's alert
cards, with a link to the report for the rest. Every value on the cards comes from that file;
recipients, when to send and how are settings, not code:

    config.toml        [alerts_email]   company rules: enabled, recipients, send_when, subject
    config.local.toml  [email]          this machine: method, Microsoft 365 app details, test address

Body ([email] body):
    "cards"      built here from the alert rows (default)
    "paginated"  the paginated report [email] paginated_report, rendered by Report Server (its MHTML export,
                 the format Report Server uses for email), plus a PDF if [email] attach_pdf. Its table is
                 loaded by step 18. If Report Server cannot be reached, the cards are sent instead.

Methods ([email] method):
    "preview"  write the email to <reports_dir>/alert_email_preview.html and send nothing (default)
    "graph"    Microsoft 365 through Microsoft Graph (production). Needs an app registration with the
               Mail.Send application permission. The client secret is never in a file: it is read from
               Windows Credential Manager (keyring, service "aman-alerts-graph", user = client_id) or
               the AMAN_GRAPH_SECRET environment variable.
    "outlook"  the Outlook desktop app of the logged-in user (prototype on a laptop only)

Safety:
    - run_all.py runs this step only after the reconcile gate (step 13) has passed;
    - the same alerts are never sent twice: the last sent data cut-off, target and content are kept in
      <reports_dir>/alert_email_state.json (--force sends again);
    - --to-me sends only to [email] test_to, whatever the recipient list says;
    - --failure "<message>" sends a short failure notice to [email] admin_to instead of alerts.

Usage:
    python pipeline/16_email_alerts.py                 # as configured
    python pipeline/16_email_alerts.py --preview       # write the HTML file only
    python pipeline/16_email_alerts.py --to-me         # test send to [email] test_to
"""
import argparse
import base64
import hashlib
import html
import importlib
import json
import os
import sys
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path

import pandas as pd

from settings import S

STATE_FILE = "alert_email_state.json"
PREVIEW_FILE = "alert_email_preview.html"
KEYRING_SERVICE = "aman-alerts-graph"

# The report's alert panel (powerbi/deneb/management_alerts.vega.json) and theme (themes/DSS_theme.json)
FONT = "'Segoe UI',Helvetica,Arial,sans-serif"
SEMI = "'Segoe UI Semibold','Segoe UI',Helvetica,Arial,sans-serif"
ACCENT = {"Critical": ("#DB0032", "#DB0032"), "Warning": ("#F2A900", "#9A6A00")}   # bar, text


# ---------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------

def settings() -> tuple[dict, dict]:
    rules = {"enabled": False, "to": [], "cc": [], "send_when": "if_any",
             "subject": "Management alerts: {act_now} to act on now · data to {cutoff}"}
    rules.update(S.config.get("alerts_email", {}))
    local = {"method": "preview", "report_url": ""}
    local.update(S.config.get("email", {}))
    if rules["send_when"] not in ("always", "if_any", "if_critical"):
        sys.exit(f"[alerts_email] send_when must be always, if_any or if_critical, not '{rules['send_when']}'")
    return rules, local


# ---------------------------------------------------------------------
# The email: the report's alert cards, for the alerts to act on now (PRIORITY 1)
# ---------------------------------------------------------------------

def _e(text) -> str:
    return html.escape(str(text))


def _b(text) -> str:
    """A key number, in bold."""
    return f'<b style="font-family:{SEMI};font-weight:600;">{_e(text)}</b>'


def card_rows(r) -> list[tuple[str, str]]:
    """The card's facts, one per line, worded with the report's terms (Actual so far = In-Year,
    Estimated = Today, Expected = next year with no price change). HTML: values already escaped."""
    f = importlib.import_module("15_official_alerts")
    year = int(r.CURRENT_YEAR)
    actual = f"{_b(f.pct(r.IN_YEAR_CR))} combined ratio on the SAR {_e(f.sar(r.EP_L12))} earned in {year}"
    if not pd.isna(r.TREND_PTS):
        prior = f.pct(r.IN_YEAR_CR - r.TREND_PTS / 100)
        word = "the same as" if f._round(r.TREND_PTS) == 0 else ("up from" if r.TREND_PTS > 0 else "down from")
        actual += f", {word} {_b(prior)} in {year - 1}" if word != "the same as" else f", the same as {year - 1}"
    target = f.pct(r.TARGET_CR)
    gap = (f"{_b('SAR ' + f.sar(r.SAR_ABOVE_TARGET))} above the {target} target" if r.CURRENT_FLAG
           else f"within the {target} target")
    estimated = f"{_b(f.pct(r.TODAY_CR))} on the SAR {_e(f.sar(r.TODAY_PREMIUM))} premium on {year} policies: {gap}"
    expected = f"{_b(f.pct(r.EXPECTED_CR))} in {year + 1} with no price change"
    reliability = f"{_e(r.CONFIDENCE)} confidence, based on {_e(f.count(r.CLAIM_COUNT_L12))} claims in {year}"
    # The report's Action without its drill-through hint, which works only inside the report
    action = _e(r.ACTION.replace(" Drill through to Optimal Price for the effect.", ""))
    if pd.notna(r.REC_PRICE_CHANGE):
        price = f.signed_pct(r.REC_PRICE_CHANGE)
        action = action.replace(_e(price), _b(price), 1)
    return [("Actual so far", actual), ("Estimated", estimated), ("Expected", expected),
            ("Reliability", reliability), ("Action", action)]


def card(no: int, r) -> str:
    bar, txt = ACCENT.get(r.SEVERITY, ("#8C8C8C", "#5C5C5C"))
    critical = r.SEVERITY == "Critical"
    icon_bg, icon_fg = (bar, "#FFFFFF") if critical else ("#FFFFFF", txt)
    rows = "".join(f"""
        <tr>
          <td valign="top" width="96" style="width:96px;padding:3px 10px 3px 0;font:12px/19px {FONT};color:#737373;white-space:nowrap;">{label}</td>
          <td valign="top" style="padding:3px 0;font:13px/19px {FONT};color:#1A1A1A;">{value}</td>
        </tr>""" for label, value in card_rows(r))
    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:separate;background:#FFFFFF;border:1px solid #E0E0E0;border-radius:8px;margin:0 0 12px 0;">
  <tr>
    <td width="4" style="width:4px;background:{bar};border-radius:8px 0 0 8px;font-size:0;line-height:0;">&nbsp;</td>
    <td style="padding:12px 18px 12px 14px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
        <td style="font:12px {SEMI};color:{txt};white-space:nowrap;">
          <span style="display:inline-block;width:13px;height:13px;line-height:13px;border-radius:7px;border:1.5px solid {bar};background:{icon_bg};color:{icon_fg};text-align:center;font:9px {SEMI};vertical-align:1px;">!</span>&nbsp;
          {_e(r.SEVERITY.upper())}
        </td>
        <td align="right" style="font:12px {FONT};color:#737373;">{no:02d}</td>
      </tr></table>
      <div style="margin:6px 0 2px 0;font:13.5px {SEMI};color:#1A1A1A;">{_e(r.SEGMENT_LABEL)}</div>
      <div style="margin:0 0 8px 0;font:13px {SEMI};color:{txt};">{_e(r.HEADLINE)}</div>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}
      </table>
    </td>
  </tr>
</table>"""


def other_alerts(alerts: pd.DataFrame) -> list[str]:
    """One sentence per other priority group (step 15 PRIORITY 2-4), values from the data."""
    sar = importlib.import_module("15_official_alerts").sar
    lines = []
    for p, (what, todo) in {
        2: ("look underpriced, but the data behind them is limited", "Review their prices before renewal."),
        3: ("have high claims while the price looks adequate", "Review the large claims before changing any rate."),
    }.items():
        part = alerts[alerts.PRIORITY == p]
        if len(part):
            top = part.iloc[0]   # the file is sorted by SAR above target
            lines.append(f"{len(part)} segment{'s' if len(part) != 1 else ''} {what}. {todo} The largest is "
                         f"{top.SEGMENT_LABEL}, SAR {sar(top.SAR_ABOVE_TARGET)} above target.")
    n4 = int((alerts.PRIORITY == 4).sum())
    if n4:
        lines.append(f"{n4} segment{'s have' if n4 != 1 else ' has'} too few claims to judge yet "
                     f"and {'are' if n4 != 1 else 'is'} kept under watch.")
    return lines


def build_email(alerts: pd.DataFrame, rules: dict, report_url: str = "") -> tuple[str, str]:
    """Subject and HTML body. The cards are the report's own text (step 15 = the report's DAX)."""
    act = alerts[alerts.PRIORITY == 1]
    n_act, n_all = len(act), len(alerts)
    if n_all:
        cutoff = pd.Timestamp(alerts.DATA_CUTOFF.iloc[0])
        cutoff_txt = f"{cutoff.day} {cutoff.strftime('%b %Y')}"
        period = alerts.PERIOD.iloc[0]
    else:
        cutoff_txt, period = "-", ""
    subject = rules["subject"].format(act_now=n_act, open=n_all, cutoff=cutoff_txt,
                                      critical=int((act.SEVERITY == "Critical").sum()),
                                      warning=int((act.SEVERITY == "Warning").sum()))

    summary = f"{n_act} to act on now · {n_all} open in the report"
    cards = "".join(card(i + 1, r) for i, (_, r) in enumerate(act.iterrows())) or (
        f'<p style="font:13px {FONT};color:#5C5C5C;text-align:center;">No segment needs action now.</p>')
    link = (f'<a href="{_e(report_url)}" style="color:#1A1A1A;font-family:{SEMI};">Open the report</a>'
            if report_url else "Open the report")
    summary_lines = other_alerts(alerts)
    others = "".join(f'<p style="margin:0 0 8px 0;">{_e(s)}</p>' for s in summary_lines)
    if summary_lines:
        others = (f'<div style="font:12px {SEMI};color:#1A1A1A;margin:0 0 6px 0;">Also open in the report</div>'
                  + others)
    more = f"{link} for every alert and the details."

    body = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#FFFFFF;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#FFFFFF;">
<tr><td align="center" style="padding:16px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;">
  <tr><td align="center" style="padding:0 0 14px 0;">
    <div style="font:16px {SEMI};color:#1A1A1A;">Management alerts</div>
    <div style="margin:4px 0 0 0;font:11px {FONT};color:#5C5C5C;">{_e(summary)}</div>
    <div style="margin:2px 0 0 0;font:10.5px/14px {FONT};color:#8C8C8C;">{_e(period)}</div>
  </td></tr>
  <tr><td>{cards}</td></tr>
  <tr><td style="padding:6px 4px 0 4px;font:12px/18px {FONT};color:#3A3A3A;">{others}</td></tr>
  <tr><td align="center" style="padding:6px 0 0 0;font:12px/18px {FONT};color:#5C5C5C;">{more}</td></tr>
</table>
</td></tr>
</table>
</body></html>"""
    return subject, body


def counts(alerts: pd.DataFrame) -> dict:
    act = alerts[alerts.PRIORITY == 1]
    crit = int((act.SEVERITY == "Critical").sum())
    return {"open": len(act), "critical": crit, "warning": len(act) - crit}


# ---------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------

def graph_secret(client_id: str) -> str:
    secret = os.environ.get("AMAN_GRAPH_SECRET")
    if not secret:
        try:
            import keyring
            secret = keyring.get_password(KEYRING_SERVICE, client_id)
        except ImportError:
            pass
    if not secret:
        sys.exit(f"No Microsoft Graph client secret. Store it once with:\n"
                 f"  python -c \"import keyring; keyring.set_password('{KEYRING_SERVICE}', '{client_id}', input('secret: '))\"")
    return secret


def render_paginated(local: dict) -> tuple[str, bytes | None]:
    """The paginated report rendered by Report Server: HTML for the email body (from its MHTML export,
    the format Report Server itself uses for email) and, if [email] attach_pdf, a PDF. Signs in as the
    Windows account that runs the pipeline."""
    import email
    import requests
    from requests_negotiate_sspi import HttpNegotiateAuth
    for k in ("report_server_url", "paginated_report"):
        if not local.get(k):
            raise RuntimeError(f"[email] {k} is missing in config.local.toml (body = paginated)")
    params = {"ReportUrl": local.get("report_url") or None}
    base = f"{local['report_server_url']}?{local['paginated_report']}"

    def get(fmt: str) -> bytes:
        r = requests.get(base, params={**params, "rs:Format": fmt}, auth=HttpNegotiateAuth(), timeout=120)
        if r.status_code != 200:
            raise RuntimeError(f"Report Server returned {r.status_code} for {fmt}: {r.text[:300]}")
        return r.content

    mhtml = email.message_from_bytes(get("MHTML"))
    part = next((p for p in mhtml.walk() if p.get_content_type() == "text/html"), None)
    if part is None:
        raise RuntimeError("Report Server's MHTML export has no HTML part")
    body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8")
    pdf = get("PDF") if local.get("attach_pdf") else None
    return body, pdf


def send_graph(local: dict, to: list, cc: list, subject: str, body: str, attachments: list) -> None:
    for k in ("tenant_id", "client_id", "sender"):
        if not local.get(k):
            sys.exit(f"[email] {k} is missing in config.local.toml (method = graph)")
    import msal
    app = msal.ConfidentialClientApplication(
        local["client_id"], authority=f"https://login.microsoftonline.com/{local['tenant_id']}",
        client_credential=graph_secret(local["client_id"]))
    token = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in token:
        sys.exit(f"Microsoft 365 sign-in failed: {token.get('error')}: {token.get('error_description')}")
    message = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": body},
            "toRecipients": [{"emailAddress": {"address": a}} for a in to],
            "ccRecipients": [{"emailAddress": {"address": a}} for a in cc],
            "attachments": [{"@odata.type": "#microsoft.graph.fileAttachment", "name": name,
                             "contentType": "application/pdf", "contentBytes": base64.b64encode(data).decode()}
                            for name, data in attachments],
        },
        "saveToSentItems": True,
    }
    req = urllib.request.Request(
        f"https://graph.microsoft.com/v1.0/users/{local['sender']}/sendMail",
        data=json.dumps(message).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {token['access_token']}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            if resp.status != 202:
                sys.exit(f"Microsoft Graph returned {resp.status}")
    except urllib.error.HTTPError as e:
        sys.exit(f"Microsoft Graph refused the email ({e.code}): {e.read().decode('utf-8', 'replace')[:500]}")


def send_outlook(to: list, cc: list, subject: str, body: str, attachments: list) -> None:
    try:
        import win32com.client
    except ImportError:
        sys.exit("method = outlook needs pywin32 (pip install pywin32) and the Outlook desktop app")
    mail = win32com.client.Dispatch("Outlook.Application").CreateItem(0)
    mail.To = "; ".join(to)
    mail.CC = "; ".join(cc)
    mail.Subject = subject
    mail.HTMLBody = body
    with tempfile.TemporaryDirectory() as tmp:
        for name, data in attachments:
            path = Path(tmp) / name
            path.write_bytes(data)
            mail.Attachments.Add(str(path))
        mail.Send()


def send(local: dict, to: list, cc: list, subject: str, body: str, attachments: list | None = None) -> None:
    method, attachments = local["method"], attachments or []
    if method == "graph":
        send_graph(local, to, cc, subject, body, attachments)
    elif method == "outlook":
        send_outlook(to, cc, subject, body, attachments)
    else:
        sys.exit(f"[email] method must be preview, graph or outlook, not '{method}'")


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def content_key(alerts: pd.DataFrame) -> str:
    text = alerts[["SEVERITY", "SEGMENT_LABEL", "ISSUE", "ACTION"]].to_csv(index=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main():
    parser = argparse.ArgumentParser(description="Email the management alerts")
    parser.add_argument("--preview", action="store_true", help="write the HTML file only, send nothing")
    parser.add_argument("--to-me", action="store_true", help="send only to [email] test_to")
    parser.add_argument("--force", action="store_true", help="send even if these alerts were already sent")
    parser.add_argument("--failure", metavar="MESSAGE", help="send a pipeline failure notice to [email] admin_to")
    args = parser.parse_args()

    rules, local = settings()
    reports = Path(S.reports_dir)
    reports.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("Email the management alerts")
    print("=" * 60)

    if args.failure:
        admin = local.get("admin_to", [])
        if local["method"] == "preview" or not admin:
            print(f"  failure notice not sent (method {local['method']}, admin_to {admin or 'empty'})")
            return
        text = html.escape(args.failure)
        send(local, admin, [], "Aman pipeline run failed",
             f"<p style='font-family:Segoe UI,Arial,sans-serif'>The scheduled pipeline run failed: {text}<br>"
             f"No alerts email was sent. See data/run_log.csv on the server.</p>")
        print(f"  failure notice sent to {', '.join(admin)}")
        return

    alerts = pd.read_csv(S.facts_dir / "fact_alerts_official.csv")
    c = counts(alerts)
    subject, body = build_email(alerts, rules, local.get("report_url", ""))
    attachments = []
    if local.get("body", "cards") == "paginated":
        try:
            body, pdf = render_paginated(local)
            if pdf:
                attachments.append((f"{local['paginated_report'].strip('/').split('/')[-1]}.pdf", pdf))
            print(f"  body: the paginated report {local['paginated_report']}" + (" (+ PDF)" if pdf else ""))
        except Exception as e:   # never lose an alert because Report Server is down: send the cards instead
            print(f"  WARNING paginated report not available ({e}); sending the built-in cards instead")
    preview = reports / PREVIEW_FILE
    preview.write_text(body, encoding="utf-8")
    print(f"  {c['open']} to act on now ({c['critical']} critical, {c['warning']} warning) of {len(alerts)} alerts;"
          f" preview: {preview}")

    if args.preview or local["method"] == "preview":
        print("  preview only: nothing sent")
        return
    if not rules["enabled"] and not args.to_me:
        print("  [alerts_email] enabled = false: nothing sent")
        return

    if rules["send_when"] == "if_critical" and c["critical"] == 0 and not args.to_me:
        print("  no critical alert to act on now: nothing sent (send_when = if_critical)")
        return
    if rules["send_when"] == "if_any" and c["open"] == 0 and not args.to_me:
        print("  nothing to act on now: nothing sent (send_when = if_any)")
        return

    if args.to_me:
        to, cc = ([local["test_to"]] if isinstance(local.get("test_to"), str) else local.get("test_to", [])), []
        if not to:
            sys.exit("--to-me needs [email] test_to in config.local.toml")
    else:
        to, cc = rules["to"], rules["cc"]
        if not to:
            sys.exit("[alerts_email] to is empty: add the recipients in config.toml")

    state_path = reports / STATE_FILE
    key = {"data_cutoff": str(alerts.DATA_CUTOFF.iloc[0]) if len(alerts) else "",
           "target": float(alerts.TARGET_CR.iloc[0]) if len(alerts) else None, "content": content_key(alerts)}
    if not args.to_me and not args.force and state_path.exists():
        last = json.loads(state_path.read_text(encoding="utf-8"))
        if {k: last.get(k) for k in key} == key:
            print(f"  these alerts were already sent on {last.get('sent_at')}: nothing sent (--force to resend)")
            return

    send(local, to, cc, subject, body, attachments)
    print(f"  sent '{subject}' to {', '.join(to)}" + (f" (cc {', '.join(cc)})" if cc else ""))
    if not args.to_me:
        state_path.write_text(json.dumps({**key, "sent_at": datetime.now().isoformat(timespec="seconds"),
                                          "to": to, "cc": cc}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
