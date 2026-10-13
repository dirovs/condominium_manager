import random
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlmodel import Session, select
from sqlalchemy import func, or_

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
    raw_ident = (req.identifier or "").strip()
    req_cedula = str(req.cedula or "").strip()
    req_email = str(req.email or "").strip().lower()
    req_role = str(req.role or "").strip()

    if raw_ident:
        if "@" in raw_ident:
            if not req_email:
                req_email = raw_ident.lower()
        else:
            if not req_cedula:
                req_cedula = raw_ident

    effective_key = req_cedula or req_email or raw_ident
    rate_key = f"pin:{client_ip}:{effective_key}"
    login_limiter.check(rate_key, max_attempts=5, window_seconds=300)

    if not effective_key:
        raise HTTPException(status_code=400, detail="Debe ingresar su cédula o correo electrónico.")

    with Session(sm.engine) as session:
        # Auto-sync from units if user is owner/tenant
        for u in sm.data.get("units", []):
            u_ced_o = str(u.get("cedula_owner") or "").strip()
            u_em_o = str(u.get("email1") or "").strip().lower()
            u_ced_t = str(u.get("cedula_tenant") or "").strip()
            u_em_t = str(u.get("email2") or "").strip().lower()

            matched = False
            if req_cedula and (req_cedula == u_ced_o or req_cedula == u_ced_t):
                matched = True
            elif req_email and (req_email == u_em_o or req_email == u_em_t):
                matched = True
            elif raw_ident and (raw_ident == u_ced_o or raw_ident.lower() == u_em_o or raw_ident == u_ced_t or raw_ident.lower() == u_em_t):
                matched = True

            if matched:
                sm.sync_user_from_unit(session, u)
                session.commit()

        conditions = []
        if req_cedula:
            conditions.append(func.trim(User.cedula) == req_cedula)
        if req_email:
            conditions.append(func.lower(User.email) == req_email)
        if raw_ident:
            conditions.append(func.trim(User.cedula) == raw_ident)
            conditions.append(func.lower(User.email) == raw_ident.lower())

        query = select(User)
        if req_role:
            if req_role in ["admin", "superadmin"]:
                query = query.where(User.role.in_(["superadmin", "admin"]))
            else:
                query = query.where(User.role == req_role)

        if conditions:
            query = query.where(or_(*conditions))

        user = session.exec(query).first()

        if not user:
            raise HTTPException(
                status_code=400,
                detail="No se encontró ningún usuario con los datos ingresados. Verifique que sus datos coincidan con los registrados en la administración."
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
    raw_ident = (req.identifier or "").strip()
    req_cedula = str(req.cedula or "").strip()
    req_role = str(req.role or "").strip()
    req_temp_pin = str(req.temp_password or "").strip()
    req_new_pwd = str(req.new_password or "").strip()

    if raw_ident and not req_cedula:
        req_cedula = raw_ident

    if not req_temp_pin:
        raise HTTPException(status_code=400, detail="Debe ingresar el PIN temporal recibido.")
    if len(req_new_pwd) < 4:
        raise HTTPException(status_code=400, detail="La nueva contraseña debe tener al menos 4 caracteres.")

    with Session(sm.engine) as session:
        conditions = []
        if req_cedula:
            conditions.append(func.trim(User.cedula) == req_cedula)
            conditions.append(func.lower(User.email) == req_cedula.lower())

        query = select(User)
        if req_role:
            if req_role in ["admin", "superadmin"]:
                query = query.where(User.role.in_(["admin", "superadmin"]))
            else:
                query = query.where(User.role == req_role)

        if conditions:
            query = query.where(or_(*conditions))

        user = session.exec(query).first()

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

    # Support multiple formats of identifier
    raw_ident = (req.identifier or req.username or "").strip()
    req_cedula = str(req.cedula or "").strip()
    req_email = str(req.email or "").strip().lower()
    req_role = str(req.role or "").strip().lower()
    req_password = str(req.password or "").strip()

    if raw_ident:
        if "@" in raw_ident:
            if not req_email:
                req_email = raw_ident.lower()
        else:
            if not req_cedula:
                req_cedula = raw_ident

    effective_key = req_cedula or req_email or raw_ident
    rate_key = f"login:{client_ip}:{effective_key}"
    login_limiter.check(rate_key, max_attempts=5, window_seconds=300)

    if not req_password:
        raise HTTPException(status_code=400, detail="Debe ingresar su contraseña o PIN temporal.")
    if not effective_key:
        raise HTTPException(status_code=400, detail="Debe ingresar su cédula, correo electrónico o usuario.")

    with Session(sm.engine) as session:
        # 1. Sync from units if resident exists and not yet in User table
        for u in sm.data.get("units", []):
            u_ced_o = str(u.get("cedula_owner") or "").strip()
            u_em_o = str(u.get("email1") or "").strip().lower()
            u_ced_t = str(u.get("cedula_tenant") or "").strip()
            u_em_t = str(u.get("email2") or "").strip().lower()

            matched = False
            if req_cedula and (req_cedula == u_ced_o or req_cedula == u_ced_t):
                matched = True
            elif req_email and (req_email == u_em_o or req_email == u_em_t):
                matched = True
            elif raw_ident and (raw_ident == u_ced_o or raw_ident.lower() == u_em_o or raw_ident == u_ced_t or raw_ident.lower() == u_em_t):
                matched = True

            if matched:
                sm.sync_user_from_unit(session, u)
                session.commit()

        # 2. Build user search conditions
        conditions = []
        if req_cedula:
            conditions.append(func.trim(User.cedula) == req_cedula)
        if req_email:
            conditions.append(func.lower(User.email) == req_email)
        if raw_ident:
            conditions.append(func.trim(User.cedula) == raw_ident)
            conditions.append(func.lower(User.email) == raw_ident.lower())

        query = select(User)
        if req_role:
            if req_role in ["admin", "superadmin"]:
                query = query.where(User.role.in_(["superadmin", "admin"]))
            else:
                query = query.where(User.role == req_role)

        if conditions:
            query = query.where(or_(*conditions))

        candidates = session.exec(query).all()

        if not candidates:
            raise HTTPException(
                status_code=400,
                detail="No se encontró ningún usuario con los datos ingresados. Verifique sus datos o solicite una clave en 'Recuperar Clave'."
            )

        # 3. Match password across candidates
        user = None
        is_temp_valid = False
        is_perm_valid = False

        for cand in candidates:
            # Check temp PIN
            cand_temp_valid = False
            if cand.temp_password and str(cand.temp_password).strip() == req_password:
                if cand.temp_password_expiry:
                    try:
                        expiry = datetime.fromisoformat(cand.temp_password_expiry)
                        if datetime.now() <= expiry:
                            cand_temp_valid = True
                        else:
                            raise HTTPException(status_code=400, detail="El PIN temporal ha expirado. Por favor solicite uno nuevo en la pestaña 'Recuperar Clave'.")
                    except ValueError:
                        cand_temp_valid = True
                else:
                    cand_temp_valid = True

            # Check permanent password
            cand_perm_valid, cand_needs_rehash = verify_password(req_password, cand.password_hash or "")
            if cand_perm_valid and cand_needs_rehash:
                cand.password_hash = get_password_hash(req_password)
                session.add(cand)
                session.commit()

            if cand_temp_valid or cand_perm_valid:
                user = cand
                is_temp_valid = cand_temp_valid
                is_perm_valid = cand_perm_valid
                break

        if not user:
            all_no_pwd = all(not c.password_hash and not c.temp_password for c in candidates)
            if all_no_pwd:
                raise HTTPException(status_code=400, detail="Esta cuenta aún no tiene contraseña establecida. Por favor ve a la pestaña 'Recuperar Clave' para solicitar tu PIN temporal de primer acceso.")
            raise HTTPException(status_code=400, detail="Contraseña o PIN temporal incorrecto. Verifique e intente nuevamente.")

        needs_password_change = is_temp_valid

        # 4. Determine unit for resident
        unit_id = None
        u_ced = str(user.cedula or "").strip()
        u_em = str(user.email or "").strip().lower()

        if user.role in ["owner", "tenant"]:
            for u in sm.data.get("units", []):
                if user.role == "owner" and (str(u.get("cedula_owner") or "").strip() == u_ced or str(u.get("email1") or "").strip().lower() == u_em):
                    unit_id = str(u["id"])
                    break
                elif user.role == "tenant" and (str(u.get("cedula_tenant") or "").strip() == u_ced or str(u.get("email2") or "").strip().lower() == u_em):
                    unit_id = str(u["id"])
                    break

            if not unit_id:
                if user.role == "owner":
                    db_u = session.exec(select(Unit).where(
                        (func.trim(Unit.cedula_owner) == u_ced) | (func.lower(Unit.email1) == u_em)
                    )).first()
                    if db_u:
                        unit_id = str(db_u.id)
                elif user.role == "tenant":
                    db_u = session.exec(select(Unit).where(
                        (func.trim(Unit.cedula_tenant) == u_ced) | (func.lower(Unit.email2) == u_em)
                    )).first()
                    if db_u:
                        unit_id = str(db_u.id)

        # 5. Issue JWT with resolved user role
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
