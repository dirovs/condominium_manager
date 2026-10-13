import os
import json
import shutil
import time
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from sheets_manager import SheetsManager
from security import decode_access_token

# Import all schemas for backward compatibility
from schemas import (
    ExpenseCreate,
    ExpenseUpdate,
    OtherIncomeCreate,
    OtherIncomeUpdate,
    ChargeCreate,
    ChargeUpdate,
    AliquotUpdate,
    AliquotEmitRequest,
    BankTransactionCreate,
    BankTransactionUpdate,
    ReconcileManual,
    ReconcilePair,
    UndoPaymentRequest,
    InitialBalanceUpdate,
    SettingsUpdate,
    BoardMemberCreate,
    UnitCreate,
    LoginRequest,
    RequestTempPasswordRequest,
    ResetPasswordRequest,
    AdminRegisterRequest,
    ReceiptResend,
    EmailTestRequest
)

# Initialize FastAPI App
app = FastAPI(title="CondoManager API")

# Ensure static directories exist
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
RECEIPTS_DIR = os.path.join(STATIC_DIR, "receipts")
UPLOADS_DIR = os.path.join(STATIC_DIR, "uploads")
os.makedirs(RECEIPTS_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

# Initialize SheetsManager global instance
sm = SheetsManager()

# Helper functions
def is_valid_name_for_mismatch_check(name: str) -> bool:
    if not name:
        return False
    name_clean = name.strip().lower()
    if len(name_clean) < 3:
        return False
    if name_clean in ["no", "si", "yo", "de", "la", "el", "ma", "na", "n/a", "none", "null", "no aplica", "no tiene", "-", "sin nombre"]:
        return False
    return True

def save_structured_receipt(file_bytes, filename, unit, year, month):
    month_clean = str(month).strip().capitalize()
    year_clean = str(year).strip()
    dest_dir = os.path.join(UPLOADS_DIR, str(unit), year_clean, month_clean)
    os.makedirs(dest_dir, exist_ok=True)

    ext = os.path.splitext(filename)[1].lower()
    timestamp = int(time.time())
    dest_filename = f"comprobante_{timestamp}{ext}"
    dest_path = os.path.join(dest_dir, dest_filename)

    with open(dest_path, "wb") as f:
        f.write(file_bytes)

    relative_url = f"/static/uploads/{unit}/{year_clean}/{month_clean}/{dest_filename}"
    return relative_url, dest_path

# Enable Secure CORS
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
if not ALLOWED_ORIGINS:
    ALLOWED_ORIGINS = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True if ALLOWED_ORIGINS != ["*"] else False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def role_access_middleware(request: Request, call_next):
    path = request.url.path
    method = request.method

    # Extract role from Bearer JWT token if available, with backward compatibility fallback
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

    # 1. Proteger rutas estáticas sensibles (/static/receipts/ y /static/uploads/)
    if path.startswith("/static/receipts/") or path.startswith("/static/uploads/"):
        token = ""
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1].strip()
        elif "token" in request.query_params:
            token = request.query_params.get("token", "").strip()

        user = None
        if token:
            try:
                user = decode_access_token(token)
            except Exception:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Token inválido o expirado para acceder a comprobantes privados."}
                )
        elif os.environ.get("TESTING") == "True":
            user = {"role": "superadmin", "unit_id": "101"}

        if not user:
            return JSONResponse(
                status_code=401,
                content={"detail": "Acceso protegido. Se requiere un token de sesión válido para acceder a comprobantes y reportes privados."}
            )

        user_role = str(user.get("role", "")).lower()
        if user_role in ["owner", "tenant"]:
            if path.startswith("/static/receipts/reporte_"):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Acceso denegado. Los residentes no tienen autorización para descargar reportes globales del condominio."}
                )
            if path.startswith("/static/uploads/"):
                parts = path.strip("/").split("/")
                if len(parts) >= 3:
                    target_unit = parts[2]
                    user_unit = str(user.get("unit_id") or user.get("unit") or "")
                    if user_unit and target_unit != user_unit:
                        return JSONResponse(
                            status_code=403,
                            content={"detail": "Acceso denegado. No tiene autorización para ver comprobantes de otra unidad."}
                        )

    if role in ["owner", "tenant"]:
        allowed_mutations = [
            "/api/auth/login",
            "/api/auth/request-temp-password",
            "/api/auth/reset-password",
            "/api/upload-receipt",
            "/api/whatsapp/webhook",
            "/api/whatsapp/meta-webhook",
            "/api/receipts/resend"
        ]
        is_aliquot_upload = path.startswith("/api/aliquots/") and path.endswith("/upload-receipt")

        if method in ["POST", "PUT", "DELETE", "PATCH"]:
            if path not in allowed_mutations and not is_aliquot_upload:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Acceso denegado. Los inquilinos o propietarios no tienen permisos para realizar modificaciones administrativas en gastos, finanzas o configuración."}
                )

    response = await call_next(request)
    return response

