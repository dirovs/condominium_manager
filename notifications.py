import os
import requests
import smtplib
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.utils import formatdate, make_msgid, parseaddr, formataddr
from email.header import Header
from datetime import datetime

def build_smtp_message(
    cfg: dict,
    to_email: str,
    to_name: str,
    subject: str,
    plain_text: str,
    html_text: str,
    attachment_path: str = None,
    attachment_name: str = None,
    x_mailer: str = None
) -> tuple[str, str, MIMEMultipart]:
    """
    Constructs an RFC 5322 / RFC 2046 compliant, anti-spam optimized email message.

    MIME Hierarchy:
      - With attachment:
          multipart/mixed
          ├── multipart/alternative
          │   ├── text/plain (utf-8)
          │   └── text/html (utf-8)
          └── application/pdf (attachment)
      - Without attachment:
          multipart/alternative
          ├── text/plain (utf-8)
          └── text/html (utf-8)

    Header Protections:
      - RFC 2047 UTF-8 encoded Subject, From, To, Reply-To, and Organization.
      - RFC 5322 Message-ID with sender domain.
      - Anti-spam delivery headers (X-Priority, Importance, Auto-Submitted, X-Auto-Response-Suppress).
    """
    condo_name = (cfg.get("condo_name") or "Condominio Casales San Pedro").strip()
    raw_from = (cfg.get("smtp_from") or cfg.get("smtp_user") or "").strip()

    from_name, from_addr = parseaddr(raw_from)
    if not from_addr:
        from_addr = (cfg.get("smtp_user") or "").strip()

    display_sender = condo_name or from_name or "Administración Condominio"
    from_header = formataddr((str(Header(display_sender, 'utf-8')), from_addr))

    _, clean_to_addr = parseaddr(to_email)
    if not clean_to_addr:
        clean_to_addr = to_email.strip()

    display_recipient = (to_name or "Condómino").strip()
    to_header = formataddr((str(Header(display_recipient, 'utf-8')), clean_to_addr))

    domain = from_addr.split("@")[-1] if "@" in from_addr else "gmail.com"
    domain = domain.strip().replace(">", "").replace("<", "")

    has_attachment = bool(attachment_path and os.path.exists(attachment_path))

    if has_attachment:
        msg = MIMEMultipart("mixed")
        alt_container = MIMEMultipart("alternative")
        alt_container.attach(MIMEText(plain_text, "plain", "utf-8"))
        alt_container.attach(MIMEText(html_text, "html", "utf-8"))
        msg.attach(alt_container)

        filename = attachment_name or os.path.basename(attachment_path)
        with open(attachment_path, "rb") as f:
            pdf_data = f.read()
        part = MIMEApplication(pdf_data, _subtype="pdf")
        part.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(part)
    else:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(plain_text, "plain", "utf-8"))
        msg.attach(MIMEText(html_text, "html", "utf-8"))

    msg["From"] = from_header
    msg["To"] = to_header
    msg["Subject"] = Header(subject, 'utf-8').encode()
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=domain)
    msg["Reply-To"] = from_header
    msg["Organization"] = str(Header(condo_name, 'utf-8'))
    msg["X-Mailer"] = x_mailer or f"{condo_name} Mailer / CondoManager"
    msg["Auto-Submitted"] = "auto-generated"
    msg["X-Auto-Response-Suppress"] = "All"
    msg["X-Priority"] = "3"
    msg["Importance"] = "normal"
    msg["MIME-Version"] = "1.0"

    return from_addr, clean_to_addr, msg

