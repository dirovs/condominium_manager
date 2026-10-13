import random
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlmodel import Session, select
from sqlalchemy import func

from schemas import (
    LoginRequest,
    RequestTempPasswordRequest,
    ResetPasswordRequest,
    AdminRegisterRequest
)
from models import User, Unit
from security import (
    create_access_token,
    decode_access_token,
    get_password_hash,
    verify_password,
    login_limiter,
    require_superadmin
)
from notifications import send_smtp_email, build_smtp_message

router = APIRouter(tags=["Authentication & Users"])

def get_sm():
    from app import sm
    return sm

@router.post("/api/auth/request-temp-password")
def request_temp_password(req: RequestTempPasswordRequest, request: Request):
    sm = get_sm()
    client_ip = request.client.host if request.client else "127.0.0.1"
    req_cedula = str(req.cedula or "").strip()
    req_email = str(req.email or "").strip().lower()
    req_role = str(req.role or "").strip()

    rate_key = f"pin:{client_ip}:{req_cedula or req_email}"
    login_limiter.check(rate_key, max_attempts=5, window_seconds=300)

    if not req_cedula and not req_email:
        raise HTTPException(status_code=400, detail="Debe ingresar su cédula o correo electrónico.")

    with Session(sm.engine) as session:
        allowed_roles = [req_role]
        if req_role in ["admin", "superadmin", ""]:
            allowed_roles = ["admin", "superadmin"]

        conditions = []
        if req_cedula:
            conditions.append(func.trim(User.cedula) == req_cedula)
        if req_email:
            conditions.append(func.lower(User.email) == req_email)

        query = select(User).where(User.role.in_(allowed_roles))
        if len(conditions) == 2:
            user = session.exec(query.where(conditions[0], conditions[1])).first()
            if not user:
                user = session.exec(query.where(conditions[0] | conditions[1])).first()
        elif len(conditions) == 1:
            user = session.exec(query.where(conditions[0])).first()
        else:
            user = None

        # Auto-sync from units if user is owner/tenant and not yet in User table
        if not user and req_role in ["owner", "tenant"]:
            for u in sm.data["units"]:
                if req_role == "owner":
                    u_ced = str(u.get("cedula_owner") or "").strip()
                    u_email = str(u.get("email1") or "").strip().lower()
                    if (req_cedula and u_ced == req_cedula) or (req_email and u_email == req_email):
                        sm.sync_user_from_unit(session, u)
                        session.commit()
                        user = session.exec(select(User).where(
                            User.role == req_role,
                            (func.trim(User.cedula) == u_ced) | (func.lower(User.email) == u_email)
                        )).first()
                        break
                elif req_role == "tenant":
                    u_ced = str(u.get("cedula_tenant") or "").strip()
                    u_email = str(u.get("email2") or "").strip().lower()
                    if (req_cedula and u_ced == req_cedula) or (req_email and u_email == req_email):
                        sm.sync_user_from_unit(session, u)
                        session.commit()
                        user = session.exec(select(User).where(
                            User.role == req_role,
                            (func.trim(User.cedula) == u_ced) | (func.lower(User.email) == u_email)
                        )).first()
                        break

        if not user:
            raise HTTPException(
                status_code=400,
                detail=f"No se encontró ningún usuario con los datos ingresados para el rol seleccionado ({req_role}). Verifique que sus datos coincidan con los registrados en la administración."
            )

        # Generate 6-digit temporal PIN
        temp_pin = f"{random.randint(100000, 999999)}"
        user.temp_password = temp_pin
        user.temp_password_expiry = (datetime.now() + timedelta(minutes=15)).isoformat()
        session.add(user)
        session.commit()

        # Send Email if SMTP is configured
        cfg = sm.config
        smtp_configured = bool(cfg.get("smtp_host") and cfg.get("smtp_user") and cfg.get("smtp_password"))
        sent = False
        smtp_err = ""

        condo_name = cfg.get("condo_name", "Condominio")

        email_body = f"""Hola {user.name},

Se ha solicitado una clave temporal de acceso para tu cuenta en {condo_name}.
Tu clave temporal es: {temp_pin} (Válida por 15 minutos).

Ingresa esta clave temporal en la pantalla de acceso para ingresar y definir tu nueva contraseña permanente.

Si no solicitaste este cambio, por favor ignora este correo.
"""
        html_body = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: 'Segoe UI', Arial, sans-serif; color: #1e293b; line-height: 1.6; background-color: #f8fafc; margin: 0; padding: 20px; }}
  .card {{ max-width: 520px; margin: 0 auto; background: #ffffff; border-radius: 8px; overflow: hidden; border: 1px solid #e2e8f0; }}
  .header {{ background: #1e293b; color: #ffffff; padding: 20px; text-align: center; }}
  .header h2 {{ margin: 0; font-size: 20px; }}
  .content {{ padding: 24px; text-align: center; }}
  .pin-box {{ background: #f1f5f9; border: 2px dashed #6366f1; border-radius: 8px; padding: 16px; margin: 20px 0; }}
  .pin-code {{ font-size: 32px; font-weight: 800; letter-spacing: 6px; color: #4338ca; margin: 0; }}
  .footer {{ text-align: center; padding: 14px; font-size: 12px; color: #64748b; background: #f8fafc; border-top: 1px solid #e2e8f0; }}
</style>
</head>
<body>
  <div class="card">
    <div class="header">
      <h2>{condo_name}</h2>
      <p style="margin:4px 0 0 0; font-size:13px; opacity:0.85;">Clave Temporal de Acceso</p>
    </div>
    <div class="content">
      <p style="text-align:left;">Hola <strong>{user.name}</strong>,</p>
      <p style="text-align:left;">Se ha solicitado una clave temporal de acceso para tu cuenta en CondoManager.</p>
      <div class="pin-box">
        <p style="margin:0 0 6px 0; font-size:12px; text-transform:uppercase; color:#64748b; font-weight:600;">Tu PIN Temporal (15 minutos):</p>
        <p class="pin-code">{temp_pin}</p>
      </div>
      <p style="font-size:13px; color:#475569; text-align:left;">Ingresa este PIN en la pantalla de acceso para definir tu contraseña permanente.</p>
      <p style="font-size:12px; color:#94a3b8; text-align:left; margin-top:16px;">Si no solicitaste este cambio, por favor ignora este correo de forma segura.</p>
    </div>
    <div class="footer">
      <p style="margin:0;">Mensaje de seguridad emitido por {condo_name}.</p>
    </div>
  </div>
</body>
</html>"""

        if smtp_configured:
            try:
                from_addr, to_addr, msg = build_smtp_message(
                    cfg=cfg,
                    to_email=user.email,
                    to_name=user.name,
                    subject=f"Clave Temporal de Acceso - {condo_name}",
                    plain_text=email_body,
                    html_text=html_body,
                    x_mailer=f"{condo_name} Auth Service"
                )
                ok, detail = send_smtp_email(cfg, from_addr, to_addr, msg, timeout=12.0)
                sent = ok
            except Exception as e:
                smtp_err = str(e)
                print(f"[SMTP Error] Error sending mail to {user.email}: {e}")

        if not sent:
            return {
                "status": "success",
                "message": f"PIN Temporal generado: {temp_pin} (Válido por 15 min). Puedes usar este PIN directamente para ingresar.",
                "simulated": True,
                "temp_pin": temp_pin
            }
        else:
            return {
                "status": "success",
                "message": f"Clave temporal enviada exitosamente a tu correo ({user.email}). Revisa tu bandeja de entrada o carpeta de spam.",
                "simulated": False
            }

@router.post("/api/auth/reset-password")
def reset_password(req: ResetPasswordRequest):
    sm = get_sm()
    req_cedula = str(req.cedula or "").strip()
    req_role = str(req.role or "").strip()
    req_temp_pin = str(req.temp_password or "").strip()
    req_new_pwd = str(req.new_password or "").strip()

    if not req_temp_pin:
        raise HTTPException(status_code=400, detail="Debe ingresar el PIN temporal recibido.")
    if len(req_new_pwd) < 4:
        raise HTTPException(status_code=400, detail="La nueva contraseña debe tener al menos 4 caracteres.")

    with Session(sm.engine) as session:
        allowed_roles = [req_role]
        if req_role in ["admin", "superadmin", ""]:
            allowed_roles = ["admin", "superadmin"]

        user = session.exec(select(User).where(
            func.trim(User.cedula) == req_cedula,
            User.role.in_(allowed_roles)
        )).first()

        if not user or not user.temp_password:
            raise HTTPException(status_code=400, detail="No se ha solicitado una clave temporal para esta cuenta o ya fue utilizada.")

        if str(user.temp_password).strip() != req_temp_pin:
            raise HTTPException(status_code=400, detail="El PIN temporal ingresado es incorrecto.")

        if user.temp_password_expiry:
            try:
                expiry = datetime.fromisoformat(user.temp_password_expiry)
                if datetime.now() > expiry:
                    raise HTTPException(status_code=400, detail="El PIN temporal ha expirado (límite 15 min). Por favor solicite uno nuevo en 'Recuperar Clave'.")
            except ValueError:
                pass

        user.password_hash = get_password_hash(req_new_pwd)
        user.temp_password = None
        user.temp_password_expiry = None
        session.add(user)
        session.commit()
        return {"status": "success", "message": "¡Contraseña actualizada exitosamente! Ya puedes iniciar sesión con tu nueva contraseña."}

@router.post("/api/auth/login")
def login(req: LoginRequest, request: Request):
    sm = get_sm()
    client_ip = request.client.host if request.client else "127.0.0.1"
    req_cedula = str(req.cedula or "").strip()
    req_email = str(req.email or "").strip().lower()
    req_role = str(req.role or "").strip()
    req_password = str(req.password or "").strip()

    rate_key = f"login:{client_ip}:{req_cedula or req_email}"
    login_limiter.check(rate_key, max_attempts=5, window_seconds=300)

    if not req_password:
        raise HTTPException(status_code=400, detail="Debe ingresar su contraseña o PIN temporal.")
    if not req_cedula and not req_email:
        raise HTTPException(status_code=400, detail="Debe ingresar su cédula o correo electrónico.")

    with Session(sm.engine) as session:
        allowed_roles = [req_role]
        if req_role in ["admin", "superadmin", ""]:
            allowed_roles = ["admin", "superadmin"]

        conditions = []
        if req_cedula:
            conditions.append(func.trim(User.cedula) == req_cedula)
        if req_email:
            conditions.append(func.lower(User.email) == req_email)

        query = select(User).where(User.role.in_(allowed_roles))
        if len(conditions) == 2:
            user = session.exec(query.where(conditions[0], conditions[1])).first()
            if not user:
                user = session.exec(query.where(conditions[0] | conditions[1])).first()
        elif len(conditions) == 1:
            user = session.exec(query.where(conditions[0])).first()
        else:
            user = None

        # If not found in User table, check if exists in units and auto-sync
        if not user and req_role in ["owner", "tenant"]:
            for u in sm.data["units"]:
                if req_role == "owner":
                    u_ced = str(u.get("cedula_owner") or "").strip()
                    u_email = str(u.get("email1") or "").strip().lower()
                    if (req_cedula and u_ced == req_cedula) or (req_email and u_email == req_email):
                        sm.sync_user_from_unit(session, u)
                        session.commit()
                        user = session.exec(select(User).where(
                            User.role == req_role,
                            (func.trim(User.cedula) == u_ced) | (func.lower(User.email) == u_email)
                        )).first()
                        break
                elif req_role == "tenant":
                    u_ced = str(u.get("cedula_tenant") or "").strip()
                    u_email = str(u.get("email2") or "").strip().lower()
                    if (req_cedula and u_ced == req_cedula) or (req_email and u_email == req_email):
                        sm.sync_user_from_unit(session, u)
                        session.commit()
                        user = session.exec(select(User).where(
                            User.role == req_role,
                            (func.trim(User.cedula) == u_ced) | (func.lower(User.email) == u_email)
                        )).first()
                        break

        if not user:
            raise HTTPException(
                status_code=400,
                detail=f"No se encontró ningún usuario con los datos ingresados para el rol seleccionado ({req_role}). Verifique sus datos o solicite una clave en 'Recuperar Clave'."
            )

        # Check password and temporary PIN
        is_temp_valid = False
        if user.temp_password and str(user.temp_password).strip() == req_password:
            if user.temp_password_expiry:
                try:
                    expiry = datetime.fromisoformat(user.temp_password_expiry)
                    if datetime.now() <= expiry:
                        is_temp_valid = True
                    else:
                        raise HTTPException(status_code=400, detail="El PIN temporal ha expirado. Por favor solicite uno nuevo en la pestaña 'Recuperar Clave'.")
                except ValueError:
                    is_temp_valid = True
            else:
                is_temp_valid = True

        is_perm_valid, needs_rehash = verify_password(req_password, user.password_hash or "")

        # Transparent upgrade of legacy hash to Bcrypt
        if is_perm_valid and needs_rehash:
            user.password_hash = get_password_hash(req_password)
            session.add(user)
            session.commit()

        if not is_temp_valid and not is_perm_valid:
            if not user.password_hash and not user.temp_password:
                raise HTTPException(status_code=400, detail="Esta cuenta aún no tiene contraseña establecida. Por favor ve a la pestaña 'Recuperar Clave' para solicitar tu PIN temporal de primer acceso.")
            raise HTTPException(status_code=400, detail="Contraseña o PIN temporal incorrecto. Verifique e intente nuevamente.")

        needs_password_change = is_temp_valid

        # Determine unit for condo resident
        unit_id = None
        if req_role in ["owner", "tenant"]:
            for u in sm.data["units"]:
                if req_role == "owner" and (str(u.get("cedula_owner") or "").strip() == req_cedula or str(u.get("email1") or "").strip().lower() == req_email):
                    unit_id = str(u["id"])
                    break
                elif req_role == "tenant" and (str(u.get("cedula_tenant") or "").strip() == req_cedula or str(u.get("email2") or "").strip().lower() == req_email):
                    unit_id = str(u["id"])
                    break

            if not unit_id:
                if req_role == "owner":
                    db_u = session.exec(select(Unit).where(
                        (func.trim(Unit.cedula_owner) == req_cedula) | (func.lower(Unit.email1) == req_email)
                    )).first()
                    if db_u:
                        unit_id = str(db_u.id)
                elif req_role == "tenant":
                    db_u = session.exec(select(Unit).where(
                        (func.trim(Unit.cedula_tenant) == req_cedula) | (func.lower(Unit.email2) == req_email)
                    )).first()
                    if db_u:
                        unit_id = str(db_u.id)

        # Issue signed JWT
        token_payload = {
            "sub": user.cedula or user.email,
            "email": user.email,
            "role": user.role,
            "name": user.name,
            "unit_id": unit_id
        }
        access_token = create_access_token(token_payload)
        login_limiter.reset(rate_key)

        return {
            "status": "success",
            "access_token": access_token,
            "token_type": "bearer",
            "user": {
                "cedula": user.cedula,
                "email": user.email,
                "role": user.role,
                "name": user.name,
                "unit_id": unit_id
            },
            "needs_password_change": needs_password_change
        }

@router.post("/api/admin/register")
def register_admin(req: AdminRegisterRequest, request: Request):
    sm = get_sm()
    role = ""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            payload = decode_access_token(auth_header.split(" ", 1)[1].strip())
            role = str(payload.get("role", "")).strip().lower()
        except Exception:
            pass
    if not role:
        role = request.headers.get("X-User-Role", "").strip().lower()

    if role != "superadmin":
        raise HTTPException(status_code=403, detail="Permiso denegado. Solo el Superadministrador puede registrar administradores.")

    with Session(sm.engine) as session:
        existing = session.exec(select(User).where(User.cedula == req.cedula)).first()
        if existing:
            raise HTTPException(status_code=400, detail="El administrador ya se encuentra registrado.")

        pwd_hash = get_password_hash(req.password)
        admin = User(
            cedula=req.cedula,
            email=req.email,
            name=req.name,
            role="admin",
            password_hash=pwd_hash,
            is_active=True
        )
        session.add(admin)
        session.commit()
        return {"status": "success", "message": f"Administrador {req.name} registrado exitosamente."}

@router.get("/api/admins")
def get_admins(request: Request):
    sm = get_sm()
    role = ""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            payload = decode_access_token(auth_header.split(" ", 1)[1].strip())
            role = str(payload.get("role", "")).strip().lower()
        except Exception:
            pass
    if not role:
        role = request.headers.get("X-User-Role", "").strip().lower()

    if role != "superadmin":
        raise HTTPException(status_code=403, detail="Acceso denegado.")

    with Session(sm.engine) as session:
        admins = session.exec(select(User).where(User.role == "admin")).all()
        return [a.model_dump(exclude={"password_hash", "temp_password"}) for a in admins]