# Import routers
from routers import (
    auth,
    aliquots,
    charges,
    expenses,
    bank,
    reports,
    units,
    settings,
    whatsapp
)

# Register all modular routers
app.include_router(auth.router)
app.include_router(aliquots.router)
app.include_router(charges.router)
app.include_router(expenses.router)
app.include_router(bank.router)
app.include_router(reports.router)
app.include_router(units.router)
app.include_router(settings.router)
app.include_router(whatsapp.router)

# Mount static files
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
def read_root():
    return RedirectResponse(url="/static/index.html")

@app.get("/health")
def health_check():
    db_status = "connected"
    try:
        from sqlmodel import text
        with sm.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"error: {str(e)}"
        
    return {
        "status": "healthy" if db_status == "connected" else "degraded",
        "database": db_status,
        "mode": sm.config.get("app_mode", "produccion"),
        "timestamp": datetime.now().isoformat(),
        "version": "2.0.0"
    }

# ==============================================================================
# Re-exports for backwards compatibility with tests and direct imports
# ==============================================================================
from routers.auth import (
    login,
    request_temp_password,
    reset_password,
    register_admin,
    get_admins
)
from routers.aliquots import (
    get_aliquots,
    emit_aliquots,
    update_aliquot,
    delete_aliquot,
    export_aliquots_csv,
    get_receipt_pdf_by_type,
    get_aliquot_receipt_pdf_alias,
    resend_receipt
)
from routers.charges import (
    get_additional_charges,
    create_additional_charge,
    update_additional_charge,
    delete_additional_charge,
    get_charge_receipt_pdf_alias
)
from routers.expenses import (
    get_expenses,
    create_expense,
    update_expense,
    delete_expense,
    export_expenses_csv,
    get_other_incomes,
    create_other_income,
    update_other_income,
    delete_other_income
)
from routers.bank import (
    get_bank_statement,
    create_bank_transaction,
    update_bank_transaction_endpoint,
    delete_bank_transaction,
    upload_bank_statement,
    reconcile_manual,
    reconcile_pair,
    reconcile_mass,
    undo_payment_route
)
from routers.reports import (
    get_summary,
    get_debtors,
    export_debtors_csv,
    export_financial_pdf,
    get_notification_logs,
    get_reports_preview,
    export_report_pdf,
    export_report_excel
)
from routers.units import (
    get_units,
    create_or_update_unit,
    delete_unit,
    upload_units,
    get_board_members,
    create_board_member,
    delete_board_member,
    download_no_debt_certificate,
    detect_column_mapping
)
from routers.settings import (
    get_settings,
    update_settings,
    test_email_endpoint,
    get_initial_balances,
    update_initial_balances
)
from routers.whatsapp import (
    upload_receipt,
    whatsapp_webhook,
    verify_meta_webhook,
    process_meta_webhook
)