def send_smtp_email(cfg, from_email, to_email, msg_obj, timeout=12.0):
    """
    Robust SMTP sender supporting:
    - Automatic cleaning of Gmail app passwords (stripping spaces).
    - Multi-strategy port attempt: Port 465 (SSL) and Port 587 (STARTTLS).
    - Auto-fallback between SSL and STARTTLS so it never fails due to ISP port throttling.
    - Adequate timeout (12s) to prevent premature timeout during TLS handshakes and attachments.
    - Strict RFC 5321 envelope addresses extraction for MAIL FROM / RCPT TO.
    """
    host = (cfg.get("smtp_host") or "smtp.gmail.com").strip()
    user = (cfg.get("smtp_user") or "").strip()
    raw_password = (cfg.get("smtp_password") or "").strip()

    # Strip spaces in Google app passwords (e.g., 'scak obkm pxux gqfu' -> 'scakobkmpxuxgqfu')
    pwd_clean = raw_password.replace(" ", "") if " " in raw_password else raw_password

    # Extract clean envelope addresses
    _, envelope_from = parseaddr(from_email)
    if not envelope_from:
        envelope_from = user

    _, envelope_to = parseaddr(to_email)
    if not envelope_to:
        envelope_to = to_email.strip()

    configured_port = int(cfg.get("smtp_port") or (465 if "gmail" in host.lower() else 587))

    # Determine ordered list of connection attempts: (port, is_ssl)
    if configured_port == 465:
        attempts = [(465, True), (587, False)]
    elif configured_port == 587:
        if "gmail" in host.lower():
            # For Gmail, 465 SSL is much more reliable across ISP networks
            attempts = [(465, True), (587, False)]
        else:
            attempts = [(587, False), (465, True)]
    else:
        attempts = [(configured_port, configured_port == 465), (465, True), (587, False)]

    last_error = None
    for port, is_ssl in attempts:
        try:
            if is_ssl:
                server = smtplib.SMTP_SSL(host, port, timeout=timeout)
                server.ehlo()
            else:
                server = smtplib.SMTP(host, port, timeout=timeout)
                server.ehlo()
                try:
                    server.starttls()
                    server.ehlo()
                except Exception:
                    pass

            try:
                server.login(user, pwd_clean)
            except smtplib.SMTPAuthenticationError:
                if raw_password != pwd_clean:
                    server.login(user, raw_password)
                else:
                    raise

            server.sendmail(envelope_from, envelope_to, msg_obj.as_string())
            try:
                server.quit()
            except Exception:
                pass
            return True, f"Enviado exitosamente vía SMTP ({'SSL' if is_ssl else 'TLS'} puerto {port})."
        except Exception as e:
            last_error = e
            print(f"[Notifications] SMTP attempt on {host}:{port} ({'SSL' if is_ssl else 'TLS'}) failed: {e}")
            continue

    if last_error:
        raise last_error
    return False, "No se pudo conectar al servidor SMTP."

