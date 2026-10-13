import json
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from security import require_admin, get_current_user
from schemas import SettingsUpdate, InitialBalanceUpdate, EmailTestRequest
from notifications import send_smtp_email, build_smtp_message

router = APIRouter(tags=["Settings & Initial Balances"])

def get_sm():
    from app import sm
    return sm

@router.get("/api/settings")
def get_settings(current_user: Optional[dict] = Depends(get_current_user)):
    sm = get_sm()
    cfg = sm.config.copy()

    # Si el usuario es un residente (owner o tenant), filtrar configuraciones sensibles del sistema
    if current_user and isinstance(current_user, dict):
        role = str(current_user.get("role", "")).lower()
        if role in ["owner", "tenant"]:
            return {
                "condo_name": cfg.get("condo_name", "Condominio"),
                "currency": cfg.get("currency", "$"),
                "directive_president": cfg.get("directive_president", ""),
                "directive_treasurer": cfg.get("directive_treasurer", ""),
                "condo_address": cfg.get("condo_address", ""),
                "condo_ruc": cfg.get("condo_ruc", ""),
                "late_fee_day": cfg.get("late_fee_day", 10),
                "late_fee_amount": cfg.get("late_fee_amount", 5.0),
                "default_aliquot_base": cfg.get("default_aliquot_base", 50.0),
            }

    cfg["google_credentials_json_configured"] = bool(cfg.get("google_credentials_json"))
    cfg["smtp_password_configured"] = bool(cfg.get("smtp_password"))
    cfg["twilio_token_configured"] = bool(cfg.get("twilio_token"))
    cfg["meta_wa_token_configured"] = bool(cfg.get("meta_wa_token"))
    cfg["meta_wa_verify_token_configured"] = bool(cfg.get("meta_wa_verify_token"))
    cfg["db_password_configured"] = bool(cfg.get("db_password"))
    return cfg