class NotificationManager:
    @staticmethod
    def _dispatch_receipt_notifications(sm, email_tasks, wa_tasks, cfg):
        """Worker function to deliver emails and WhatsApp messages with timeout without blocking UI."""
        # 1. Process Emails
        for task in email_tasks:
            from_email = task["from_email"]
            email = task["email"]
            msg = task["msg"]
            log_entry = task["log_entry"]

            try:
                ok, detail = send_smtp_email(cfg, from_email, email, msg, timeout=12.0)
                log_entry["status"] = "Enviado"
                log_entry["details"] = detail
                print(f"[Notifications] Email dispatch SUCCESS to {email}: {detail}")
            except Exception as e:
                log_entry["status"] = "Error"
                log_entry["details"] = f"Fallo SMTP: {str(e)}"
                print(f"[Notifications] Email dispatch error to {email}: {e}")

        # 2. Process WhatsApp
        for task in wa_tasks:
            wa_provider = task["wa_provider"]
            phone = task["phone"]
            wa_body = task["wa_body"]
            log_entry = task["log_entry"]
            public_pdf_url = task["public_pdf_url"]
            receipt_filename = task["receipt_filename"]
            receipt_no = task["receipt_no"]

            if wa_provider == "meta":
                meta_token = cfg.get("meta_wa_token")
                phone_number_id = cfg.get("meta_wa_phone_number_id")

                if not meta_token or not phone_number_id:
                    log_entry["status"] = "Error"
                    log_entry["details"] = "Meta API Error: Token o Phone Number ID no configurado."
                else:
                    try:
                        dest_phone = "".join(filter(str.isdigit, phone))
                        headers = {
                            "Authorization": f"Bearer {meta_token}",
                            "Content-Type": "application/json"
                        }
                        text_payload = {
                            "messaging_product": "whatsapp",
                            "recipient_type": "individual",
                            "to": dest_phone,
                            "type": "text",
                            "text": {
                                "preview_url": False,
                                "body": wa_body
                            }
                        }
                        res_text = requests.post(
                            f"https://graph.facebook.com/v19.0/{phone_number_id}/messages",
                            headers=headers,
                            json=text_payload,
                            timeout=4.0
                        )

                        doc_status = ""
                        if "127.0.0.1" not in public_pdf_url and "localhost" not in public_pdf_url:
                            doc_payload = {
                                "messaging_product": "whatsapp",
                                "recipient_type": "individual",
                                "to": dest_phone,
                                "type": "document",
                                "document": {
                                    "link": public_pdf_url,
                                    "filename": receipt_filename,
                                    "caption": f"Recibo Oficial Nº {receipt_no}"
                                }
                            }
                            res_doc = requests.post(
                                f"https://graph.facebook.com/v19.0/{phone_number_id}/messages",
                                headers=headers,
                                json=doc_payload,
                                timeout=4.0
                            )
                            if res_doc.status_code not in [200, 201]:
                                doc_status = f" | Error al enviar PDF: {res_doc.text}"
                            else:
                                doc_status = " | PDF enviado exitosamente."
                        else:
                            doc_status = " | Envío de archivo PDF omitido en red local."

                        if res_text.status_code in [200, 201]:
                            log_entry["status"] = "Enviado"
                            log_entry["details"] = f"Enviado vía Meta WhatsApp. ID: {res_text.json().get('messages', [{}])[0].get('id')}{doc_status}"
                        else:
                            log_entry["status"] = "Error"
                            log_entry["details"] = f"Meta API Error {res_text.status_code}: {res_text.text}{doc_status}"
                    except Exception as e:
                        log_entry["status"] = "Error"
                        log_entry["details"] = f"Fallo Meta API: {str(e)}"
                        print(f"[Notifications] Meta WhatsApp dispatch error to {phone}: {e}")

            elif wa_provider == "twilio":
                try:
                    url = f"https://api.twilio.com/2010-04-01/Accounts/{cfg['twilio_sid']}/Messages.json"
                    recipient_num = phone
                    if not recipient_num.startswith("whatsapp:"):
                        recipient_num = f"whatsapp:{recipient_num}"
                    from_num = cfg.get('twilio_whatsapp_from', '')
                    if not from_num.startswith("whatsapp:"):
                        from_num = f"whatsapp:{from_num}"

                    payload = {
                        "To": recipient_num,
                        "From": from_num,
                        "Body": wa_body
                    }
                    if public_pdf_url.startswith("http://") and "127.0.0.1" not in public_pdf_url:
                        payload["MediaUrl"] = public_pdf_url

                    response = requests.post(
                        url,
                        data=payload,
                        auth=(cfg['twilio_sid'], cfg['twilio_token']),
                        timeout=4.0
                    )
                    if response.status_code in [200, 201]:
                        log_entry["status"] = "Enviado"
                        log_entry["details"] = f"Enviado vía Twilio WhatsApp. SID: {response.json().get('sid')}"
                    else:
                        log_entry["status"] = "Error"
                        log_entry["details"] = f"Twilio API Error {response.status_code}: {response.text}"
                except Exception as e:
                    log_entry["status"] = "Error"
                    log_entry["details"] = f"Fallo Twilio: {str(e)}"
                    print(f"[Notifications] Twilio dispatch error to {phone}: {e}")

        # Final sync of log statuses
        try:
            sm.sync()
        except Exception as e:
            print(f"[Notifications] Error syncing logs: {e}")

    @staticmethod
    def send_receipt_notifications(sm, unit_id, receipt_pdf_path, receipt_no, resident_name, concept, amount, payment_date, reference, background=True):
        """
        Sends notifications by Email and WhatsApp for a specific receipt.
        If SMTP and Twilio parameters are not configured, it runs in simulation mode.
        Dispatches network delivery asynchronously in the background so the UI never hangs.
        """
        # Try to find historical contact details from Aliquot or Charge
        hist_owner = ""
        hist_email1 = ""
        hist_phone1 = ""
        hist_tenant = ""
        hist_email2 = ""
        hist_phone2 = ""

        if receipt_no.startswith("REC-AL-"):
            aliquot_id = receipt_no.replace("REC-AL-", "")
            aliquot = next((a for a in sm.data["aliquots"] if a["id"] == aliquot_id), None)
            if aliquot:
                hist_owner = aliquot.get("owner_name")
                hist_email1 = aliquot.get("email_owner")
                hist_phone1 = aliquot.get("phone_owner")
                hist_tenant = aliquot.get("tenant_name")
                hist_email2 = aliquot.get("email_tenant")
                hist_phone2 = aliquot.get("phone_tenant")
        elif receipt_no.startswith("REC-AC-"):
            charge_id = receipt_no.replace("REC-AC-", "")
            charge = next((c for c in sm.data["additional_charges"] if c["id"] == charge_id), None)
            if charge:
                hist_owner = charge.get("owner_name")
                hist_email1 = charge.get("email_owner")
                hist_phone1 = charge.get("phone_owner")
                hist_tenant = charge.get("tenant_name")
                hist_email2 = charge.get("email_tenant")
                hist_phone2 = charge.get("phone_tenant")

        unit = next((u for u in sm.data["units"] if u["id"] == str(unit_id)), None)
        if not unit and not hist_owner:
            print(f"[Notifications] Unit {unit_id} not found and no historical owner info available.")
            return False

        owner_name = hist_owner or (unit["owner"] if unit else "Propietario")
        email1 = hist_email1 or (unit.get("email1", "") if unit else "")
        phone1 = hist_phone1 or (unit.get("phone1", "") if unit else "")

        tenant_name = hist_tenant or (unit.get("tenant", "") if unit else "")
        email2 = hist_email2 or (unit.get("email2", "") if unit else "")
        phone2 = hist_phone2 or (unit.get("phone2", "") if unit else "")

        contacts = []
        # Add Owner
        if owner_name:
            contacts.append({
                "role": "Propietario",
                "name": owner_name,
                "email": email1.strip(),
                "phone": phone1.strip()
            })
        # Add Tenant if exists
        if tenant_name:
            contacts.append({
                "role": "Inquilino",
                "name": tenant_name,
                "email": email2.strip(),
                "phone": phone2.strip()
            })

        # Filter contacts to those with at least some info
        contacts = [c for c in contacts if c["email"] or c["phone"]]
        if not contacts:
            print(f"[Notifications] No contact information found for Unit {unit_id} (current or historical).")
            return False

        # Get settings
        cfg = sm.config
        condo_name = (cfg.get("condo_name") or "Condominio Casales San Pedro").strip()
        condo_ruc = (cfg.get("condo_ruc") or "").strip()
        condo_address = (cfg.get("condo_address") or "").strip()
        currency = cfg.get("currency", "$")

        smtp_configured = bool(cfg.get("smtp_host") and cfg.get("smtp_user") and cfg.get("smtp_password") and cfg.get("smtp_from"))
        twilio_configured = bool(cfg.get("twilio_sid") and cfg.get("twilio_token") and cfg.get("twilio_whatsapp_from"))

        # Determine public URL of the receipt PDF
        receipt_filename = os.path.basename(receipt_pdf_path)
        public_pdf_url = f"http://127.0.0.1:8000/static/receipts/{receipt_filename}"

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        results = []
        email_tasks = []
        wa_tasks = []

        for c in contacts:
            name = c["name"]
            role = c["role"]
            email = c["email"]
            phone = c["phone"]

            # --- EMAIL CHANNEL ---
            if email:
                subject = f"Comprobante de Pago Nº {receipt_no} - Depto {unit_id} | {condo_name}"

                plain_body = f"""============================================================
{condo_name.upper()}
ADMINISTRACIÓN Y CONTROL DE PAGOS
COMPROBANTE OFICIAL DE PAGO DIGITAL
============================================================

Estimado(a) {name} ({role}),

Le confirmamos que su pago ha sido recibido, verificado y conciliado exitosamente en el sistema de administración del condominio.

------------------------------------------------------------
RESUMEN DE LA TRANSACCIÓN:
------------------------------------------------------------
- Nº de Recibo:           {receipt_no}
- Departamento / Unidad:  {unit_id}
- Condómino / Titular:    {name}
- Concepto:               {concept}
- Monto Recibido:         {currency}{amount:.2f}
- Fecha de Pago:          {payment_date}
- Referencia Bancaria:    {reference or 'Validación Manual'}
------------------------------------------------------------

DOCUMENTO ADJUNTO:
Su recibo oficial en formato PDF ({receipt_filename}) se encuentra adjunto a este correo electrónico para su respaldo y archivo contable.

VALIDACIÓN ELECTRÓNICA:
Este comprobante digital cuenta con respaldo oficial tras la confirmación de la transacción en la cuenta bancaria del condominio.

------------------------------------------------------------
{condo_name}
{f'RUC: {condo_ruc}' if condo_ruc else ''}
{f'Dirección: {condo_address}' if condo_address else ''}
Contacto de Administración: {cfg.get('smtp_from') or cfg.get('smtp_user')}

Aviso: Mensaje transaccional generado automáticamente por el sistema de administración del condominio. Si recibió este mensaje por error, por favor comuníquese con la administración.
============================================================
"""

                ruc_html = f'<p style="margin: 0 0 2px 0;">RUC: {condo_ruc}</p>' if condo_ruc else ''
                addr_html = f'<p style="margin: 0 0 2px 0;">{condo_address}</p>' if condo_address else ''
                admin_email = cfg.get('smtp_from') or cfg.get('smtp_user')

                html_body = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Recibo Oficial de Pago Nº {receipt_no}</title>
  <style>
    body {{
      font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, Helvetica, Arial, sans-serif;
      color: #1e293b;
      line-height: 1.6;
      background-color: #f1f5f9;
      margin: 0;
      padding: 24px 12px;
      -webkit-font-smoothing: antialiased;
    }}
    .email-wrapper {{
      max-width: 600px;
      margin: 0 auto;
      background: #ffffff;
      border-radius: 10px;
      overflow: hidden;
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
      border: 1px solid #e2e8f0;
    }}
    .email-header {{
      background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
      color: #ffffff;
      padding: 26px 24px;
      text-align: center;
    }}
    .email-header h1 {{
      margin: 0;
      font-size: 20px;
      font-weight: 700;
      letter-spacing: 0.5px;
      text-transform: uppercase;
      color: #ffffff;
    }}
    .email-header .subtitle {{
      margin: 6px 0 0 0;
      font-size: 13px;
      color: #94a3b8;
      font-weight: 400;
    }}
    .badge {{
      display: inline-block;
      margin-top: 12px;
      background: rgba(37, 99, 235, 0.2);
      border: 1px solid #3b82f6;
      color: #93c5fd;
      padding: 4px 12px;
      border-radius: 20px;
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.5px;
    }}
    .email-body {{
      padding: 28px 24px;
    }}
    .greeting {{
      font-size: 15px;
      color: #0f172a;
      margin-top: 0;
      margin-bottom: 12px;
    }}
    .intro-text {{
      font-size: 14px;
      color: #475569;
      margin-bottom: 20px;
    }}
    .summary-card {{
      background: #f8fafc;
      border: 1px solid #e2e8f0;
      border-radius: 8px;
      padding: 18px;
      margin: 20px 0;
    }}
    .summary-title {{
      font-size: 13px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: #0f172a;
      margin: 0 0 14px 0;
      border-bottom: 1px solid #e2e8f0;
      padding-bottom: 8px;
    }}
    .data-table {{
      width: 100%;
      border-collapse: collapse;
    }}
    .data-table td {{
      padding: 6px 0;
      font-size: 13.5px;
      vertical-align: top;
    }}
    .data-label {{
      color: #64748b;
      font-weight: 600;
      width: 42%;
    }}
    .data-value {{
      color: #0f172a;
      font-weight: 500;
      text-align: right;
    }}
    .data-value-highlight {{
      color: #16a34a;
      font-weight: 700;
      font-size: 16px;
      text-align: right;
    }}
    .attachment-box {{
      background: #eff6ff;
      border: 1px dashed #3b82f6;
      border-radius: 8px;
      padding: 14px 16px;
      margin-top: 20px;
    }}
    .attachment-text {{
      font-size: 13px;
      color: #1e40af;
      margin: 0;
    }}
    .validation-note {{
      margin-top: 20px;
      padding: 12px 14px;
      background: #f0fdf4;
      border-left: 4px solid #16a34a;
      border-radius: 0 6px 6px 0;
      font-size: 12.5px;
      color: #166534;
      line-height: 1.5;
    }}
    .email-footer {{
      background: #f8fafc;
      border-top: 1px solid #e2e8f0;
      padding: 20px 24px;
      text-align: center;
      font-size: 11.5px;
      color: #64748b;
      line-height: 1.6;
    }}
    .footer-condo {{
      font-weight: 700;
      color: #334155;
    }}
    .footer-disclaimer {{
      margin-top: 10px;
      color: #94a3b8;
      font-size: 11px;
    }}
  </style>
</head>
<body>
  <!-- Preheader preview text -->
  <div style="display:none;font-size:1px;color:#ffffff;line-height:1px;max-height:0px;max-width:0px;opacity:0;overflow:hidden;">
    Comprobante oficial de pago Nº {receipt_no} para el Departamento {unit_id} ({condo_name}). Monto: {currency}{amount:.2f}.
  </div>

  <div class="email-wrapper">
    <div class="email-header">
      <h1>{condo_name}</h1>
      <p class="subtitle">Administración y Control de Pagos</p>
      <span class="badge">COMPROBANTE OFICIAL DE PAGO</span>
    </div>

    <div class="email-body">
      <p class="greeting">Estimado(a) <strong>{name}</strong> ({role}),</p>
      <p class="intro-text">
        Le confirmamos que su pago ha sido recibido, verificado y conciliado exitosamente en el sistema de administración del condominio.
      </p>

      <div class="summary-card">
        <div class="summary-title">Resumen de la Transacción</div>
        <table class="data-table">
          <tr>
            <td class="data-label">Nº de Recibo:</td>
            <td class="data-value"><strong>{receipt_no}</strong></td>
          </tr>
          <tr>
            <td class="data-label">Departamento / Unidad:</td>
            <td class="data-value">{unit_id}</td>
          </tr>
          <tr>
            <td class="data-label">Condómino / Titular:</td>
            <td class="data-value">{name}</td>
          </tr>
          <tr>
            <td class="data-label">Concepto:</td>
            <td class="data-value">{concept}</td>
          </tr>
          <tr>
            <td class="data-label">Fecha de Pago:</td>
            <td class="data-value">{payment_date}</td>
          </tr>
          <tr>
            <td class="data-label">Referencia Bancaria:</td>
            <td class="data-value">{reference or 'Validación Manual'}</td>
          </tr>
          <tr style="border-top: 1px solid #e2e8f0;">
            <td class="data-label" style="padding-top: 10px; font-weight: 700; color: #0f172a;">Monto Recibido:</td>
            <td class="data-value-highlight" style="padding-top: 10px;">{currency}{amount:.2f}</td>
          </tr>
        </table>
      </div>

      <div class="attachment-box">
        <p class="attachment-text">
          📎 <strong>Archivo Adjunto:</strong> Su recibo oficial en formato PDF (<code>{receipt_filename}</code>) se encuentra adjunto a este mensaje para su respaldo y archivo personal.
        </p>
      </div>

      <div class="validation-note">
        ✅ <strong>Documento Validado Electrónicamente:</strong> Este comprobante digital cuenta con respaldo oficial tras la confirmación de la transacción en la cuenta bancaria del condominio.
      </div>
    </div>

    <div class="email-footer">
      <p class="footer-condo" style="margin: 0 0 4px 0;">{condo_name}</p>
      {ruc_html}
      {addr_html}
      <p style="margin: 4px 0 0 0;">Contacto de Administración: <a href="mailto:{admin_email}" style="color: #2563eb; text-decoration: none;">{admin_email}</a></p>
      <div class="footer-disclaimer">
        Este es un mensaje transaccional enviado automáticamente por el sistema de administración. Si usted no es residente ni propietario del departamento {unit_id}, por favor ignore este mensaje o comuníquese con la administración.
      </div>
    </div>
  </div>