@router.post("/api/settings")
def update_settings(settings: SettingsUpdate, current_user: dict = Depends(require_admin)):
    sm = get_sm()
    try:
        if settings.google_credentials_json and settings.google_credentials_json != "******":
            json.loads(settings.google_credentials_json)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Google Credentials JSON format.")

    try:
        return sm.update_settings(
            spreadsheet_id=settings.spreadsheet_id,
            google_credentials_json=settings.google_credentials_json,
            use_google_sheets=settings.use_google_sheets,
            late_fee_day=settings.late_fee_day,
            late_fee_amount=settings.late_fee_amount,
            initial_bank_balance=settings.initial_bank_balance,
            default_aliquot_base=settings.default_aliquot_base,
            smtp_host=settings.smtp_host,
            smtp_port=settings.smtp_port,
            smtp_user=settings.smtp_user,
            smtp_password=settings.smtp_password,
            smtp_from=settings.smtp_from,
            twilio_sid=settings.twilio_sid,
            twilio_token=settings.twilio_token,
            twilio_whatsapp_from=settings.twilio_whatsapp_from,
            whatsapp_provider=settings.whatsapp_provider,
            meta_wa_token=settings.meta_wa_token,
            meta_wa_phone_number_id=settings.meta_wa_phone_number_id,
            meta_wa_verify_token=settings.meta_wa_verify_token,
            meta_wa_business_account_id=settings.meta_wa_business_account_id,
            directive_president=settings.directive_president,
            directive_treasurer=settings.directive_treasurer,
            condo_name=settings.condo_name,
            condo_address=settings.condo_address,
            condo_ruc=settings.condo_ruc,
            exonerate_directiva=settings.exonerate_directiva,
            receipt_recipient_type=settings.receipt_recipient_type,
            app_mode=settings.app_mode,
            db_type=settings.db_type,
            db_host=settings.db_host,
            db_port=settings.db_port,
            db_user=settings.db_user,
            db_password=settings.db_password,
            db_name=settings.db_name,
            db_custom_url=settings.db_custom_url
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/settings/test-email")
def test_email_endpoint(
    req: Optional[EmailTestRequest] = None,
    current_user: dict = Depends(require_admin)
):
    sm = get_sm()
    cfg = dict(sm.config)
    if req:
        if req.smtp_host is not None and req.smtp_host.strip():
            cfg["smtp_host"] = req.smtp_host.strip()
        if req.smtp_port is not None:
            cfg["smtp_port"] = req.smtp_port
        if req.smtp_user is not None and req.smtp_user.strip():
            cfg["smtp_user"] = req.smtp_user.strip()
        if req.smtp_password is not None and req.smtp_password.strip():
            cfg["smtp_password"] = req.smtp_password.strip()
        if req.smtp_from is not None and req.smtp_from.strip():
            cfg["smtp_from"] = req.smtp_from.strip()

    target_email = (req.recipient_email.strip() if req and req.recipient_email and req.recipient_email.strip() else None) or cfg.get("smtp_user") or cfg.get("smtp_from")
    if not target_email:
        raise HTTPException(status_code=400, detail="Debe ingresar un correo de destino o configurar el usuario SMTP.")

    try:
        condo_name = cfg.get("condo_name", "Condominio Casales San Pedro")
        from_email_cfg = cfg.get("smtp_from") or cfg.get("smtp_user")
        subject = f"Verificación de Conexión de Correo - {condo_name}"

        plain_text = f"""============================================================
{condo_name.upper()} - VERIFICACIÓN DE CONEXIÓN SMTP
============================================================

¡Hola!

Este es un mensaje de prueba enviado desde el sistema de administración de {condo_name}.

Si recibes este mensaje, la configuración de correo electrónico (SMTP) está activa, autenticada y funcionando correctamente. A partir de este momento, los recibos oficiales y las notificaciones de pago se despacharán automáticamente a los correos de los condóminos.

DETALLES DE CONFIGURACIÓN:
- Servidor SMTP: {cfg.get('smtp_host')}
- Remitente:     {from_email_cfg}
- Destinatario:  {target_email}
- Fecha y Hora:  {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}

============================================================
{condo_name} - Administración y Control de Pagos
"""
        html_text = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Verificación de Conexión SMTP - {condo_name}</title>
</head>
<body style="font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, Arial, sans-serif; background-color: #f8fafc; padding: 20px; margin: 0; color: #1e293b;">
  <div style="max-width: 540px; margin: 0 auto; background: #ffffff; border-radius: 10px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);">
    <div style="background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); color: #ffffff; padding: 24px; text-align: center;">
      <h2 style="margin: 0; font-size: 20px; text-transform: uppercase; letter-spacing: 0.5px;">{condo_name}</h2>
      <p style="margin: 6px 0 0 0; font-size: 13px; color: #94a3b8;">Verificación de Conexión y Entrega SMTP</p>
    </div>
    <div style="padding: 26px 24px; line-height: 1.6;">
      <div style="background: #f0fdf4; border-left: 4px solid #16a34a; padding: 12px 16px; border-radius: 0 6px 6px 0; margin-bottom: 20px;">
        <h3 style="color: #166534; margin: 0 0 4px 0; font-size: 15px;">✅ Conexión Exitosa</h3>
        <p style="margin: 0; font-size: 13.5px; color: #15803d;">El servidor de correo electrónico ha sido configurado y validado satisfactoriamente.</p>
      </div>
      <p style="font-size: 14px; color: #334155; margin-bottom: 16px;">
        A partir de este momento, los recibos oficiales y las notificaciones de pago se despacharán automáticamente a los correos de los condóminos.
      </p>
      <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; font-size: 13px;">
        <div style="font-weight: bold; color: #0f172a; margin-bottom: 8px; border-bottom: 1px solid #e2e8f0; padding-bottom: 4px;">Parámetros Verificados:</div>
        <p style="margin: 4px 0;"><strong>Servidor:</strong> {cfg.get('smtp_host')}</p>
        <p style="margin: 4px 0;"><strong>Remitente:</strong> {from_email_cfg}</p>
        <p style="margin: 4px 0;"><strong>Destinatario:</strong> {target_email}</p>
        <p style="margin: 4px 0;"><strong>Fecha y Hora:</strong> {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</p>
      </div>
    </div>
    <div style="background: #f8fafc; border-top: 1px solid #e2e8f0; padding: 14px; text-align: center; font-size: 11.5px; color: #64748b;">
      Mensaje de verificación generado automáticamente por el sistema de administración de {condo_name}.
    </div>
  </div>
</body>
</html>"""

        from_addr, to_addr, msg = build_smtp_message(
            cfg=cfg,
            to_email=target_email,
            to_name="Administrador",
            subject=subject,
            plain_text=plain_text,
            html_text=html_text,
            x_mailer=f"{condo_name} SMTP Verification"
        )

        ok, detail = send_smtp_email(cfg, from_addr, to_addr, msg, timeout=12.0)
        return {
            "status": "success",
            "message": f"Correo de prueba enviado exitosamente a {target_email}. {detail}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al enviar correo de prueba: {str(e)}")

@router.get("/api/initial-balances")
def get_initial_balances():
    sm = get_sm()
    if sm.data.get("initial_balances"):
        return sm.data["initial_balances"]
    return {
        "id": 1,
        "initial_bank_balance": float(sm.config.get("initial_bank_balance", 0.0)),
        "initial_reserve_fund": float(sm.config.get("initial_reserve_fund", 0.0)),
        "cut_off_date": "",
        "description": "Saldo inicial de apertura de cuentas",
        "notes": "",
        "updated_at": ""
    }

@router.post("/api/initial-balances")
def update_initial_balances(
    req: InitialBalanceUpdate,
    current_user: dict = Depends(require_admin)
):
    sm = get_sm()
    try:
        return sm.update_initial_balances(
            initial_bank_balance=req.initial_bank_balance,
            initial_reserve_fund=req.initial_reserve_fund,
            cut_off_date=req.cut_off_date,
            description=req.description,
            notes=req.notes
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