</body>
</html>"""

                log_entry = {
                    "timestamp": timestamp,
                    "unit": unit_id,
                    "recipient": f"{name} ({role})",
                    "channel": "Email",
                    "destination": email,
                    "subject": subject,
                    "status": "Enviado" if not smtp_configured else "Pendiente",
                    "mode": "Real" if smtp_configured else "Simulado",
                    "details": "Modo simulación (sin credenciales SMTP configuradas)." if not smtp_configured else "Procesando envío en segundo plano..."
                }

                if smtp_configured:
                    try:
                        from_addr, to_addr, msg_obj = build_smtp_message(
                            cfg=cfg,
                            to_email=email,
                            to_name=f"{name} ({role})",
                            subject=subject,
                            plain_text=plain_body,
                            html_text=html_body,
                            attachment_path=receipt_pdf_path,
                            attachment_name=receipt_filename,
                            x_mailer=f"{condo_name} Notification System"
                        )

                        email_tasks.append({
                            "from_email": from_addr,
                            "email": to_addr,
                            "msg": msg_obj,
                            "log_entry": log_entry
                        })
                    except Exception as e:
                        log_entry["status"] = "Error"
                        log_entry["details"] = f"Error preparando email: {str(e)}"

                sm.data["notification_logs"].append(log_entry)
                results.append(log_entry)

            # --- WHATSAPP CHANNEL ---
            if phone:
                wa_body = f"""✅ *{cfg.get('condo_name', 'Condominio Casales San Pedro')} - Recibo Oficial de Pago*

Estimado(a) {name} ({role}), su pago ha sido conciliado y registrado con éxito.

*Nº Recibo:* {receipt_no}
*Depto:* {unit_id}
*Concepto:* {concept}
*Monto:* {cfg.get('currency', '$')}{amount:.2f}
*Referencia:* {reference or 'N/A'}
*Fecha Pago:* {payment_date}

Puede descargar su recibo en PDF aquí: {public_pdf_url}"""

                wa_provider = cfg.get("whatsapp_provider", "simulated")
                if wa_provider == "simulated" and twilio_configured:
                    wa_provider = "twilio"

                is_real_wa = wa_provider in ["twilio", "meta"]
                log_entry = {
                    "timestamp": timestamp,
                    "unit": unit_id,
                    "recipient": f"{name} ({role})",
                    "channel": "WhatsApp",
                    "destination": phone,
                    "subject": "Recibo de Pago",
                    "status": "Enviado" if not is_real_wa else "Pendiente",
                    "mode": "Real" if is_real_wa else "Simulado",
                    "details": "Modo simulación (sin credenciales de WhatsApp configuradas)." if not is_real_wa else "Procesando envío en segundo plano..."
                }
                
                if is_real_wa:
                    wa_tasks.append({
                        "wa_provider": wa_provider,
                        "phone": phone,
                        "wa_body": wa_body,
                        "log_entry": log_entry,
                        "public_pdf_url": public_pdf_url,
                        "receipt_filename": receipt_filename,
                        "receipt_no": receipt_no
                    })
                    
                sm.data["notification_logs"].append(log_entry)
                results.append(log_entry)
                
        # Sync initial log entries
        sm.sync()
        
        # Dispatch real network sending
        if email_tasks or wa_tasks:
            if background:
                thread = threading.Thread(
                    target=NotificationManager._dispatch_receipt_notifications,
                    args=(sm, email_tasks, wa_tasks, cfg),
                    daemon=True
                )
                thread.start()
            else:
                NotificationManager._dispatch_receipt_notifications(sm, email_tasks, wa_tasks, cfg)
                
        return results

