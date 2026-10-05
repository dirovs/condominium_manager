import os
import json
import shutil
import requests
import io
import csv
from datetime import datetime
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Response, Request
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from sheets_manager import SheetsManager
from ocr_helper import OCRHelper
from receipt_processor import ReceiptProcessor, generate_no_debt_certificate_pdf, FinancialReportPDFGenerator, FinancialReportExcelGenerator
from notifications import NotificationManager, send_smtp_email, build_smtp_message
from sqlmodel import Session, select
from models import User, Unit, Aliquot, AdditionalCharge, BankTransaction, Expense, OtherIncome

app = FastAPI(title="CondoManager API")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def role_access_middleware(request: Request, call_next):
    path = request.url.path
    method = request.method
    role = request.headers.get("X-User-Role", "").strip().lower()

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

# Initialize SheetsManager
sm = SheetsManager()

# Ensure static directories exist
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
RECEIPTS_DIR = os.path.join(STATIC_DIR, "receipts")
UPLOADS_DIR = os.path.join(STATIC_DIR, "uploads")
os.makedirs(RECEIPTS_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

def is_valid_name_for_mismatch_check(name: str) -> bool:
    if not name:
        return False
    name_clean = name.strip().lower()
    if len(name_clean) < 3:
        return False
    if name_clean in ["no", "si", "yo", "de", "la", "el", "ma", "na", "n/a", "none", "null", "no aplica", "no tiene", "-", "sin nombre"]:
        return False
    return True

# Pydantic Schemas
class ExpenseCreate(BaseModel):
    date: str
    category: str
    description: str
    amount: float

class ExpenseUpdate(BaseModel):
    date: str
    category: str
    description: str
    amount: float

class OtherIncomeCreate(BaseModel):
    date: str
    concept: str
    amount: float
    reference: Optional[str] = ""

class OtherIncomeUpdate(BaseModel):
    date: str
    concept: str
    amount: float
    reference: Optional[str] = ""

class ChargeCreate(BaseModel):
    unit: str
    type: str
    description: str
    amount: float
    issue_date: Optional[str] = None

class ChargeUpdate(BaseModel):
    amount: float
    description: str
    reference: Optional[str] = None
    payment_date: Optional[str] = None
    auto_reconcile: Optional[bool] = True

class AliquotUpdate(BaseModel):
    amount: Optional[float] = None
    reference: Optional[str] = None
    payment_date: Optional[str] = None
    auto_reconcile: Optional[bool] = True
    late_fee: Optional[float] = None

class BankTransactionUpdate(BaseModel):
    reference: str
    date: Optional[str] = None
    amount: Optional[float] = None
    detail: Optional[str] = None

class ReconcileManual(BaseModel):
    id: str # Aliquot ID or Charge ID
    type: str # 'aliquot' or 'charge'
    reference: str
    payment_date: str
    late_fee: Optional[float] = 0.0
    amount: Optional[float] = None
    receipt_recipient: Optional[str] = "inquilino"

class UndoPaymentRequest(BaseModel):
    id: str
    type: str  # 'aliquot' or 'charge'

class InitialBalanceUpdate(BaseModel):
    initial_bank_balance: float
    initial_reserve_fund: Optional[float] = 0.0
    cut_off_date: Optional[str] = ""
    description: Optional[str] = "Saldo inicial de apertura de cuentas"
    notes: Optional[str] = ""

class SettingsUpdate(BaseModel):
    spreadsheet_id: str
    google_credentials_json: str
    use_google_sheets: bool
    late_fee_day: int
    late_fee_amount: float
    initial_bank_balance: Optional[float] = 0.0
    default_aliquot_base: Optional[float] = 70.0
    smtp_host: Optional[str] = ""
    smtp_port: Optional[int] = 587
    smtp_user: Optional[str] = ""
    smtp_password: Optional[str] = ""
    smtp_from: Optional[str] = ""
    twilio_sid: Optional[str] = ""
    twilio_token: Optional[str] = ""
    twilio_whatsapp_from: Optional[str] = ""
    whatsapp_provider: Optional[str] = "simulated"
    meta_wa_token: Optional[str] = ""
    meta_wa_phone_number_id: Optional[str] = ""
    meta_wa_verify_token: Optional[str] = ""
    meta_wa_business_account_id: Optional[str] = ""
    directive_president: Optional[str] = ""
    directive_treasurer: Optional[str] = ""
    condo_name: Optional[str] = "Condominio El Mirador"
    condo_address: Optional[str] = ""
    condo_ruc: Optional[str] = ""
    exonerate_directiva: Optional[bool] = False
    receipt_recipient_type: Optional[str] = "inquilino"
    app_mode: Optional[str] = "produccion"
    db_type: Optional[str] = "sqlite"
    db_host: Optional[str] = ""
    db_port: Optional[int] = 5432
    db_user: Optional[str] = ""
    db_password: Optional[str] = ""
    db_name: Optional[str] = ""
    db_custom_url: Optional[str] = ""

class BoardMemberCreate(BaseModel):
    unit_id: str
    name: str
    role: str

class UnitCreate(BaseModel):
    id: str
    owner: str
    cedula_owner: Optional[str] = ""
    phone1: Optional[str] = ""
    email1: Optional[str] = ""
    tenant: Optional[str] = ""
    cedula_tenant: Optional[str] = ""
    phone2: Optional[str] = ""
    email2: Optional[str] = ""
    aliquot_base: Optional[float] = None
    receipt_recipient_type: Optional[str] = "general"

class LoginRequest(BaseModel):
    cedula: str
    email: str
    role: str
    password: Optional[str] = ""

class RequestTempPasswordRequest(BaseModel):
    cedula: str
    email: str
    role: str

class ResetPasswordRequest(BaseModel):
    cedula: str
    role: str
    temp_password: str
    new_password: str

class AdminRegisterRequest(BaseModel):
    cedula: str
    email: str
    name: str
    password: str

class BankTransactionCreate(BaseModel):
    date: str
    reference: str
    amount: float
    detail: str

class ReconcilePair(BaseModel):
    payment_id: str
    payment_type: str
    bank_reference: str
    receipt_recipient: Optional[str] = "inquilino"

class ReceiptResend(BaseModel):
    id: str
    type: str

class AliquotEmitRequest(BaseModel):
    month: str
    year: int
    unit_id: Optional[str] = None
    amount: Optional[float] = None

def save_structured_receipt(file_bytes, filename, unit, year, month):
    month_clean = str(month).strip().capitalize()
    year_clean = str(year).strip()
    dest_dir = os.path.join(UPLOADS_DIR, str(unit), year_clean, month_clean)
    os.makedirs(dest_dir, exist_ok=True)
    
    ext = os.path.splitext(filename)[1].lower()
    import time
    timestamp = int(time.time())
    dest_filename = f"comprobante_{timestamp}{ext}"
    dest_path = os.path.join(dest_dir, dest_filename)
    
    with open(dest_path, "wb") as f:
        f.write(file_bytes)
        
    relative_url = f"/static/uploads/{unit}/{year_clean}/{month_clean}/{dest_filename}"
    return relative_url, dest_path

# Routes
@app.get("/api/summary")
def get_summary():
    return sm.get_summary()

@app.get("/api/aliquots")
def get_aliquots():
    return sm.get_aliquots_with_details()

@app.post("/api/aliquots/emit")
def emit_aliquots(req: AliquotEmitRequest):
    try:
        if req.unit_id and str(req.unit_id).strip().lower() not in ["", "all", "todos"]:
            aliquot = sm.emit_aliquot_individual(
                unit=req.unit_id,
                month=req.month,
                year=req.year,
                amount=req.amount
            )
            return {
                "status": "success",
                "emitted": 1,
                "message": f"Alícuota para el Depto {req.unit_id} ({req.month} {req.year}) generada exitosamente.",
                "aliquot": aliquot
            }
        else:
            count = sm.emit_aliquots_bulk(req.month, req.year)
            if count == 0:
                message = f"Todas las alícuotas para {req.month} {req.year} ya estaban generadas. No se generaron nuevos registros."
            else:
                message = f"Se emitieron {count} alícuotas faltantes para {req.month} {req.year} exitosamente."
            return {"status": "success", "emitted": count, "message": message}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/aliquots/{id}")
def update_aliquot(id: str, req: AliquotUpdate):
    try:
        aliquot = None
        for a in sm.data.get("aliquots", []):
            if str(a.get("id")) == str(id):
                aliquot = a
                break
                
        if not aliquot:
            raise HTTPException(status_code=404, detail="Alícuota no encontrada.")
            
        is_paid = (aliquot.get("status") == "Pagado")
        
        # 1. Update amount if provided
        if req.amount is not None:
            if float(req.amount) < 0:
                raise ValueError("El monto de la alícuota no puede ser negativo.")
            aliquot["amount"] = float(req.amount)
            if aliquot.get("status") != "Pagado":
                aliquot["paid_amount"] = 0.0
            with Session(sm.engine) as session:
                db_a = session.exec(select(Aliquot).where(Aliquot.id == id)).first()
                if db_a:
                    db_a.amount = float(req.amount)
                    session.add(db_a)
                    session.commit()
            sm.sync()
            
        # 2. Update late fee if provided
        if req.late_fee is not None:
            aliquot["late_fee"] = float(req.late_fee)
            with Session(sm.engine) as session:
                db_a = session.exec(select(Aliquot).where(Aliquot.id == id)).first()
                if db_a:
                    db_a.late_fee = float(req.late_fee)
                    session.add(db_a)
                    session.commit()
            sm.sync()
            
        # 3. Update reference if provided
        if req.reference is not None:
            new_ref = str(req.reference).strip()
            aliquot["reference"] = new_ref
            if req.payment_date:
                aliquot["payment_date"] = str(req.payment_date).strip()
                
            # Search for an EXACT match in bank_statement
            bank_match = None
            if new_ref:
                for b in sm.data.get("bank_statement", []):
                    if b.get("reference") and str(b["reference"]).strip().lower() == new_ref.lower() and not b.get("reconciled"):
                        bank_match = b
                        break
                        
            if bank_match and req.auto_reconcile:
                pay_date = bank_match.get("date") or req.payment_date or aliquot.get("payment_date") or datetime.now().strftime("%Y-%m-%d")
                pay_amt = float(bank_match.get("amount") or aliquot.get("amount") or 70.0)
                
                rec_res = sm.reconcile_payment(
                    unit=aliquot["unit"],
                    month=aliquot["month"],
                    year=int(aliquot["year"]),
                    amount=pay_amt,
                    reference=new_ref,
                    payment_date=pay_date,
                    late_fee=req.late_fee,
                    reset_existing=True
                )
                aliquot = rec_res["aliquot"]
                sm.sync()
                
                # Generate receipt PDF
                dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_{aliquot['id']}.pdf")
                receipt_no = f"REC-AL-{aliquot['id']}"
                recipient_name = sm.get_receipt_recipient_name(aliquot["unit"])
                concept = f"Pago de Alícuota Ordinaria - Mes: {aliquot['month']} / {aliquot['year']}"
                
                ReceiptProcessor.generate_receipt_pdf(
                    dest_path=dest_pdf,
                    receipt_no=receipt_no,
                    resident_name=recipient_name,
                    unit=str(aliquot["unit"]),
                    concept=concept,
                    amount=rec_res["aliquot_paid"],
                    late_fee=rec_res["late_fee_paid"],
                    reference=new_ref,
                    payment_date=pay_date,
                    condo_name=sm.config.get("condo_name", "Condominio El Mirador"),
                    abono_deuda=rec_res.get("abono_deuda", 0.0),
                    pago_extra=rec_res.get("pago_extra", 0.0),
                    saldo_deuda=rec_res.get("saldo_deuda", 0.0),
                    advance_aliquots=rec_res.get("advance_aliquots")
                )
                
                return {
                    "status": "success",
                    "reconciled": True,
                    "message": f"Referencia guardada y conciliada automáticamente con la transacción bancaria #{new_ref} por {sm.config.get('currency', '$')}{pay_amt:.2f}.",
                    "aliquot": aliquot,
                    "receipt_url": f"/api/receipts/aliquot/{aliquot['id']}/pdf"
                }
            else:
                if new_ref and not is_paid:
                    aliquot["status"] = "Validación Manual"
                with Session(sm.engine) as session:
                    db_a = session.exec(select(Aliquot).where(Aliquot.id == id)).first()
                    if db_a:
                        db_a.reference = new_ref
                        db_a.status = aliquot["status"]
                        if req.payment_date:
                            db_a.payment_date = req.payment_date
                        if req.amount is not None:
                            db_a.amount = float(req.amount)
                        if req.late_fee is not None:
                            db_a.late_fee = float(req.late_fee)
                        session.add(db_a)
                        session.commit()
                sm.sync()
                
                msg = f"Referencia '{new_ref}' guardada. No se encontró ninguna transacción en el estado de cuenta con esta referencia exacta." if new_ref else "Alícuota actualizada correctamente."
                return {
                    "status": "success",
                    "reconciled": False,
                    "message": msg,
                    "aliquot": aliquot
                }
                
        return {
            "status": "success",
            "reconciled": False,
            "message": "Alícuota actualizada correctamente.",
            "aliquot": aliquot
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/aliquots/{id}")
def delete_aliquot(id: str, request: Request):
    role_header = request.headers.get("X-User-Role", "").strip().lower()
    if role_header != "superadmin":
        raise HTTPException(
            status_code=403,
            detail="Permiso denegado. Solo el Superadministrador tiene permisos para eliminar alícuotas."
        )
    
    success = sm.delete_aliquot(id)
    if not success:
        raise HTTPException(status_code=404, detail="La alícuota especificada no existe o ya fue eliminada.")
        
    return {"status": "success", "message": "Alícuota eliminada exitosamente."}

@app.post("/api/aliquots/{id}/upload-receipt")
async def upload_receipt_for_aliquot(
    id: str,
    file: UploadFile = File(...)
):
    # Find the aliquot
    aliquot = None
    for a in sm.data["aliquots"]:
        if a["id"] == id:
            aliquot = a
            break
    if not aliquot:
        raise HTTPException(status_code=404, detail="Alícuota no encontrada.")
        
    unit_id = aliquot["unit"]
    month = aliquot["month"]
    year = aliquot["year"]
    
    # Verify unit exists
    unit_exists = any(u["id"] == unit_id for u in sm.data["units"])
    if not unit_exists:
        raise HTTPException(status_code=404, detail="El departamento de la alícuota no existe.")
        
    owner_name = next(u["owner"] for u in sm.data["units"] if u["id"] == unit_id)
    recipient_name = sm.get_receipt_recipient_name(unit_id)
    
    # Extract Text from Uploaded File
    file_ext = os.path.splitext(file.filename)[1].lower()
    file_bytes = await file.read()
    
    # Save to structured path
    relative_url, temp_file_path = save_structured_receipt(file_bytes, file.filename, unit_id, year, month)
    
    file_text = ""
    try:
        if file_ext == ".pdf":
            file_text = ReceiptProcessor.extract_text_from_pdf(temp_file_path)
        elif file_ext in [".png", ".jpg", ".jpeg"]:
            file_text = OCRHelper.extract_text(temp_file_path)
    except Exception as e:
        print(f"Error extracting text: {e}")
        
    # Name mismatch validation
    chosen_unit = next((u for u in sm.data["units"] if u["id"] == unit_id), None)
    chosen_names = []
    if chosen_unit.get("owner"):
        chosen_names.append(chosen_unit["owner"])
    if chosen_unit.get("tenant"):
        chosen_names.append(chosen_unit["tenant"])
        
    combined_text = file_text.lower()
    
    # Check if chosen unit's names are mentioned
    chosen_mentioned = False
    for name in chosen_names:
        if not is_valid_name_for_mismatch_check(name):
            continue
        parts = name.split()
        if len(parts) >= 2:
            first = parts[0].lower()
            last = parts[-1].lower()
            if first in combined_text and last in combined_text:
                chosen_mentioned = True
                break
        elif len(parts) == 1:
            first = parts[0].lower()
            if first in combined_text:
                chosen_mentioned = True
                break
                
    # If not mentioned, check if another unit's owner/tenant is mentioned
    mismatch_found = False
    other_entity = None
    if not chosen_mentioned:
        for u in sm.data["units"]:
            if u["id"] == unit_id:
                continue
            u_names = []
            if u.get("owner"):
                u_names.append((u["owner"], "propietario"))
            if u.get("tenant"):
                u_names.append((u["tenant"], "inquilino"))
                
            for name, role in u_names:
                if not is_valid_name_for_mismatch_check(name):
                    continue
                parts = name.split()
                mentioned = False
                if len(parts) >= 2:
                    first = parts[0].lower()
                    last = parts[-1].lower()
                    if first in combined_text and last in combined_text:
                        mentioned = True
                elif len(parts) == 1:
                    first = parts[0].lower()
                    if first in combined_text:
                        mentioned = True
                        
                if mentioned:
                    mismatch_found = True
                    other_entity = (u["id"], name, role)
                    break
            if mismatch_found:
                break
                
    if mismatch_found:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        unit_other, name_other, role_other = other_entity
        return {
            "status": "error",
            "message": f"Discrepancia: El comprobante hace referencia a {name_other} ({role_other} del Depto {unit_other}), pero estás subiendo el comprobante para el Depto {unit_id}."
        }
        
    # Parse details (Amount, Reference, Date)
    extracted_amount, extracted_reference, extracted_date = ReceiptProcessor.parse_bank_receipt_text(file_text)
    if not extracted_amount:
        extracted_amount = float(aliquot["amount"])
    if not extracted_reference:
        extracted_reference = f"SIM-{int(datetime.now().timestamp())}"
        
    # Conciliation against bank statement (Exact reference match)
    bank_match = None
    for b in sm.data.get("bank_statement", []):
        if b.get("reference") and extracted_reference and str(b["reference"]).strip().lower() == str(extracted_reference).strip().lower():
            if abs(b["amount"] - extracted_amount) < 0.01 and not b.get("reconciled", False):
                bank_match = b
                break
                
    payment_date = bank_match["date"] if bank_match else (extracted_date or datetime.now().strftime("%Y-%m-%d"))
        
    aliquot["comprobante_url"] = relative_url
    
    if bank_match:
        rec_result = sm.reconcile_payment(
            unit=unit_id,
            month=aliquot["month"],
            year=int(aliquot["year"]),
            amount=extracted_amount,
            reference=extracted_reference,
            payment_date=payment_date
        )
        aliquot = rec_result["aliquot"]
        aliquot["comprobante_url"] = relative_url
        sm.sync()
        
        # Generate receipt PDF
        receipt_no = f"REC-AL-{aliquot['id']}"
        dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_{aliquot['id']}.pdf")
        concept = f"Pago de Alícuota Ordinaria - Mes: {aliquot['month']} / {aliquot['year']}"
        
        ReceiptProcessor.generate_receipt_pdf(
            dest_path=dest_pdf,
            receipt_no=receipt_no,
            resident_name=recipient_name,
            unit=unit_id,
            concept=concept,
            amount=rec_result["aliquot_paid"],
            late_fee=rec_result["late_fee_paid"],
            reference=extracted_reference,
            payment_date=payment_date,
            abono_deuda=rec_result["abono_deuda"],
            pago_extra=rec_result["pago_extra"],
            saldo_deuda=rec_result["saldo_deuda"],
            advance_aliquots=rec_result.get("advance_aliquots")
        )
        
        try:
            NotificationManager.send_receipt_notifications(
                sm=sm,
                unit_id=unit_id,
                receipt_pdf_path=dest_pdf,
                receipt_no=receipt_no,
                resident_name=recipient_name,
                concept=concept,
                amount=aliquot["amount"] + aliquot["late_fee"],
                payment_date=payment_date,
                reference=extracted_reference
            )
        except Exception as e:
            print(f"Error sending aliquot upload receipt notification: {e}")
            
        late_fee_msg = f" (incluye multa de {sm.config['currency']}{aliquot['late_fee']:.2f} por pago después del día {sm.config['late_fee_day']})" if aliquot["late_fee"] > 0 else ""
        
        return {
            "status": "success",
            "message": f"Comprobante conciliado exitosamente con la transacción bancaria #{extracted_reference} por {sm.config['currency']}{extracted_amount:.2f}{late_fee_msg}.",
            "details": {
                "tipo": "Alícuota Ordinaria",
                "mes": f"{aliquot['month']} / {aliquot['year']}",
                "unidad": unit_id,
                "propietario": owner_name,
                "monto": aliquot["amount"],
                "multa": aliquot["late_fee"],
                "referencia": extracted_reference,
                "fecha_pago": payment_date,
                "conciliacion": "Automática"
            },
            "receipt_url": f"/static/receipts/recibo_{aliquot['id']}.pdf",
            "comprobante_url": relative_url
        }
    else:
        late_fee = sm.check_late_fee(payment_date, aliquot["month"], aliquot["year"])
        aliquot["status"] = "Validación Manual"
        aliquot["reference"] = extracted_reference
        aliquot["payment_date"] = payment_date
        aliquot["late_fee"] = late_fee
        sm.sync()
        
        return {
            "status": "manual_validation",
            "message": f"Comprobante recibido por {sm.config['currency']}{extracted_amount:.2f}, pero la referencia '{extracted_reference}' no coincide con el Estado de Cuenta. Marcado para Validación Manual.",
            "details": {
                "tipo": "Alícuota Ordinaria",
                "mes": f"{aliquot['month']} / {aliquot['year']}",
                "unidad": unit_id,
                "propietario": owner_name,
                "monto": extracted_amount,
                "multa": late_fee,
                "referencia": extracted_reference,
                "fecha_pago": payment_date,
                "conciliacion": "Pendiente de Verificación Manual"
            },
            "comprobante_url": relative_url
        }

@app.get("/api/expenses")
def get_expenses():
    return sm.get_expenses_list()

@app.post("/api/expenses")
def create_expense(expense: ExpenseCreate):
    return sm.add_expense(expense.date, expense.category, expense.description, expense.amount)

@app.get("/api/other-incomes")
def get_other_incomes():
    return sm.data.get("other_incomes", [])

@app.post("/api/other-incomes")
def create_other_income(inc: OtherIncomeCreate):
    return sm.add_other_income(inc.date, inc.concept, inc.amount, inc.reference)

@app.delete("/api/other-incomes/{id}")
def delete_other_income(id: int):
    success = sm.delete_other_income(id)
    if not success:
        raise HTTPException(status_code=404, detail="Ingreso no encontrado")
    return {"status": "success"}

@app.put("/api/other-incomes/{id}")
def update_other_income(id: int, inc: OtherIncomeUpdate):
    updated = sm.update_other_income(id, inc.date, inc.concept, inc.amount, inc.reference)
    if not updated:
        raise HTTPException(status_code=404, detail="Ingreso no encontrado")
    return updated

@app.put("/api/expenses/{id}")
def update_expense(id: str, exp: ExpenseUpdate):
    updated = sm.update_expense(id, exp.date, exp.category, exp.description, exp.amount)
    if not updated:
        raise HTTPException(status_code=404, detail="Gasto no encontrado")
    return updated

@app.delete("/api/expenses/{id}")
def delete_expense(id: str):
    success = sm.delete_expense(id)
    if not success:
        raise HTTPException(status_code=404, detail="Gasto no encontrado")
    return {"status": "success"}

@app.get("/api/board-members")
def get_board_members():
    return sm.data.get("board_members", [])

@app.post("/api/board-members")
def create_board_member(member: BoardMemberCreate):
    return sm.add_board_member(member.unit_id, member.name, member.role)

@app.delete("/api/board-members/{id}")
def delete_board_member(id: int):
    success = sm.delete_board_member(id)
    if not success:
        raise HTTPException(status_code=404, detail="Miembro de la directiva no encontrado")
    return {"status": "success"}

@app.get("/api/debtors")
def get_debtors():
    return sm.get_debtors()

@app.get("/api/additional-charges")
def get_additional_charges():
    return sm.get_additional_charges_with_details()

@app.post("/api/additional-charges")
def create_additional_charge(charge: ChargeCreate):
    return sm.add_additional_charge(charge.unit, charge.type, charge.description, charge.amount, charge.issue_date)

@app.put("/api/additional-charges/{id}")
def update_additional_charge(id: str, req: ChargeUpdate):
    try:
        updated = sm.update_additional_charge(id, req.amount, req.description, req.reference)
        if not updated:
            raise HTTPException(status_code=404, detail="Cargo adicional no encontrado.")
            
        new_ref = str(req.reference or "").strip()
        if new_ref:
            # Check for exact bank match
            bank_match = None
            for b in sm.data.get("bank_statement", []):
                if b.get("reference") and str(b["reference"]).strip().lower() == new_ref.lower() and not b.get("reconciled", False):
                    bank_match = b
                    break
                    
            if bank_match and req.auto_reconcile:
                pay_date = bank_match.get("date") or req.payment_date or datetime.now().strftime("%Y-%m-%d")
                pay_amt = float(bank_match.get("amount") or req.amount or 0.0)
                sm.reconcile_additional_charge(id, new_ref, pay_date, pay_amt)
                
                dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_charge_{id}.pdf")
                receipt_no = f"REC-AC-{id}"
                recipient_name = sm.get_receipt_recipient_name(updated["unit"])
                concept = f"{updated.get('type', 'Cargo')}: {updated.get('description', '')}"
                ReceiptProcessor.generate_receipt_pdf(
                    dest_path=dest_pdf,
                    receipt_no=receipt_no,
                    resident_name=recipient_name,
                    unit=str(updated["unit"]),
                    concept=concept,
                    amount=pay_amt,
                    late_fee=0.0,
                    reference=new_ref,
                    payment_date=pay_date,
                    condo_name=sm.config.get("condo_name", "Condominio El Mirador")
                )
                return {
                    "status": "success",
                    "reconciled": True,
                    "message": f"Cargo adicional conciliado automáticamente con la transacción bancaria #{new_ref}.",
                    "charge": updated,
                    "receipt_url": f"/api/receipts/charge/{id}/pdf"
                }
                
        return {
            "status": "success",
            "reconciled": False,
            "message": "Cargo adicional actualizado correctamente.",
            "charge": updated
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/additional-charges/{id}")
def delete_additional_charge(id: str):
    success = sm.delete_additional_charge(id)
    if not success:
        raise HTTPException(status_code=404, detail="Cargo adicional no encontrado.")
    return {"status": "success"}

@app.get("/api/bank-statement")
def get_bank_statement():
    return sm.get_bank_statement_list()

@app.post("/api/bank-statement-add")
def create_bank_transaction(tx: BankTransactionCreate):
    return sm.add_bank_transaction(tx.date, tx.reference, tx.amount, tx.detail)

@app.put("/api/bank-statement/{old_reference}")
def update_bank_transaction_endpoint(old_reference: str, req: BankTransactionUpdate):
    try:
        old_ref_clean = str(old_reference).strip()
        new_ref = str(req.reference).strip()
        
        # If this transaction was previously matched/reconciled, undo its previous effect
        matched_aliquots = [a for a in sm.data.get("aliquots", []) if a.get("reference") and old_ref_clean.lower() in str(a.get("reference", "")).lower()]
        matched_charges = [c for c in sm.data.get("additional_charges", []) if c.get("reference") and old_ref_clean.lower() in str(c.get("reference", "")).lower()]
        
        for a in matched_aliquots:
            sm.undo_payment(a["id"], "aliquot")
            a["reference"] = new_ref
            
        for c in matched_charges:
            sm.undo_payment(c["id"], "charge")
            c["reference"] = new_ref

        updated_tx = sm.update_bank_transaction(
            old_reference=old_reference,
            new_reference=req.reference,
            date=req.date,
            amount=req.amount,
            detail=req.detail
        )
        if not updated_tx:
            raise HTTPException(status_code=404, detail="Transacción bancaria no encontrada.")
            
        new_ref = str(req.reference).strip()
        # Check if there is an unreconciled aliquot with this exact reference
        aliquot_match = None
        for a in sm.data.get("aliquots", []):
            if a.get("status") != "Pagado" and a.get("reference") and str(a["reference"]).strip().lower() == new_ref.lower():
                aliquot_match = a
                break
                
        if aliquot_match:
            rec_date = updated_tx.get("date") or aliquot_match.get("payment_date") or datetime.now().strftime("%Y-%m-%d")
            rec_amt = float(updated_tx.get("amount") or aliquot_match.get("amount") or 70.0)
            rec_res = sm.reconcile_payment(
                unit=aliquot_match["unit"],
                month=aliquot_match["month"],
                year=int(aliquot_match["year"]),
                amount=rec_amt,
                reference=new_ref,
                payment_date=rec_date,
                late_fee=aliquot_match.get("late_fee"),
                reset_existing=True
            )
            # Generate receipt PDF
            dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_{aliquot_match['id']}.pdf")
            receipt_no = f"REC-AL-{aliquot_match['id']}"
            recipient_name = sm.get_receipt_recipient_name(aliquot_match["unit"])
            concept = f"Pago de Alícuota Ordinaria - Mes: {aliquot_match['month']} / {aliquot_match['year']}"
            ReceiptProcessor.generate_receipt_pdf(
                dest_path=dest_pdf,
                receipt_no=receipt_no,
                resident_name=recipient_name,
                unit=str(aliquot_match["unit"]),
                concept=concept,
                amount=rec_res["aliquot_paid"],
                late_fee=rec_res["late_fee_paid"],
                reference=new_ref,
                payment_date=rec_date,
                condo_name=sm.config.get("condo_name", "Condominio El Mirador"),
                abono_deuda=rec_res.get("abono_deuda", 0.0),
                pago_extra=rec_res.get("pago_extra", 0.0),
                saldo_deuda=rec_res.get("saldo_deuda", 0.0),
                advance_aliquots=rec_res.get("advance_aliquots")
            )
            return {
                "status": "success",
                "reconciled": True,
                "message": f"Transacción actualizada y conciliada automáticamente con la alícuota del Depto {aliquot_match['unit']} ({aliquot_match['month']})!",
                "tx": updated_tx,
                "aliquot": aliquot_match,
                "receipt_url": f"/api/receipts/aliquot/{aliquot_match['id']}/pdf"
            }
            
        # Also check additional charges
        charge_match = None
        for c in sm.data.get("additional_charges", []):
            if c.get("status") != "Pagado" and c.get("reference") and str(c["reference"]).strip().lower() == new_ref.lower():
                charge_match = c
                break
                
        if charge_match:
            rec_date = updated_tx.get("date") or charge_match.get("payment_date") or datetime.now().strftime("%Y-%m-%d")
            rec_amt = float(updated_tx.get("amount") or charge_match.get("amount") or 0.0)
            sm.reconcile_additional_charge(charge_match["id"], new_ref, rec_date, rec_amt)
            dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_charge_{charge_match['id']}.pdf")
            receipt_no = f"REC-AC-{charge_match['id']}"
            recipient_name = sm.get_receipt_recipient_name(charge_match["unit"])
            concept = f"{charge_match.get('type', 'Cargo')}: {charge_match.get('description', '')}"
            ReceiptProcessor.generate_receipt_pdf(
                dest_path=dest_pdf,
                receipt_no=receipt_no,
                resident_name=recipient_name,
                unit=str(charge_match["unit"]),
                concept=concept,
                amount=rec_amt,
                late_fee=0.0,
                reference=new_ref,
                payment_date=rec_date,
                condo_name=sm.config.get("condo_name", "Condominio El Mirador")
            )
            return {
                "status": "success",
                "reconciled": True,
                "message": f"Transacción actualizada y conciliada automáticamente con el cargo del Depto {charge_match['unit']}!",
                "tx": updated_tx,
                "charge": charge_match,
                "receipt_url": f"/api/receipts/charge/{charge_match['id']}/pdf"
            }
            
        return {
            "status": "success",
            "reconciled": False,
            "message": "Transacción bancaria actualizada exitosamente.",
            "tx": updated_tx
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/settings")
def get_settings():
    # Mask credentials for security but let them check if it exists
    cfg = sm.config.copy()
    cfg["google_credentials_json_configured"] = bool(cfg.get("google_credentials_json"))
    cfg["smtp_password_configured"] = bool(cfg.get("smtp_password"))
    cfg["twilio_token_configured"] = bool(cfg.get("twilio_token"))
    cfg["meta_wa_token_configured"] = bool(cfg.get("meta_wa_token"))
    cfg["meta_wa_verify_token_configured"] = bool(cfg.get("meta_wa_verify_token"))
    cfg["db_password_configured"] = bool(cfg.get("db_password"))
    return cfg

@app.post("/api/settings")
def update_settings(settings: SettingsUpdate):
    try:
        # If google_credentials_json is provided as raw text, validate it
        if settings.google_credentials_json and settings.google_credentials_json != "******":
            json.loads(settings.google_credentials_json) # simple syntax check
    except Exception as e:
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

class EmailTestRequest(BaseModel):
    recipient_email: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from: Optional[str] = None

@app.post("/api/settings/test-email")
def test_email_endpoint(req: Optional[EmailTestRequest] = None):
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

@app.get("/api/initial-balances")
def get_initial_balances():
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

@app.post("/api/initial-balances")
def update_initial_balances(req: InitialBalanceUpdate):
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

@app.get("/api/units")
def get_units():
    return sm.data["units"]

@app.post("/api/units")
def create_or_update_unit(unit: UnitCreate):
    return sm.add_or_update_unit(
        unit_id=unit.id,
        owner=unit.owner,
        phone1=unit.phone1,
        email1=unit.email1,
        tenant=unit.tenant,
        phone2=unit.phone2,
        email2=unit.email2,
        aliquot_base=unit.aliquot_base,
        cedula_owner=unit.cedula_owner,
        cedula_tenant=unit.cedula_tenant,
        receipt_recipient_type=unit.receipt_recipient_type
    )

def detect_column_mapping(header_row, is_xlsx=False):
    col_mapping = {}
    start_idx = 1 if is_xlsx else 0
    for idx, val in enumerate(header_row):
        col_idx = idx + start_idx
        val = str(val or "").strip().lower()
        
        # 1. ID / Departamento
        if val == "id" or any(k in val for k in ["departamento", "depto", "unidad", "unit", "nro de d", "nro. de d", "numero de d", "número de d"]):
            col_mapping["id"] = col_idx
            
        # 2. Cédula checks
        elif any(k in val for k in ["cedula", "cédula", "nro. doc", "documento"]):
            if any(k in val for k in ["inquilino", "tenant", "residente"]):
                col_mapping["cedula_tenant"] = col_idx
            else:
                col_mapping["cedula_owner"] = col_idx
                
        # 3. Email / Correo checks
        elif any(k in val for k in ["mail", "email", "correo"]):
            if any(k in val for k in ["inquilino", "tenant", "residente"]):
                col_mapping["email2"] = col_idx
            else:
                col_mapping["email1"] = col_idx
                
        # 4. Phone / Teléfono checks
        elif val == "tel" or any(k in val for k in ["telef", "phone", "celular", "tlf"]):
            if any(k in val for k in ["inquilino", "tenant", "residente"]):
                col_mapping["phone2"] = col_idx
            else:
                col_mapping["phone1"] = col_idx
                
        # 5. Base Role check: Tenant (more specific than Owner/generic names)
        elif any(k in val for k in ["inquilino", "tenant"]):
            col_mapping["tenant"] = col_idx
            
        # 6. Base Role check: Owner
        elif any(k in val for k in ["propietario", "owner", "copropietario", "dueño", "dueno", "propietaria"]):
            col_mapping["owner"] = col_idx
            
        # 7. Aliquot base check
        elif any(k in val for k in ["alicuotabase", "alícuota base", "aliquot_base", "alicuota", "base"]):
            col_mapping["aliquot_base"] = col_idx
            
        # 8. Generic fallback for names
        elif any(k in val for k in ["nombre", "name", "nombres", "apellidos", "residente"]):
            if "residente" in val and "tenant" not in col_mapping:
                col_mapping["tenant"] = col_idx
            elif "owner" not in col_mapping:
                col_mapping["owner"] = col_idx
                
    # Fallback to defaults if ID or Owner is not detected
    if "id" not in col_mapping or "owner" not in col_mapping:
        if is_xlsx:
            col_mapping = {
                "id": 1, "owner": 2, "phone1": 3, "email1": 4,
                "tenant": 5, "phone2": 6, "email2": 7, "aliquot_base": 8
            }
        else:
            col_mapping = {
                "id": 0, "owner": 1, "phone1": 2, "email1": 3,
                "tenant": 4, "phone2": 5, "email2": 6, "aliquot_base": 7
            }
        
    return col_mapping

@app.delete("/api/units/{unit_id}")
def delete_unit(unit_id: str):
    success = sm.delete_unit(unit_id)
    if not success:
        raise HTTPException(status_code=404, detail="Departamento no encontrado.")
    return {"status": "success", "message": f"Departamento {unit_id} eliminado correctamente."}

@app.post("/api/upload-units")
async def upload_units(file: UploadFile = File(...)):
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in [".csv", ".xlsx"]:
        raise HTTPException(status_code=400, detail="Formato de archivo no soportado. Use CSV o XLSX.")
        
    temp_path = os.path.join(UPLOADS_DIR, f"units_{datetime.now().timestamp()}{file_ext}")
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    try:
        units = []
        if file_ext == ".csv":
            import csv
            with open(temp_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            lines = content.splitlines()
            if not lines:
                return {"status": "success", "added": 0, "message": "Archivo vacío."}
            
            sep = ","
            if ";" in lines[0] and lines[0].count(";") > lines[0].count(","):
                sep = ";"
                
            reader = csv.reader(lines, delimiter=sep)
            rows = list(reader)
            if not rows:
                return {"status": "success", "added": 0, "message": "Archivo vacío."}
            
            # Find headers and detect mapping
            header_row = [val.strip().lower() for val in rows[0]]
            col_mapping = detect_column_mapping(header_row, is_xlsx=False)
                
            for r in rows[1:]:
                if not r or len(r) <= max(col_mapping.get("id", 0), col_mapping.get("owner", 1)):
                    continue
                unit_id = r[col_mapping["id"]].strip()
                owner = r[col_mapping["owner"]].strip()
                if not unit_id or not owner:
                    continue
                
                # Safely extract optional columns
                def get_col_val(col_name):
                    idx = col_mapping.get(col_name)
                    if idx is not None and idx < len(r):
                        return r[idx].strip()
                    return ""
                
                phone1 = get_col_val("phone1")
                email1 = get_col_val("email1")
                tenant = get_col_val("tenant")
                phone2 = get_col_val("phone2")
                email2 = get_col_val("email2")
                
                aliquot_base = None
                if "aliquot_base" in col_mapping:
                    val = get_col_val("aliquot_base")
                    if val:
                        try:
                            aliquot_base = float(val.replace("$", "").replace(",", "").strip())
                        except:
                            try:
                                aliquot_base = float(val.replace("$", "").replace(".", "").replace(",", ".").strip())
                            except:
                                aliquot_base = None
                            
                cedula_owner = get_col_val("cedula_owner")
                cedula_tenant = get_col_val("cedula_tenant")
                
                units.append({
                    "id": unit_id, "owner": owner, "phone1": phone1, "email1": email1,
                    "tenant": tenant, "phone2": phone2, "email2": email2, "aliquot_base": aliquot_base,
                    "cedula_owner": cedula_owner, "cedula_tenant": cedula_tenant
                })
        else: # XLSX
            import openpyxl
            wb = openpyxl.load_workbook(temp_path, data_only=True)
            sheet = wb.active
            
            # Find headers and detect mapping
            header_row = [sheet.cell(1, col_idx).value for col_idx in range(1, sheet.max_column + 1)]
            col_mapping = detect_column_mapping(header_row, is_xlsx=True)
                
            for r_idx in range(2, sheet.max_row + 1):
                unit_id = str(sheet.cell(r_idx, col_mapping["id"]).value or "").strip()
                owner = str(sheet.cell(r_idx, col_mapping["owner"]).value or "").strip()
                if not unit_id or not owner:
                    continue
                    
                phone1 = str(sheet.cell(r_idx, col_mapping["phone1"]).value or "").strip() if "phone1" in col_mapping else ""
                email1 = str(sheet.cell(r_idx, col_mapping["email1"]).value or "").strip() if "email1" in col_mapping else ""
                tenant = str(sheet.cell(r_idx, col_mapping["tenant"]).value or "").strip() if "tenant" in col_mapping else ""
                phone2 = str(sheet.cell(r_idx, col_mapping["phone2"]).value or "").strip() if "phone2" in col_mapping else ""
                email2 = str(sheet.cell(r_idx, col_mapping["email2"]).value or "").strip() if "email2" in col_mapping else ""
                
                aliquot_base = None
                if "aliquot_base" in col_mapping:
                    raw_aliquot = sheet.cell(r_idx, col_mapping["aliquot_base"]).value
                    if raw_aliquot is not None:
                        try:
                            aliquot_base = float(raw_aliquot)
                        except:
                            aliquot_base = None
                        
                cedula_owner = str(sheet.cell(r_idx, col_mapping["cedula_owner"]).value or "").strip() if "cedula_owner" in col_mapping else ""
                cedula_tenant = str(sheet.cell(r_idx, col_mapping["cedula_tenant"]).value or "").strip() if "cedula_tenant" in col_mapping else ""
                
                units.append({
                    "id": unit_id, "owner": owner, "phone1": phone1, "email1": email1,
                    "tenant": tenant, "phone2": phone2, "email2": email2, "aliquot_base": aliquot_base,
                    "cedula_owner": cedula_owner, "cedula_tenant": cedula_tenant
                })
            wb.close()
            
        # Add to SheetsManager
        added = 0
        for u in units:
            sm.add_or_update_unit(
                unit_id=u["id"],
                owner=u["owner"],
                phone1=u["phone1"],
                email1=u["email1"],
                tenant=u["tenant"],
                phone2=u["phone2"],
                email2=u["email2"],
                aliquot_base=u["aliquot_base"],
                cedula_owner=u.get("cedula_owner", ""),
                cedula_tenant=u.get("cedula_tenant", "")
            )
            added += 1
            
        return {
            "status": "success",
            "message": f"Se procesaron e importaron {added} departamentos correctamente.",
            "added": added
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al procesar el archivo de departamentos: {e}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

@app.post("/api/reconcile-manual")
def reconcile_manual(req: ReconcileManual):
    if req.type == "aliquot":
        # Check if the reference matches bank statement to mark it
        for b in sm.data["bank_statement"]:
            if b["reference"] == req.reference:
                b["reconciled"] = True
                break
                
        # Parse unit, year, month from aliquot id (format: unit-year-month)
        parts = req.id.split("-")
        unit_id = parts[0]
        year = int(parts[1])
        month = parts[2]
        
        # Look up current aliquot record to default amount
        aliquot_rec = None
        for a in sm.data["aliquots"]:
            if a["id"] == req.id:
                aliquot_rec = a
                break
                
        amt_to_use = req.amount
        reset_existing = False
        if aliquot_rec:
            if aliquot_rec.get("paid_amount", 0.0) > 0 or aliquot_rec.get("reference"):
                reset_existing = True
            if amt_to_use is None:
                base_due = float(aliquot_rec.get("amount", 0.0)) + float(aliquot_rec.get("paid_amount", 0.0))
                amt_to_use = base_due + float(req.late_fee or 0.0)
        elif amt_to_use is None:
            amt_to_use = 70.0 + float(req.late_fee or 0.0)
            
        rec_result = sm.reconcile_payment(
            unit=unit_id,
            month=month,
            year=year,
            amount=amt_to_use,
            reference=req.reference,
            payment_date=req.payment_date,
            late_fee=req.late_fee,
            recipient_override=req.receipt_recipient,
            reset_existing=reset_existing
        )
        
        if rec_result:
            aliquot = rec_result["aliquot"]
            # Generate receipt PDF
            receipt_no = f"REC-AL-{aliquot['id']}"
            resident_name = sm.get_receipt_recipient_name(aliquot["unit"], req.receipt_recipient)
            dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_{aliquot['id']}.pdf")
            concept = f"Pago de Alícuota Ordinaria - Mes: {aliquot['month']} / {aliquot['year']}"
            
            ReceiptProcessor.generate_receipt_pdf(
                dest_path=dest_pdf,
                receipt_no=receipt_no,
                resident_name=resident_name,
                unit=aliquot["unit"],
                concept=concept,
                amount=rec_result["aliquot_paid"],
                late_fee=rec_result["late_fee_paid"],
                reference=req.reference,
                payment_date=req.payment_date,
                abono_deuda=rec_result["abono_deuda"],
                pago_extra=rec_result["pago_extra"],
                saldo_deuda=rec_result["saldo_deuda"],
                advance_aliquots=rec_result.get("advance_aliquots")
            )
            
            try:
                NotificationManager.send_receipt_notifications(
                    sm=sm,
                    unit_id=aliquot["unit"],
                    receipt_pdf_path=dest_pdf,
                    receipt_no=receipt_no,
                    resident_name=resident_name,
                    concept=concept,
                    amount=rec_result["aliquot_paid"] + rec_result["late_fee_paid"] + rec_result["abono_deuda"] + rec_result["pago_extra"],
                    payment_date=req.payment_date,
                    reference=req.reference
                )
            except Exception as e:
                print(f"Error sending manual notification: {e}")
                
            return {"status": "success", "aliquot": aliquot, "receipt_url": f"/static/receipts/recibo_{aliquot['id']}.pdf"}
        raise HTTPException(status_code=404, detail="Aliquot record not found or could not be reconciled.")
        
    elif req.type == "charge":
        # Look up current charge record to default amount
        charge_rec = None
        for c in sm.data["additional_charges"]:
            if c["id"] == req.id:
                charge_rec = c
                break
                
        amt_to_use = req.amount
        if amt_to_use is None:
            amt_to_use = charge_rec["amount"] if charge_rec else 0.0
            
        rec_result = sm.reconcile_additional_charge(req.id, req.reference, req.payment_date, amount=amt_to_use, recipient_override=req.receipt_recipient)
        if rec_result:
            charge = rec_result["charge"]
            # Generate receipt PDF
            receipt_no = f"REC-AC-{charge['id']}"
            resident_name = sm.get_receipt_recipient_name(charge["unit"], req.receipt_recipient)
            dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_charge_{charge['id']}.pdf")
            concept = f"{charge['type']}: {charge['description']}"
            
            ReceiptProcessor.generate_receipt_pdf(
                dest_path=dest_pdf,
                receipt_no=receipt_no,
                resident_name=resident_name,
                unit=charge["unit"],
                concept=concept,
                amount=0.0,
                late_fee=0.0,
                reference=req.reference,
                payment_date=req.payment_date,
                abono_deuda=rec_result["abono_deuda"],
                pago_extra=rec_result["pago_extra"],
                saldo_deuda=rec_result["saldo_deuda"]
            )
            
            try:
                NotificationManager.send_receipt_notifications(
                    sm=sm,
                    unit_id=charge["unit"],
                    receipt_pdf_path=dest_pdf,
                    receipt_no=receipt_no,
                    resident_name=resident_name,
                    concept=concept,
                    amount=rec_result["abono_deuda"] + rec_result["pago_extra"],
                    payment_date=req.payment_date,
                    reference=req.reference
                )
            except Exception as e:
                print(f"Error sending manual notification: {e}")
                
            return {"status": "success", "charge": charge, "receipt_url": f"/static/receipts/recibo_charge_{charge['id']}.pdf"}
        raise HTTPException(status_code=404, detail="Additional charge record not found or could not be reconciled.")

@app.post("/api/undo-payment")
def undo_payment_route(req: UndoPaymentRequest):
    success = sm.undo_payment(req.id, req.type)
    if success:
        return {"status": "success", "message": "Pago eliminado/deshecho correctamente."}
    raise HTTPException(status_code=400, detail="No se pudo eliminar/deshacer el pago.")

@app.post("/api/upload-receipt")
async def upload_receipt(
    message: str = Form(""),
    file: Optional[UploadFile] = File(None),
    unit: Optional[str] = Form(None)
):
    # 1. Parse WhatsApp Message
    parsed_msg = ReceiptProcessor.parse_whatsapp_message(message)
    
    # If unit is explicitly selected in manual form, use it
    if unit:
        parsed_msg["unit"] = unit
        
    # Check if we at least parsed a unit
    if not parsed_msg["unit"]:
        return {
            "status": "error",
            "message": "No se pudo identificar el Departamento o Unidad en el mensaje de WhatsApp. Por favor, asegúrate de incluir el número de departamento (ej. 'Departamento: 701')."
        }
        
    if not parsed_msg["month"]:
        # Fallback to current month if not specified
        months = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
        parsed_msg["month"] = months[datetime.now().month - 1]

    # Validate unit exists
    unit_id = parsed_msg["unit"]
    unit_exists = any(u["id"] == unit_id for u in sm.data["units"])
    if not unit_exists:
        return {
            "status": "error",
            "message": f"El departamento '{unit_id}' especificado en el mensaje no existe en la base de datos de condóminos."
        }

    # Retrieve owner name
    owner_name = next(u["owner"] for u in sm.data["units"] if u["id"] == unit_id)
    recipient_name = sm.get_receipt_recipient_name(unit_id)
    
    # 2. Extract Text from Uploaded File (if present)
    file_text = ""
    relative_url = ""
    if file:
        file_ext = os.path.splitext(file.filename)[1].lower()
        file_bytes = await file.read()
        
        # Determine year
        year = parsed_msg.get("year") or datetime.now().year
        month = parsed_msg.get("month")
        
        relative_url, dest_path = save_structured_receipt(file_bytes, file.filename, unit_id, year, month)
        
        try:
            if file_ext == ".pdf":
                file_text = ReceiptProcessor.extract_text_from_pdf(dest_path)
            elif file_ext in [".png", ".jpg", ".jpeg"]:
                file_text = OCRHelper.extract_text(dest_path)
            else:
                file_text = ""
        except Exception as e:
            print(f"Error extracting text: {e}")
            
    # If no file was uploaded, check if details are written inside the text
    if not file_text:
        # Fallback to parsing the WhatsApp message body for receipt details (simulated case)
        file_text = message

    # 2b. Mismatch check: chosen unit vs parsed data (Propietario / Inquilino)
    if unit:
        # Check if the parsed unit from text itself differs
        temp_parsed = ReceiptProcessor.parse_whatsapp_message(message)
        if temp_parsed["unit"] and temp_parsed["unit"] != unit:
            if relative_url and os.path.exists(os.path.join(STATIC_DIR, relative_url.lstrip('/'))):
                try:
                    os.remove(os.path.join(STATIC_DIR, relative_url.lstrip('/')))
                except Exception as e:
                    print(f"Error removing file after mismatch: {e}")
            return {
                "status": "error",
                "message": f"Discrepancia: El mensaje de WhatsApp indica Departamento {temp_parsed['unit']}, pero seleccionaste el Depto {unit}."
            }
            
        chosen_unit = next((u for u in sm.data["units"] if u["id"] == unit), None)
        chosen_names = []
        if chosen_unit.get("owner"):
            chosen_names.append(chosen_unit["owner"])
        if chosen_unit.get("tenant"):
            chosen_names.append(chosen_unit["tenant"])
            
        combined_text = (message + " " + file_text).lower()
        
        # Check if chosen unit's names are mentioned
        chosen_mentioned = False
        for name in chosen_names:
            if not is_valid_name_for_mismatch_check(name):
                continue
            parts = name.split()
            if len(parts) >= 2:
                first = parts[0].lower()
                last = parts[-1].lower()
                if first in combined_text and last in combined_text:
                    chosen_mentioned = True
                    break
            elif len(parts) == 1:
                first = parts[0].lower()
                if first in combined_text:
                    chosen_mentioned = True
                    break
                    
        # If not mentioned, check if another unit's owner/tenant is mentioned
        mismatch_found = False
        other_entity = None
        if not chosen_mentioned:
            for u in sm.data["units"]:
                if u["id"] == unit:
                    continue
                u_names = []
                if u.get("owner"):
                    u_names.append((u["owner"], "propietario"))
                if u.get("tenant"):
                    u_names.append((u["tenant"], "inquilino"))
                    
                for name, role in u_names:
                    if not is_valid_name_for_mismatch_check(name):
                        continue
                    parts = name.split()
                    mentioned = False
                    if len(parts) >= 2:
                        first = parts[0].lower()
                        last = parts[-1].lower()
                        if first in combined_text and last in combined_text:
                            mentioned = True
                    elif len(parts) == 1:
                        first = parts[0].lower()
                        if first in combined_text:
                            mentioned = True
                            
                    if mentioned:
                        mismatch_found = True
                        other_entity = (u["id"], name, role)
                        break
                if mismatch_found:
                    break
                    
        if mismatch_found:
            if relative_url and os.path.exists(os.path.join(STATIC_DIR, relative_url.lstrip('/'))):
                try:
                    os.remove(os.path.join(STATIC_DIR, relative_url.lstrip('/')))
                except Exception as e:
                    print(f"Error removing file after mismatch: {e}")
            unit_id, name, role = other_entity
            return {
                "status": "error",
                "message": f"Discrepancia: El comprobante o mensaje hace referencia a {name} ({role} del Depto {unit_id}), pero seleccionaste el Depto {unit}."
            }

    # 3. Parse receipt details (Amount, Reference, Date)
    extracted_amount, extracted_reference, extracted_date = ReceiptProcessor.parse_bank_receipt_text(file_text)
    
    # Fallback/simulation matching if not parsed
    if not extracted_amount:
        # Fall back to the global default aliquot base
        extracted_amount = float(sm.config.get("default_aliquot_base", 70.0))
    if not extracted_reference:
        # Fallback reference matching Jenny's cedula from WhatsApp text if available
        extracted_reference = parsed_msg["cedula"].split("-")[0] if parsed_msg["cedula"] else f"SIM-{int(datetime.now().timestamp())}"

    # 4. Conciliation against Bank Statement (Estado de Cuenta - Exact reference match)
    bank_match = None
    for b in sm.data.get("bank_statement", []):
        if b.get("reference") and extracted_reference and str(b["reference"]).strip().lower() == str(extracted_reference).strip().lower():
            if abs(b["amount"] - extracted_amount) < 0.01 and not b.get("reconciled", False):
                bank_match = b
                break

    # Also handle additional charges matching
    is_additional_charge = False
    matching_charge = None
    
    for c in sm.data["additional_charges"]:
        if c["unit"] == unit_id and c["status"] == "Pendiente":
            if c["type"].lower() in message.lower() or any(keyword in message.lower() for keyword in c["description"].lower().split()):
                is_additional_charge = True
                matching_charge = c
                break

    payment_date = bank_match["date"] if bank_match else (extracted_date or datetime.now().strftime("%Y-%m-%d"))

    if is_additional_charge and matching_charge:
        # 4a. Process additional charge payment
        if bank_match:
            # Reconcile additional charge
            sm.reconcile_additional_charge(matching_charge["id"], extracted_reference, payment_date)
            matching_charge["comprobante_url"] = relative_url
            sm.sync()
            
            # Generate receipt PDF
            receipt_no = f"REC-AC-{matching_charge['id']}"
            dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_charge_{matching_charge['id']}.pdf")
            concept = f"{matching_charge['type']}: {matching_charge['description']}"
            
            ReceiptProcessor.generate_receipt_pdf(
                dest_path=dest_pdf,
                receipt_no=receipt_no,
                resident_name=recipient_name,
                unit=unit_id,
                concept=concept,
                amount=matching_charge["amount"],
                late_fee=0.0,
                reference=extracted_reference,
                payment_date=payment_date
            )
            
            try:
                NotificationManager.send_receipt_notifications(
                    sm=sm,
                    unit_id=unit_id,
                    receipt_pdf_path=dest_pdf,
                    receipt_no=receipt_no,
                    resident_name=recipient_name,
                    concept=concept,
                    amount=matching_charge["amount"],
                    payment_date=payment_date,
                    reference=extracted_reference
                )
            except Exception as e:
                print(f"Error sending upload-receipt charge notification: {e}")
                
            return {
                "status": "success",
                "message": f"Comprobante conciliado exitosamente con la transacción bancaria #{extracted_reference} por {sm.config['currency']}{extracted_amount:.2f}.",
                "details": {
                    "tipo": "Cargo Extraordinario / Multa",
                    "descripcion": matching_charge["description"],
                    "unidad": unit_id,
                    "propietario": owner_name,
                    "monto": matching_charge["amount"],
                    "referencia": extracted_reference,
                    "fecha_pago": payment_date,
                    "conciliacion": "Automática"
                },
                "receipt_url": f"/static/receipts/recibo_charge_{matching_charge['id']}.pdf",
                "comprobante_url": relative_url
            }
        else:
            # Bank statement transaction not found: Mark for Manual Validation
            matching_charge["status"] = "Validación Manual"
            matching_charge["reference"] = extracted_reference
            matching_charge["payment_date"] = payment_date
            matching_charge["comprobante_url"] = relative_url
            sm.sync()
            
            return {
                "status": "manual_validation",
                "message": f"Comprobante recibido por {sm.config['currency']}{extracted_amount:.2f}, pero la referencia '{extracted_reference}' no coincide con el Estado de Cuenta. Registrado para Validación Manual.",
                "details": {
                    "tipo": "Cargo Extraordinario / Multa",
                    "descripcion": matching_charge["description"],
                    "unidad": unit_id,
                    "propietario": owner_name,
                    "monto": matching_charge["amount"],
                    "referencia": extracted_reference,
                    "fecha_pago": payment_date,
                    "conciliacion": "Pendiente de Verificación Manual"
                },
                "comprobante_url": relative_url
            }

    else:
        # 4b. Process Aliquot Payment
        if bank_match:
            # Reconcile aliquot
            rec_result = sm.reconcile_payment(
                unit=unit_id,
                month=parsed_msg["month"],
                year=parsed_msg["year"],
                amount=extracted_amount,
                reference=extracted_reference,
                payment_date=payment_date
            )
            aliquot = rec_result["aliquot"]
            aliquot["comprobante_url"] = relative_url
            sm.sync()
            
            # Generate receipt PDF
            receipt_no = f"REC-AL-{aliquot['id']}"
            dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_{aliquot['id']}.pdf")
            concept = f"Pago de Alícuota Ordinaria - Mes: {aliquot['month']} / {aliquot['year']}"
            
            ReceiptProcessor.generate_receipt_pdf(
                dest_path=dest_pdf,
                receipt_no=receipt_no,
                resident_name=recipient_name,
                unit=unit_id,
                concept=concept,
                amount=rec_result["aliquot_paid"],
                late_fee=rec_result["late_fee_paid"],
                reference=extracted_reference,
                payment_date=payment_date,
                abono_deuda=rec_result["abono_deuda"],
                pago_extra=rec_result["pago_extra"],
                saldo_deuda=rec_result["saldo_deuda"],
                advance_aliquots=rec_result.get("advance_aliquots")
            )
            
            try:
                NotificationManager.send_receipt_notifications(
                    sm=sm,
                    unit_id=unit_id,
                    receipt_pdf_path=dest_pdf,
                    receipt_no=receipt_no,
                    resident_name=recipient_name,
                    concept=concept,
                    amount=rec_result["aliquot_paid"] + rec_result["late_fee_paid"] + rec_result["abono_deuda"] + rec_result["pago_extra"],
                    payment_date=payment_date,
                    reference=extracted_reference
                )
            except Exception as e:
                print(f"Error sending upload-receipt aliquot notification: {e}")
                
            late_fee_msg = f" (incluye multa de {sm.config['currency']}{rec_result['late_fee_paid']:.2f} por pago después del día {sm.config['late_fee_day']})" if rec_result["late_fee_paid"] > 0 else ""
            
            return {
                "status": "success",
                "message": f"Comprobante conciliado exitosamente con la transacción bancaria #{extracted_reference} por {sm.config['currency']}{extracted_amount:.2f}{late_fee_msg}.",
                "details": {
                    "tipo": "Alícuota Ordinaria",
                    "mes": f"{aliquot['month']} / {aliquot['year']}",
                    "unidad": unit_id,
                    "propietario": owner_name,
                    "monto": rec_result["aliquot_paid"],
                    "multa": rec_result["late_fee_paid"],
                    "referencia": extracted_reference,
                    "fecha_pago": payment_date,
                    "conciliacion": "Automática"
                },
                "receipt_url": f"/static/receipts/recibo_{aliquot['id']}.pdf",
                "comprobante_url": relative_url
            }
        else:
            # Bank statement transaction not found: Mark for Manual Validation
            aliquot_id = f"{unit_id}-{parsed_msg['year']}-{parsed_msg['month']}"
            
            # Check if aliquot already exists in list, else create it
            aliquot = None
            for a in sm.data["aliquots"]:
                if a["unit"] == unit_id and a["month"].lower() == parsed_msg["month"].lower() and int(a["year"]) == int(parsed_msg["year"]):
                    aliquot = a
                    break
            
            # Apply check for late fee if we do manual validation, based on today's date
            late_fee = sm.check_late_fee(payment_date, parsed_msg["month"], parsed_msg["year"])
            
            if aliquot:
                aliquot["status"] = "Validación Manual"
                aliquot["reference"] = extracted_reference
                aliquot["payment_date"] = payment_date
                aliquot["late_fee"] = late_fee
                aliquot["comprobante_url"] = relative_url
            else:
                aliquot = {
                    "id": aliquot_id,
                    "unit": unit_id,
                    "month": parsed_msg["month"],
                    "year": parsed_msg["year"],
                    "amount": extracted_amount,
                    "late_fee": late_fee,
                    "payment_date": payment_date,
                    "reference": extracted_reference,
                    "status": "Validación Manual",
                    "comprobante_url": relative_url
                }
                sm.data["aliquots"].append(aliquot)
                
            sm.sync()
            
            return {
                "status": "manual_validation",
                "message": f"Comprobante recibido por {sm.config['currency']}{extracted_amount:.2f}, pero la referencia bancaria '{extracted_reference}' no se encontró en el Estado de Cuenta. Marcado para Validación Manual.",
                "details": {
                    "tipo": "Alícuota Ordinaria",
                    "mes": f"{parsed_msg['month']} / {parsed_msg['year']}",
                    "unidad": unit_id,
                    "propietario": owner_name,
                    "monto": extracted_amount,
                    "multa": late_fee,
                    "referencia": extracted_reference,
                    "fecha_pago": payment_date,
                    "conciliacion": "Pendiente de Verificación Manual"
                },
                "comprobante_url": relative_url
            }

@app.post("/api/whatsapp/webhook")
async def whatsapp_webhook(
    request: Request,
    Body: str = Form(""),
    From: str = Form(""),
    NumMedia: int = Form(0),
    MediaUrl0: Optional[str] = Form(None),
    MediaContentType0: Optional[str] = Form(None)
):
    # Base URL for public media links
    base_url = str(request.base_url)
    
    # 1. Parse structured WhatsApp message from body text
    parsed_msg = ReceiptProcessor.parse_whatsapp_message(Body)
    
    # Check if unit could be parsed
    if not parsed_msg["unit"]:
        response_text = (
            "❌ No pudimos identificar tu número de departamento o unidad en el mensaje.\n\n"
            "Por favor, vuelve a enviar tu comprobante con el siguiente formato:\n"
            "Nombre: [Tu Nombre]\n"
            "Cédula: [Tu Cédula]\n"
            "Departamento: [Número de Depto]\n"
            "Mes: [Mes de pago]"
        )
        return Response(
            content=f"<Response><Message><Body>{response_text}</Body></Message></Response>",
            media_type="application/xml"
        )
        
    unit_id = parsed_msg["unit"]
    
    # Check if unit exists
    unit_exists = any(u["id"] == unit_id for u in sm.data["units"])
    if not unit_exists:
        response_text = f"❌ El departamento '{unit_id}' no está registrado en el condominio. Por favor verifica los datos."
        return Response(
            content=f"<Response><Message><Body>{response_text}</Body></Message></Response>",
            media_type="application/xml"
        )
        
    owner_name = next(u["owner"] for u in sm.data["units"] if u["id"] == unit_id)
    recipient_name = sm.get_receipt_recipient_name(unit_id)
    
    # 2. Download Media file if attached by Twilio
    file_text = ""
    extracted_amount = None
    extracted_reference = None
    relative_url = ""
    
    if NumMedia > 0 and MediaUrl0:
        # Determine extension from content type
        ext = ".jpg"
        if MediaContentType0 == "application/pdf":
            ext = ".pdf"
        elif MediaContentType0 == "image/png":
            ext = ".png"
            
        try:
            # Download file from Twilio CDN
            r = requests.get(MediaUrl0, timeout=10.0)
            if r.status_code == 200:
                year = parsed_msg.get("year") or datetime.now().year
                month = parsed_msg.get("month")
                if not month:
                    months = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
                    month = months[datetime.now().month - 1]
                    parsed_msg["month"] = month
                    
                relative_url, dest_path = save_structured_receipt(r.content, f"whatsapp_receipt{ext}", unit_id, year, month)
                
                # Extract text depending on file type
                if ext == ".pdf":
                    file_text = ReceiptProcessor.extract_text_from_pdf(dest_path)
                else:
                    file_text = OCRHelper.extract_text(dest_path)
        except Exception as e:
            print(f"Error downloading or parsing Twilio media: {e}")

    # If no file text was extracted, fall back to parsing body text
    if not file_text:
        file_text = Body

    # 3. Parse receipt details (Amount, Reference, Date)
    extracted_amount, extracted_reference, extracted_date = ReceiptProcessor.parse_bank_receipt_text(file_text)
    
    # Fallback to defaults if not parsed
    if not extracted_amount:
        extracted_amount = next(u["aliquot_base"] for u in sm.data["units"] if u["id"] == unit_id)
    if not extracted_reference:
        extracted_reference = parsed_msg["cedula"].split("-")[0] if parsed_msg["cedula"] else f"REF-{int(datetime.now().timestamp())}"

    # 4. Check if it's an additional charge or normal aliquot
    is_additional_charge = False
    matching_charge = None
    
    for c in sm.data["additional_charges"]:
        if c["unit"] == unit_id and c["status"] == "Pendiente":
            if c["type"].lower() in Body.lower() or any(keyword in Body.lower() for keyword in c["description"].lower().split()):
                is_additional_charge = True
                matching_charge = c
                break

    # 5. Search Bank Statement for a match (Exact reference match)
    bank_match = None
    for b in sm.data.get("bank_statement", []):
        if b.get("reference") and extracted_reference and str(b["reference"]).strip().lower() == str(extracted_reference).strip().lower():
            if abs(b["amount"] - extracted_amount) < 0.01 and not b.get("reconciled", False):
                bank_match = b
                break

    payment_date = bank_match["date"] if bank_match else (extracted_date or datetime.now().strftime("%Y-%m-%d"))

    # 6. Reconcile and Response Building
    if is_additional_charge and matching_charge:
        # Handle Additional Charge
        if bank_match:
            sm.reconcile_additional_charge(matching_charge["id"], extracted_reference, payment_date)
            matching_charge["comprobante_url"] = relative_url
            sm.sync()
            
            # Generate PDF
            receipt_no = f"REC-AC-{matching_charge['id']}"
            dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_charge_{matching_charge['id']}.pdf")
            concept = f"{matching_charge['type']}: {matching_charge['description']}"
            
            ReceiptProcessor.generate_receipt_pdf(
                dest_path=dest_pdf,
                receipt_no=receipt_no,
                resident_name=recipient_name,
                unit=unit_id,
                concept=concept,
                amount=matching_charge["amount"],
                late_fee=0.0,
                reference=extracted_reference,
                payment_date=payment_date
            )
            
            try:
                NotificationManager.send_receipt_notifications(
                    sm=sm,
                    unit_id=unit_id,
                    receipt_pdf_path=dest_pdf,
                    receipt_no=receipt_no,
                    resident_name=recipient_name,
                    concept=concept,
                    amount=matching_charge["amount"],
                    payment_date=payment_date,
                    reference=extracted_reference
                )
            except Exception as e:
                print(f"Error sending webhook charge notification: {e}")
                
            public_pdf_url = f"{base_url}static/receipts/recibo_charge_{matching_charge['id']}.pdf"
            response_xml = (
                "<Response><Message>"
                f"<Body>✅ ¡Gracias! Tu pago de {matching_charge['type']} por {sm.config['currency']}{matching_charge['amount']:.2f} ha sido conciliado en el banco Produbanco. Adjuntamos tu recibo oficial.</Body>"
                f"<Media>{public_pdf_url}</Media>"
                "</Message></Response>"
            )
        else:
            # Manual validation charge
            matching_charge["status"] = "Validación Manual"
            matching_charge["reference"] = extracted_reference
            matching_charge["payment_date"] = payment_date
            matching_charge["comprobante_url"] = relative_url
            sm.sync()
            
            response_xml = (
                "<Response><Message>"
                f"<Body>⚠️ Recibimos tu comprobante de {matching_charge['type']} por {sm.config['currency']}{extracted_amount:.2f}, pero la referencia {extracted_reference} está pendiente de confirmación bancaria. El pago ha quedado registrado en estado 'Validación Manual'.</Body>"
                "</Message></Response>"
            )
    else:
        # Handle Aliquot
        if not parsed_msg["month"]:
            months = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
            parsed_msg["month"] = months[datetime.now().month - 1]

        if bank_match:
            rec_result = sm.reconcile_payment(
                unit=unit_id,
                month=parsed_msg["month"],
                year=parsed_msg["year"],
                amount=extracted_amount,
                reference=extracted_reference,
                payment_date=payment_date
            )
            aliquot = rec_result["aliquot"]
            aliquot["comprobante_url"] = relative_url
            sm.sync()
            
            # Generate PDF
            receipt_no = f"REC-AL-{aliquot['id']}"
            dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_{aliquot['id']}.pdf")
            concept = f"Pago de Alícuota Ordinaria - Mes: {aliquot['month']} / {aliquot['year']}"
            
            ReceiptProcessor.generate_receipt_pdf(
                dest_path=dest_pdf,
                receipt_no=receipt_no,
                resident_name=recipient_name,
                unit=unit_id,
                concept=concept,
                amount=rec_result["aliquot_paid"],
                late_fee=rec_result["late_fee_paid"],
                reference=extracted_reference,
                payment_date=payment_date,
                abono_deuda=rec_result["abono_deuda"],
                pago_extra=rec_result["pago_extra"],
                saldo_deuda=rec_result["saldo_deuda"],
                advance_aliquots=rec_result.get("advance_aliquots")
            )
            
            try:
                NotificationManager.send_receipt_notifications(
                    sm=sm,
                    unit_id=unit_id,
                    receipt_pdf_path=dest_pdf,
                    receipt_no=receipt_no,
                    resident_name=recipient_name,
                    concept=concept,
                    amount=rec_result["aliquot_paid"] + rec_result["late_fee_paid"] + rec_result["abono_deuda"] + rec_result["pago_extra"],
                    payment_date=payment_date,
                    reference=extracted_reference
                )
            except Exception as e:
                print(f"Error sending webhook aliquot notification: {e}")
                
            late_fee_msg = f" (incluye recargo de {sm.config['currency']}{rec_result['late_fee_paid']:.2f} por pago atrasado)" if rec_result["late_fee_paid"] > 0 else ""
            public_pdf_url = f"{base_url}static/receipts/recibo_{aliquot['id']}.pdf"
            
            response_xml = (
                "<Response><Message>"
                f"<Body>✅ ¡Muchas gracias! Tu pago de la alícuota de {aliquot['month']} ha sido registrado y conciliado exitosamente{late_fee_msg}. Adjuntamos tu recibo digital oficial.</Body>"
                f"<Media>{public_pdf_url}</Media>"
                "</Message></Response>"
            )
        else:
            # Manual validation aliquot
            aliquot_id = f"{unit_id}-{parsed_msg['year']}-{parsed_msg['month']}"
            late_fee = sm.check_late_fee(payment_date, parsed_msg["month"], parsed_msg["year"])
            
            # Check if exists
            aliquot = None
            for a in sm.data["aliquots"]:
                if a["unit"] == unit_id and a["month"].lower() == parsed_msg["month"].lower() and int(a["year"]) == int(parsed_msg["year"]):
                    aliquot = a
                    break
            
            if aliquot:
                aliquot["status"] = "Validación Manual"
                aliquot["reference"] = extracted_reference
                aliquot["payment_date"] = payment_date
                aliquot["late_fee"] = late_fee
                aliquot["comprobante_url"] = relative_url
            else:
                aliquot = {
                    "id": aliquot_id,
                    "unit": unit_id,
                    "month": parsed_msg["month"],
                    "year": parsed_msg["year"],
                    "amount": extracted_amount,
                    "late_fee": late_fee,
                    "payment_date": payment_date,
                    "reference": extracted_reference,
                    "status": "Validación Manual",
                    "comprobante_url": relative_url
                }
                sm.data["aliquots"].append(aliquot)
            sm.sync()
            
            response_xml = (
                "<Response><Message>"
                f"<Body>⚠️ Comprobante recibido. La referencia {extracted_reference} por {sm.config['currency']}{extracted_amount:.2f} no coincide con el Estado de Cuenta. El pago de la alícuota de {parsed_msg['month']} queda en estado 'Validación Manual' hasta confirmación de la administración.</Body>"
                "</Message></Response>"
            )

    return Response(content=response_xml, media_type="application/xml")

@app.get("/api/whatsapp/meta-webhook")
def verify_meta_webhook(request: Request):
    """
    Handles verification of the Meta WhatsApp webhook (GET request).
    """
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    
    expected_token = sm.config.get("meta_wa_verify_token")
    
    if mode == "subscribe" and token == expected_token:
        print("[Meta Webhook] Verification successful!")
        return Response(content=challenge, media_type="text/plain")
    
    print("[Meta Webhook] Verification failed.")
    raise HTTPException(status_code=403, detail="Verification token mismatch.")

@app.post("/api/whatsapp/meta-webhook")
async def process_meta_webhook(request: Request):
    """
    Processes incoming messages and media from Meta's WhatsApp API.
    """
    try:
        body_bytes = await request.body()
        data = json.loads(body_bytes.decode("utf-8"))
    except Exception as e:
        print(f"[Meta Webhook] JSON parse error: {e}")
        return {"status": "ignored", "reason": "invalid_json"}
        
    print(f"[Meta Webhook] Event received: {json.dumps(data)}")
    
    # Check if this is a message status update or another event
    entry = data.get("entry", [])
    if not entry:
        return {"status": "ignored", "reason": "empty_entry"}
        
    changes = entry[0].get("changes", [])
    if not changes:
        return {"status": "ignored", "reason": "empty_changes"}
        
    val = changes[0].get("value", {})
    messages = val.get("messages", [])
    if not messages:
        # Status update (delivered, read, sent, etc.), ignore
        return {"status": "ignored", "reason": "no_messages"}
        
    msg = messages[0]
    from_number = msg.get("from") # Normalized phone number (e.g. 593987654321)
    msg_type = msg.get("type")
    
    # Get message body text if type is text or read caption of media
    msg_text = ""
    if msg_type == "text":
        msg_text = msg.get("text", {}).get("body", "")
    elif msg_type in ["image", "document"]:
        msg_text = msg.get(msg_type, {}).get("caption", "")
        
    print(f"[Meta Webhook] Message from {from_number} (Type: {msg_type}): {msg_text}")
    
    # 1. Emparejamiento Inteligente de Departamento/Unidad
    unit_id = None
    owner_name = None
    
    def clean_phone(p):
        return "".join(filter(str.isdigit, str(p)))
        
    clean_from = clean_phone(from_number)
    for u in sm.data["units"]:
        u_p1 = clean_phone(u.get("phone1", ""))
        u_p2 = clean_phone(u.get("phone2", ""))
        if clean_from and ((u_p1 and (clean_from[-9:] == u_p1[-9:] or u_p1[-9:] == clean_from[-9:])) or 
                           (u_p2 and (clean_from[-9:] == u_p2[-9:] or u_p2[-9:] == clean_from[-9:]))):
            unit_id = u["id"]
            owner_name = u["owner"]
            break
            
    parsed_msg = ReceiptProcessor.parse_whatsapp_message(msg_text)
    if not unit_id:
        if parsed_msg["unit"]:
            unit_id = parsed_msg["unit"]
            unit_exists = any(u["id"] == unit_id for u in sm.data["units"])
            if unit_exists:
                owner_name = next(u["owner"] for u in sm.data["units"] if u["id"] == unit_id)
            else:
                unit_id = None
                
    if not unit_id:
        response_text = (
            "❌ No pudimos identificar tu número de departamento o unidad en el mensaje o tu teléfono no está registrado.\n\n"
            "Por favor, vuelve a enviar tu mensaje asegurándote de incluir el formato:\n"
            "Departamento: [Número de Depto]"
        )
        send_meta_reply_text(from_number, response_text)
        return {"status": "error", "reason": "unit_not_identified"}
        
    if not parsed_msg["month"]:
        months = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
        parsed_msg["month"] = months[datetime.now().month - 1]
    year = parsed_msg.get("year") or datetime.now().year
    month = parsed_msg.get("month")
    recipient_name = sm.get_receipt_recipient_name(unit_id)
    
    # 2. Download Media file if attached
    file_bytes = None
    file_name = None
    file_ext = ""
    relative_url = ""
    
    if msg_type in ["image", "document"]:
        media_info = msg.get(msg_type, {})
        media_id = media_info.get("id")
        mime_type = media_info.get("mime_type", "")
        
        if msg_type == "document":
            file_name = media_info.get("filename", "comprobante.pdf")
            file_ext = os.path.splitext(file_name)[1].lower()
        else:
            file_name = "comprobante.jpg"
            file_ext = ".pdf" if mime_type == "application/pdf" else (".png" if mime_type == "image/png" else ".jpg")
            
        print(f"[Meta Webhook] Downloading media {media_id} ({mime_type})...")
        
        meta_token = sm.config.get("meta_wa_token")
        if meta_token and media_id:
            try:
                headers = {"Authorization": f"Bearer {meta_token}"}
                r_info = requests.get(f"https://graph.facebook.com/v19.0/{media_id}", headers=headers, timeout=10.0)
                if r_info.status_code == 200:
                    media_url = r_info.json().get("url")
                    if media_url:
                        r_file = requests.get(media_url, headers=headers, timeout=10.0)
                        if r_file.status_code == 200:
                            file_bytes = r_file.content
                            relative_url, dest_path = save_structured_receipt(file_bytes, file_name, unit_id, year, month)
            except Exception as e:
                print(f"[Meta Webhook] Error downloading Meta media: {e}")
                
    # 3. Text/OCR extraction
    file_text = ""
    if file_bytes and relative_url:
        dest_path = os.path.join(STATIC_DIR, relative_url.lstrip('/'))
        try:
            if file_ext == ".pdf":
                file_text = ReceiptProcessor.extract_text_from_pdf(dest_path)
            else:
                file_text = OCRHelper.extract_text(dest_path)
        except Exception as e:
            print(f"[Meta Webhook] Error extracting text: {e}")
            
    if not file_text:
        file_text = msg_text
        
    # 4. Parse receipt details (Amount, Reference, Date)
    extracted_amount, extracted_reference, extracted_date = ReceiptProcessor.parse_bank_receipt_text(file_text)
    if not extracted_amount:
        extracted_amount = next(u["aliquot_base"] for u in sm.data["units"] if u["id"] == unit_id)
    if not extracted_reference:
        extracted_reference = parsed_msg["cedula"].split("-")[0] if parsed_msg["cedula"] else f"REF-{int(datetime.now().timestamp())}"
        
    # 5. Check if it's an additional charge or normal aliquot
    is_additional_charge = False
    matching_charge = None
    
    for c in sm.data["additional_charges"]:
        if c["unit"] == unit_id and c["status"] == "Pendiente":
            if c["type"].lower() in msg_text.lower() or any(keyword in msg_text.lower() for keyword in c["description"].lower().split()):
                is_additional_charge = True
                matching_charge = c
                break
                
    # 6. Search Bank Statement for a match (Exact reference match)
    bank_match = None
    for b in sm.data.get("bank_statement", []):
        if b.get("reference") and extracted_reference and str(b["reference"]).strip().lower() == str(extracted_reference).strip().lower():
            if abs(b["amount"] - extracted_amount) < 0.01 and not b.get("reconciled", False):
                bank_match = b
                break
                
    payment_date = bank_match["date"] if bank_match else (extracted_date or datetime.now().strftime("%Y-%m-%d"))
        
    # 7. Reconcile and Send Response
    base_url = str(request.base_url)
    
    if is_additional_charge and matching_charge:
        if bank_match:
            sm.reconcile_additional_charge(matching_charge["id"], extracted_reference, payment_date)
            matching_charge["comprobante_url"] = relative_url
            sm.sync()
            
            receipt_no = f"REC-AC-{matching_charge['id']}"
            dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_charge_{matching_charge['id']}.pdf")
            concept = f"{matching_charge['type']}: {matching_charge['description']}"
            ReceiptProcessor.generate_receipt_pdf(
                dest_path=dest_pdf, receipt_no=receipt_no, resident_name=recipient_name,
                unit=unit_id, concept=concept, amount=matching_charge["amount"],
                late_fee=0.0, reference=extracted_reference, payment_date=payment_date
            )
            
            try:
                NotificationManager.send_receipt_notifications(
                    sm=sm, unit_id=unit_id, receipt_pdf_path=dest_pdf, receipt_no=receipt_no,
                    resident_name=recipient_name, concept=concept, amount=matching_charge["amount"],
                    payment_date=payment_date, reference=extracted_reference
                )
            except Exception as e:
                print(f"Error logging notifications: {e}")
                
            reply_body = f"✅ ¡Gracias! Tu pago de {matching_charge['type']} por {sm.config['currency']}{matching_charge['amount']:.2f} ha sido conciliado en el banco Produbanco. Adjuntamos tu recibo oficial."
            send_meta_reply_text(from_number, reply_body)
            
            public_pdf_url = f"{base_url}static/receipts/recibo_charge_{matching_charge['id']}.pdf"
            if "127.0.0.1" not in public_pdf_url:
                send_meta_reply_document(from_number, public_pdf_url, f"recibo_charge_{matching_charge['id']}.pdf", f"Recibo Oficial {receipt_no}")
        else:
            matching_charge["status"] = "Validación Manual"
            matching_charge["reference"] = extracted_reference
            matching_charge["payment_date"] = payment_date
            matching_charge["comprobante_url"] = relative_url
            sm.sync()
            
            reply_body = f"⚠️ Recibimos tu comprobante de {matching_charge['type']} por {sm.config['currency']}{extracted_amount:.2f}, pero la referencia {extracted_reference} está pendiente de confirmación bancaria. El pago ha quedado registrado en estado 'Validación Manual'."
            send_meta_reply_text(from_number, reply_body)
    else:
        if bank_match:
            rec_result = sm.reconcile_payment(
                unit=unit_id, month=parsed_msg["month"], year=parsed_msg["year"],
                amount=extracted_amount, reference=extracted_reference, payment_date=payment_date
            )
            aliquot = rec_result["aliquot"]
            aliquot["comprobante_url"] = relative_url
            sm.sync()
            
            receipt_no = f"REC-AL-{aliquot['id']}"
            dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_{aliquot['id']}.pdf")
            concept = f"Pago de Alícuota Ordinaria - Mes: {aliquot['month']} / {aliquot['year']}"
            ReceiptProcessor.generate_receipt_pdf(
                dest_path=dest_pdf, receipt_no=receipt_no, resident_name=recipient_name,
                unit=unit_id, concept=concept, amount=rec_result["aliquot_paid"],
                late_fee=rec_result["late_fee_paid"], reference=extracted_reference, payment_date=payment_date,
                abono_deuda=rec_result["abono_deuda"], pago_extra=rec_result["pago_extra"], saldo_deuda=rec_result["saldo_deuda"],
                advance_aliquots=rec_result.get("advance_aliquots")
            )
            
            try:
                NotificationManager.send_receipt_notifications(
                    sm=sm, unit_id=unit_id, receipt_pdf_path=dest_pdf, receipt_no=receipt_no,
                    resident_name=recipient_name, concept=concept, amount=rec_result["aliquot_paid"] + rec_result["late_fee_paid"] + rec_result["abono_deuda"] + rec_result["pago_extra"],
                    payment_date=payment_date, reference=extracted_reference
                )
            except Exception as e:
                print(f"Error logging notifications: {e}")
                
            late_fee_msg = f" (incluye recargo de {sm.config['currency']}{rec_result['late_fee_paid']:.2f} por pago atrasado)" if rec_result["late_fee_paid"] > 0 else ""
            reply_body = f"✅ ¡Muchas gracias! Tu pago de la alícuota de {aliquot['month']} ha sido registrado y conciliado exitosamente{late_fee_msg}. Adjuntamos tu recibo digital oficial."
            send_meta_reply_text(from_number, reply_body)
            
            public_pdf_url = f"{base_url}static/receipts/recibo_{aliquot['id']}.pdf"
            if "127.0.0.1" not in public_pdf_url:
                send_meta_reply_document(from_number, public_pdf_url, f"recibo_{aliquot['id']}.pdf", f"Recibo Oficial {receipt_no}")
        else:
            aliquot_id = f"{unit_id}-{parsed_msg['year']}-{parsed_msg['month']}"
            late_fee = sm.check_late_fee(payment_date, parsed_msg["month"], parsed_msg["year"])
            
            aliquot = None
            for a in sm.data["aliquots"]:
                if a["unit"] == unit_id and a["month"].lower() == parsed_msg["month"].lower() and int(a["year"]) == int(parsed_msg["year"]):
                    aliquot = a
                    break
                    
            if aliquot:
                aliquot["status"] = "Validación Manual"
                aliquot["reference"] = extracted_reference
                aliquot["payment_date"] = payment_date
                aliquot["late_fee"] = late_fee
                aliquot["comprobante_url"] = relative_url
            else:
                aliquot = {
                    "id": aliquot_id, "unit": unit_id, "month": parsed_msg["month"], "year": parsed_msg["year"],
                    "amount": extracted_amount, "late_fee": late_fee, "payment_date": payment_date,
                    "reference": extracted_reference, "status": "Validación Manual", "comprobante_url": relative_url
                }
                sm.data["aliquots"].append(aliquot)
            sm.sync()
            
            reply_body = f"⚠️ Comprobante recibido. La referencia {extracted_reference} por {sm.config['currency']}{extracted_amount:.2f} no coincide con el Estado de Cuenta. El pago de la alícuota de {parsed_msg['month']} queda en estado 'Validación Manual' hasta confirmación de la administración."
            send_meta_reply_text(from_number, reply_body)
            
    return {"status": "success"}

def send_meta_reply_text(to_number: str, message: str):
    """Helper to send a text reply using Meta API."""
    cfg = sm.config
    meta_token = cfg.get("meta_wa_token")
    phone_number_id = cfg.get("meta_wa_phone_number_id")
    if not meta_token or not phone_number_id:
        return
    try:
        headers = {
            "Authorization": f"Bearer {meta_token}",
            "Content-Type": "application/json"
        }
        dest_phone = "".join(filter(str.isdigit, to_number))
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": dest_phone,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": message
            }
        }
        requests.post(f"https://graph.facebook.com/v19.0/{phone_number_id}/messages", headers=headers, json=payload, timeout=5.0)
    except Exception as e:
        print(f"Error sending Meta text reply: {e}")

def send_meta_reply_document(to_number: str, doc_url: str, filename: str, caption: str):
    """Helper to send a document reply using Meta API."""
    cfg = sm.config
    meta_token = cfg.get("meta_wa_token")
    phone_number_id = cfg.get("meta_wa_phone_number_id")
    if not meta_token or not phone_number_id:
        return
    try:
        headers = {
            "Authorization": f"Bearer {meta_token}",
            "Content-Type": "application/json"
        }
        dest_phone = "".join(filter(str.isdigit, to_number))
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": dest_phone,
            "type": "document",
            "document": {
                "link": doc_url,
                "filename": filename,
                "caption": caption
            }
        }
        requests.post(f"https://graph.facebook.com/v19.0/{phone_number_id}/messages", headers=headers, json=payload, timeout=5.0)
    except Exception as e:
        print(f"Error sending Meta document reply: {e}")

@app.post("/api/upload-bank-statement")
async def upload_bank_statement(file: UploadFile = File(...)):
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in [".pdf", ".png", ".jpg", ".jpeg", ".csv", ".xlsx"]:
        raise HTTPException(status_code=400, detail="Formato de archivo no soportado.")
        
    temp_path = os.path.join(UPLOADS_DIR, f"statement_{datetime.now().timestamp()}{file_ext}")
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    try:
        from receipt_processor import BankStatementParser
        transactions = BankStatementParser.parse_statement_file(temp_path, file_ext)
        
        # Add transactions bulk filtering duplicates
        added, duplicates = sm.add_bank_transactions_bulk(transactions)
        
        return {
            "status": "success",
            "message": f"Se procesó el archivo '{file.filename}' correctamente.",
            "added": added,
            "duplicates": duplicates,
            "total_extracted": len(transactions)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al procesar el archivo: {e}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

@app.delete("/api/bank-statement/{reference}")
def delete_bank_transaction(reference: str):
    success = sm.delete_bank_transaction(reference)
    if not success:
        raise HTTPException(status_code=404, detail="Transacción bancaria no encontrada.")
    return {"status": "success", "message": "Transacción eliminada correctamente."}

@app.post("/api/reconcile-pair")
def reconcile_pair(req: ReconcilePair):
    # 1. Find bank transaction to get date and amount
    bank_tx = None
    for b in sm.data["bank_statement"]:
        if b["reference"] == req.bank_reference:
            bank_tx = b
            break
            
    if not bank_tx:
        raise HTTPException(status_code=404, detail="La transacción bancaria no existe.")
        
    bank_amount = float(bank_tx["amount"])

    if req.payment_type == "aliquot":
        # Find aliquot
        aliquot = None
        for a in sm.data["aliquots"]:
            if a["id"] == req.payment_id:
                aliquot = a
                break
                
        if not aliquot:
            raise HTTPException(status_code=404, detail="El registro de alícuota no existe.")
            
        reconcile_res = sm.reconcile_payment(
            unit=aliquot["unit"],
            month=aliquot["month"],
            year=aliquot["year"],
            amount=bank_amount,
            reference=req.bank_reference,
            payment_date=bank_tx["date"],
            recipient_override=req.receipt_recipient
        )
        
        # Mark bank transaction as reconciled
        bank_tx["reconciled"] = True
        sm.sync()
        
        # Generate receipt PDF
        receipt_no = f"REC-AL-{aliquot['id']}"
        resident_name = sm.get_receipt_recipient_name(aliquot["unit"], req.receipt_recipient)
        dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_{aliquot['id']}.pdf")
        concept = f"Pago de Alícuota Ordinaria - Mes: {aliquot['month']} / {aliquot['year']}"
        
        ReceiptProcessor.generate_receipt_pdf(
            dest_path=dest_pdf,
            receipt_no=receipt_no,
            resident_name=resident_name,
            unit=aliquot["unit"],
            concept=concept,
            amount=bank_amount,
            late_fee=aliquot.get("late_fee", 0.0),
            reference=req.bank_reference,
            payment_date=bank_tx["date"]
        )
        
        try:
            NotificationManager.send_receipt_notifications(
                sm=sm,
                unit_id=aliquot["unit"],
                receipt_pdf_path=dest_pdf,
                receipt_no=receipt_no,
                resident_name=resident_name,
                concept=concept,
                amount=bank_amount,
                payment_date=bank_tx["date"],
                reference=req.bank_reference
            )
        except Exception as e:
            print(f"Error sending reconcile-pair aliquot notification: {e}")
            
        return {"status": "success", "receipt_url": f"/static/receipts/recibo_{aliquot['id']}.pdf"}
        
    elif req.payment_type == "charge":
        # Find charge
        charge = None
        for c in sm.data["additional_charges"]:
            if c["id"] == req.payment_id:
                charge = c
                break
                
        if not charge:
            raise HTTPException(status_code=404, detail="El cargo extraordinario no existe.")
            
        reconcile_res = sm.reconcile_additional_charge(
            charge_id=charge["id"],
            reference=req.bank_reference,
            payment_date=bank_tx["date"],
            amount=bank_amount,
            recipient_override=req.receipt_recipient
        )
        
        # Mark bank transaction reconciled
        bank_tx["reconciled"] = True
        sm.sync()
        
        actual_paid = reconcile_res.get("abono_deuda", bank_amount) if reconcile_res else bank_amount
        
        # Generate receipt PDF
        receipt_no = f"REC-AC-{charge['id']}"
        resident_name = sm.get_receipt_recipient_name(charge["unit"], req.receipt_recipient)
        dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_charge_{charge['id']}.pdf")
        concept = f"{charge['type']}: {charge['description']}"
        
        ReceiptProcessor.generate_receipt_pdf(
            dest_path=dest_pdf,
            receipt_no=receipt_no,
            resident_name=resident_name,
            unit=charge["unit"],
            concept=concept,
            amount=actual_paid,
            late_fee=0.0,
            reference=req.bank_reference,
            payment_date=bank_tx["date"]
        )
        
        try:
            NotificationManager.send_receipt_notifications(
                sm=sm,
                unit_id=charge["unit"],
                receipt_pdf_path=dest_pdf,
                receipt_no=receipt_no,
                resident_name=resident_name,
                concept=concept,
                amount=actual_paid,
                payment_date=bank_tx["date"],
                reference=req.bank_reference
            )
        except Exception as e:
            print(f"Error sending reconcile-pair charge notification: {e}")
            
        return {"status": "success", "receipt_url": f"/static/receipts/recibo_charge_{charge['id']}.pdf"}

@app.get("/api/aliquots/export/csv")
def export_aliquots_csv():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID Alicuota", "Unidad", "Propietario", "Mes", "Año", "Monto Base", "Multa", "Total", "Fecha Pago", "Referencia", "Estado"])
    
    aliquots = sm.get_aliquots_with_details()
    for a in aliquots:
        total = a["amount"] + a["late_fee"]
        writer.writerow([
            a["id"], a["unit"], a["owner"], a["month"], a["year"], 
            f"{a['amount']:.2f}", f"{a['late_fee']:.2f}", f"{total:.2f}",
            a["payment_date"], a["reference"], a["status"]
        ])
        
    csv_data = output.getvalue()
    output.close()
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=reporte_alicuotas.csv"}
    )

@app.get("/api/expenses/export/csv")
def export_expenses_csv():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID Gasto", "Fecha", "Categoria", "Descripcion", "Monto"])
    
    expenses = sm.get_expenses_list()
    for e in expenses:
        writer.writerow([e["id"], e["date"], e["category"], e["description"], f"{e['amount']:.2f}"])
        
    csv_data = output.getvalue()
    output.close()
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=reporte_gastos.csv"}
    )

@app.get("/api/debtors/export/csv")
def export_debtors_csv():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Unidad", "Propietario", "Meses Vencidos", "Detalles Deuda", "Deuda Total"])
    
    debtors = sm.get_debtors()
    for d in debtors:
        writer.writerow([
            d["unit"], d["owner"], d["unpaid_aliquots_count"],
            ", ".join(d["details"]), f"{d['total_debt']:.2f}"
        ])
        
    csv_data = output.getvalue()
    output.close()
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=reporte_deudores.csv"}
    )

@app.get("/api/reports/financial-pdf")
def export_financial_pdf():
    pdf_path = os.path.join(RECEIPTS_DIR, "reporte_financiero_edificio.pdf")
    
    summary = sm.get_summary()
    aliquots = sm.get_aliquots_with_details()
    expenses = sm.get_expenses_list()
    debtors = sm.get_debtors()
    
    ReceiptProcessor.generate_financial_report_pdf(pdf_path, summary, aliquots, expenses, debtors)
    
    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename="Reporte_Financiero_Edificio.pdf"
    )

@app.get("/api/notification-logs")
def get_notification_logs():
    return sm.data.get("notification_logs", [])

@app.post("/api/receipts/resend")
def resend_receipt(req: ReceiptResend):
    unit_id = None
    resident_name = "Condómino"
    concept = ""
    amount = 0.0
    payment_date = ""
    reference = ""
    receipt_no = ""
    pdf_filename = ""
    
    if req.type == "aliquot":
        aliquot = next((a for a in sm.data["aliquots"] if a["id"] == req.id), None)
        if not aliquot:
            raise HTTPException(status_code=404, detail="Alícuota no encontrada.")
        if aliquot["status"] != "Pagado":
            raise HTTPException(status_code=400, detail="Solo se pueden reenviar recibos de pagos ya conciliados/pagados.")
            
        unit_id = aliquot["unit"]
        resident_name = sm.get_receipt_recipient_name(unit_id)
        concept = f"Pago de Alícuota Ordinaria - Mes: {aliquot['month']} / {aliquot['year']}"
        amount = aliquot["amount"] + aliquot["late_fee"]
        payment_date = aliquot["payment_date"]
        reference = aliquot["reference"]
        receipt_no = f"REC-AL-{aliquot['id']}"
        pdf_filename = f"recibo_{aliquot['id']}.pdf"
        
    elif req.type == "charge":
        charge = next((c for c in sm.data["additional_charges"] if c["id"] == req.id), None)
        if not charge:
            raise HTTPException(status_code=404, detail="Cargo extraordinario no encontrado.")
        if charge["status"] != "Pagado":
            raise HTTPException(status_code=400, detail="Solo se pueden reenviar recibos de pagos ya conciliados/pagados.")
            
        unit_id = charge["unit"]
        resident_name = sm.get_receipt_recipient_name(unit_id)
        concept = f"{charge['type']}: {charge['description']}"
        amount = charge["amount"]
        payment_date = charge["payment_date"]
        reference = charge["reference"]
        receipt_no = f"REC-AC-{charge['id']}"
        pdf_filename = f"recibo_charge_{charge['id']}.pdf"
        
    else:
        raise HTTPException(status_code=400, detail="Tipo de recibo no soportado.")
        
    pdf_path = os.path.join(RECEIPTS_DIR, pdf_filename)
    
    late_fee_val = aliquot.get("late_fee", 0.0) if req.type == "aliquot" else 0.0
    base_amount = (aliquot.get("paid_amount") or aliquot.get("amount") or 0.0) if req.type == "aliquot" else (charge.get("paid_amount") or charge.get("amount") or 0.0)
    abono_val = float(aliquot.get("abono_deuda", 0.0) or 0.0) if req.type == "aliquot" else 0.0
    pago_extra_val = float(aliquot.get("pago_extra", 0.0) or 0.0) if req.type == "aliquot" else 0.0
    saldo_deuda_val = float(aliquot.get("saldo_deuda", 0.0) or 0.0) if req.type == "aliquot" else 0.0
    advance_aliquots_val = aliquot.get("advance_aliquots") if req.type == "aliquot" else None

    ReceiptProcessor.generate_receipt_pdf(
        dest_path=pdf_path,
        receipt_no=receipt_no,
        resident_name=resident_name,
        unit=str(unit_id),
        concept=concept,
        amount=base_amount,
        late_fee=late_fee_val,
        reference=reference,
        payment_date=payment_date,
        condo_name=sm.config.get("condo_name"),
        condo_address=sm.config.get("condo_address"),
        condo_ruc=sm.config.get("condo_ruc"),
        abono_deuda=abono_val,
        pago_extra=pago_extra_val,
        saldo_deuda=saldo_deuda_val,
        advance_aliquots=advance_aliquots_val,
        sm=sm
    )
        
    results = NotificationManager.send_receipt_notifications(
        sm=sm,
        unit_id=unit_id,
        receipt_pdf_path=pdf_path,
        receipt_no=receipt_no,
        resident_name=resident_name,
        concept=concept,
        amount=amount,
        payment_date=payment_date,
        reference=reference
    )
    
    if not results:
        raise HTTPException(status_code=500, detail="No se pudo despachar el reenvío. Verifique que existan contactos registrados.")
        
    return {"status": "success", "message": f"Recibo reenviado exitosamente a {len(results)} destinos.", "sent_logs": results}

@app.post("/api/reconcile-mass")
def reconcile_mass():
    try:
        aliquots_count, charges_count = sm.reconcile_mass()
        return {
            "status": "success",
            "aliquots_reconciled": aliquots_count,
            "charges_reconciled": charges_count,
            "total_reconciled": aliquots_count + charges_count
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error durante la conciliación masiva: {e}")

@app.get("/api/receipts/{type}/{id}/pdf")
def get_receipt_pdf_by_type(type: str, id: str):
    type_clean = str(type or "aliquot").strip().lower()
    if type_clean in ["aliquots", "aliquot", "alicuota"]:
        aliquot = None
        for a in sm.data.get("aliquots", []):
            if str(a.get("id")) == str(id):
                aliquot = a
                break
        if not aliquot:
            raise HTTPException(status_code=404, detail="Alícuota no encontrada.")
            
        dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_{aliquot['id']}.pdf")
        
        # Always regenerate fresh receipt to reflect current resident/owner and config
        receipt_no = f"REC-AL-{aliquot['id']}"
        resident_name = aliquot.get("paid_by") or sm.get_receipt_recipient_name(aliquot.get("unit"))
        concept = f"Pago de Alícuota Ordinaria - Mes: {aliquot.get('month', '')} / {aliquot.get('year', '')}"
        paid_amount = float(aliquot.get("paid_amount") or aliquot.get("amount") or 70.0)
        late_fee = float(aliquot.get("late_fee") or 0.0)
        ref = aliquot.get("reference") or "Comprobante de Pago"
        pdate = aliquot.get("payment_date") or datetime.now().strftime("%Y-%m-%d")
        
        ReceiptProcessor.generate_receipt_pdf(
            dest_path=dest_pdf,
            receipt_no=receipt_no,
            resident_name=resident_name,
            unit=str(aliquot.get("unit")),
            concept=concept,
            amount=paid_amount,
            late_fee=late_fee,
            reference=ref,
            payment_date=pdate,
            condo_name=sm.config.get("condo_name"),
            condo_address=sm.config.get("condo_address"),
            condo_ruc=sm.config.get("condo_ruc"),
            abono_deuda=float(aliquot.get("abono_deuda", 0.0) or 0.0),
            pago_extra=float(aliquot.get("pago_extra", 0.0) or 0.0),
            saldo_deuda=float(aliquot.get("saldo_deuda", 0.0) or 0.0),
            advance_aliquots=aliquot.get("advance_aliquots"),
            sm=sm
        )
        
        return FileResponse(dest_pdf, media_type="application/pdf", filename=f"recibo_{aliquot['id']}.pdf")

    elif type_clean in ["charge", "cargo", "additional_charge", "charges"]:
        charge = None
        for c in sm.data.get("additional_charges", []):
            if str(c.get("id")) == str(id):
                charge = c
                break
        if not charge:
            raise HTTPException(status_code=404, detail="Cargo no encontrado.")
            
        dest_pdf = os.path.join(RECEIPTS_DIR, f"recibo_charge_{charge['id']}.pdf")
        
        # Always regenerate fresh receipt to reflect current resident/owner and config
        receipt_no = f"REC-AC-{charge['id']}"
        resident_name = charge.get("paid_by") or charge.get("owner_name") or sm.get_receipt_recipient_name(charge.get("unit"))
        concept = f"{charge.get('type', 'Cargo')}: {charge.get('description', '') or charge.get('concept', '')}"
        paid_amount = float(charge.get("paid_amount") or charge.get("amount") or 0.0)
        ref = charge.get("reference") or "Comprobante de Pago"
        pdate = charge.get("payment_date") or datetime.now().strftime("%Y-%m-%d")
        
        ReceiptProcessor.generate_receipt_pdf(
            dest_path=dest_pdf,
            receipt_no=receipt_no,
            resident_name=resident_name,
            unit=str(charge.get("unit")),
            concept=concept,
            amount=paid_amount,
            late_fee=0.0,
            reference=ref,
            payment_date=pdate,
            condo_name=sm.config.get("condo_name"),
            condo_address=sm.config.get("condo_address"),
            condo_ruc=sm.config.get("condo_ruc"),
            sm=sm
        )
        
        return FileResponse(dest_pdf, media_type="application/pdf", filename=f"recibo_charge_{charge['id']}.pdf")

    else:
        raise HTTPException(status_code=400, detail="Tipo de recibo no válido.")

@app.get("/api/aliquots/{id}/receipt-pdf")
def get_aliquot_receipt_pdf_alias(id: str):
    return get_receipt_pdf_by_type("aliquot", id)

@app.get("/api/charges/{id}/receipt-pdf")
def get_charge_receipt_pdf_alias(id: str):
    return get_receipt_pdf_by_type("charge", id)

@app.get("/api/reports/preview")
def get_reports_preview(
    report_type: str = "payments",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    unit: Optional[str] = None,
    category: Optional[str] = None,
    min_debt: float = 0.0,
    status_filter: Optional[str] = None,
    record_type: Optional[str] = None
):
    try:
        report_type_clean = str(report_type or "payments").strip().lower()
        if report_type_clean == "payments":
            return sm.get_payments_report_data(start_date=start_date, end_date=end_date, unit=unit, category=category)
        elif report_type_clean in ["debtors", "deudores"]:
            return sm.get_debtors_report_data(unit=unit, min_debt=min_debt, status_filter=status_filter)
        elif report_type_clean in ["unreconciled", "sin_conciliar"]:
            return sm.get_unreconciled_report_data(start_date=start_date, end_date=end_date, record_type=record_type)
        else:
            raise HTTPException(status_code=400, detail=f"Tipo de reporte '{report_type}' no soportado.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/reports/export/pdf")
def export_report_pdf(
    report_type: str = "payments",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    unit: Optional[str] = None,
    category: Optional[str] = None,
    min_debt: float = 0.0,
    status_filter: Optional[str] = None,
    record_type: Optional[str] = None
):
    try:
        report_type_clean = str(report_type or "payments").strip().lower()
        condo_name = sm.config.get("condo_name", "Condominio El Mirador")
        condo_address = sm.config.get("condo_address", "")
        condo_ruc = sm.config.get("condo_ruc", "")
        currency = sm.config.get("currency", "$")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if report_type_clean == "payments":
            data = sm.get_payments_report_data(start_date=start_date, end_date=end_date, unit=unit, category=category)
            dest_file = os.path.join(RECEIPTS_DIR, f"reporte_pagos_{timestamp}.pdf")
            FinancialReportPDFGenerator.generate_payments_report_pdf(dest_file, data, condo_name=condo_name, currency=currency, condo_address=condo_address, condo_ruc=condo_ruc)
            return FileResponse(dest_file, media_type="application/pdf", filename=f"reporte_pagos_{timestamp}.pdf")
            
        elif report_type_clean in ["debtors", "deudores"]:
            data = sm.get_debtors_report_data(unit=unit, min_debt=min_debt, status_filter=status_filter)
            dest_file = os.path.join(RECEIPTS_DIR, f"reporte_deudores_{timestamp}.pdf")
            FinancialReportPDFGenerator.generate_debtors_report_pdf(dest_file, data, condo_name=condo_name, currency=currency, condo_address=condo_address, condo_ruc=condo_ruc)
            return FileResponse(dest_file, media_type="application/pdf", filename=f"reporte_deudores_{timestamp}.pdf")
            
        elif report_type_clean in ["unreconciled", "sin_conciliar"]:
            data = sm.get_unreconciled_report_data(start_date=start_date, end_date=end_date, record_type=record_type)
            dest_file = os.path.join(RECEIPTS_DIR, f"reporte_sin_conciliar_{timestamp}.pdf")
            FinancialReportPDFGenerator.generate_unreconciled_report_pdf(dest_file, data, condo_name=condo_name, currency=currency, condo_address=condo_address, condo_ruc=condo_ruc)
            return FileResponse(dest_file, media_type="application/pdf", filename=f"reporte_sin_conciliar_{timestamp}.pdf")
            
        else:
            raise HTTPException(status_code=400, detail=f"Tipo de reporte '{report_type}' no soportado.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/reports/export/excel")
def export_report_excel(
    report_type: str = "payments",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    unit: Optional[str] = None,
    category: Optional[str] = None,
    min_debt: float = 0.0,
    status_filter: Optional[str] = None,
    record_type: Optional[str] = None
):
    try:
        report_type_clean = str(report_type or "payments").strip().lower()
        condo_name = sm.config.get("condo_name", "Condominio El Mirador")
        condo_address = sm.config.get("condo_address", "")
        condo_ruc = sm.config.get("condo_ruc", "")
        currency = sm.config.get("currency", "$")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if report_type_clean == "payments":
            data = sm.get_payments_report_data(start_date=start_date, end_date=end_date, unit=unit, category=category)
            dest_file = os.path.join(RECEIPTS_DIR, f"reporte_pagos_{timestamp}.xlsx")
            FinancialReportExcelGenerator.generate_payments_report_excel(dest_file, data, condo_name=condo_name, currency=currency, condo_address=condo_address, condo_ruc=condo_ruc)
            return FileResponse(dest_file, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=f"reporte_pagos_{timestamp}.xlsx")
            
        elif report_type_clean in ["debtors", "deudores"]:
            data = sm.get_debtors_report_data(unit=unit, min_debt=min_debt, status_filter=status_filter)
            dest_file = os.path.join(RECEIPTS_DIR, f"reporte_deudores_{timestamp}.xlsx")
            FinancialReportExcelGenerator.generate_debtors_report_excel(dest_file, data, condo_name=condo_name, currency=currency, condo_address=condo_address, condo_ruc=condo_ruc)
            return FileResponse(dest_file, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=f"reporte_deudores_{timestamp}.xlsx")
            
        elif report_type_clean in ["unreconciled", "sin_conciliar"]:
            data = sm.get_unreconciled_report_data(start_date=start_date, end_date=end_date, record_type=record_type)
            dest_file = os.path.join(RECEIPTS_DIR, f"reporte_sin_conciliar_{timestamp}.xlsx")
            FinancialReportExcelGenerator.generate_unreconciled_report_excel(dest_file, data, condo_name=condo_name, currency=currency, condo_address=condo_address, condo_ruc=condo_ruc)
            return FileResponse(dest_file, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=f"reporte_sin_conciliar_{timestamp}.xlsx")
            
        else:
            raise HTTPException(status_code=400, detail=f"Tipo de reporte '{report_type}' no soportado.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

import hashlib
import random
from datetime import datetime, timedelta
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from sqlalchemy import func

@app.post("/api/auth/request-temp-password")
def request_temp_password(req: RequestTempPasswordRequest):
    req_cedula = str(req.cedula or "").strip()
    req_email = str(req.email or "").strip().lower()
    req_role = str(req.role or "").strip()

    if not req_cedula and not req_email:
        raise HTTPException(status_code=400, detail="Debe ingresar su cédula o correo electrónico.")

    with Session(sm.engine) as session:
        # Determine allowed roles
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
                detail=f"No se encontró ningún usuario con los datos ingresados para el rol seleccionado ({req_role}). Verifique que sus datos coincidan con los registrados en la administración."
            )
        
        # Generate a 6-digit temporal PIN
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
        from_email = cfg.get("smtp_from") or cfg.get("smtp_user")
        from_header = f'"{condo_name}" <{from_email}>'
        domain = from_email.split("@")[-1] if "@" in from_email else "gmail.com"

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
            print("\n" + "="*50)
            print("SIMULANDO ENVÍO DE EMAIL DE CLAVE TEMPORAL:")
            print(f"Para: {user.email}")
            print(f"PIN Temporal: {temp_pin}")
            if smtp_err:
                print(f"Detalle error SMTP: {smtp_err}")
            print("="*50 + "\n")
            
            return {
                "status": "success",
                "message": f"PIN Temporal generado: {temp_pin} (Válido por 15 min). El servidor de correo no está activo o falló el envío ({smtp_err or 'Modo Simulación'}), por lo que puedes usar este PIN directamente para ingresar.",
                "simulated": True,
                "temp_pin": temp_pin
            }
        else:
            return {
                "status": "success",
                "message": f"Clave temporal enviada exitosamente a tu correo ({user.email}). Revisa tu bandeja de entrada o carpeta de spam.",
                "simulated": False
            }

@app.post("/api/auth/reset-password")
def reset_password(req: ResetPasswordRequest):
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
        
        # Check PIN and expiry
        if str(user.temp_password).strip() != req_temp_pin:
            raise HTTPException(status_code=400, detail="El PIN temporal ingresado es incorrecto.")
            
        if user.temp_password_expiry:
            try:
                expiry = datetime.fromisoformat(user.temp_password_expiry)
                if datetime.now() > expiry:
                    raise HTTPException(status_code=400, detail="El PIN temporal ha expirado (límite 15 min). Por favor solicite uno nuevo en 'Recuperar Clave'.")
            except ValueError:
                pass
        
        # Update password
        user.password_hash = hashlib.sha256(req_new_pwd.encode()).hexdigest()
        user.temp_password = None
        user.temp_password_expiry = None
        session.add(user)
        session.commit()
        return {"status": "success", "message": "¡Contraseña actualizada exitosamente! Ya puedes iniciar sesión con tu nueva contraseña."}

@app.post("/api/auth/login")
def login(req: LoginRequest):
    req_cedula = str(req.cedula or "").strip()
    req_email = str(req.email or "").strip().lower()
    req_role = str(req.role or "").strip()
    req_password = str(req.password or "").strip()

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
        
        # Check password
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
        
        hashed_pwd = hashlib.sha256(req_password.encode()).hexdigest()
        is_perm_valid = (user.password_hash == hashed_pwd)
        
        # Default superadmin password fallback
        if not is_perm_valid and not is_temp_valid:
            if user.email == 'superadmin@condo.com' and req_password in ['admin123', 'super123']:
                is_perm_valid = True
                user.password_hash = hashlib.sha256(b"admin123").hexdigest()
                session.add(user)
                session.commit()
        
        if not is_temp_valid and not is_perm_valid:
            if not user.password_hash and not user.temp_password:
                raise HTTPException(status_code=400, detail="Esta cuenta aún no tiene contraseña establecida. Por favor ve a la pestaña 'Recuperar Clave' para solicitar tu PIN temporal de primer acceso.")
            raise HTTPException(status_code=400, detail="Contraseña o PIN temporal incorrecto. Verifique e intente nuevamente.")
            
        # If logged in with temp password, indicate they should change it
        needs_password_change = is_temp_valid
        
        # Determine unit if condomino
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
        
        return {
            "status": "success",
            "user": {
                "cedula": user.cedula,
                "email": user.email,
                "role": user.role,
                "name": user.name,
                "unit_id": unit_id
            },
            "needs_password_change": needs_password_change
        }
        
@app.post("/api/admin/register")
def register_admin(req: AdminRegisterRequest, request: Request):
    role_header = request.headers.get("X-User-Role")
    if role_header != "superadmin":
        raise HTTPException(status_code=403, detail="Permiso denegado. Solo el Superadministrador puede registrar administradores.")
        
    with Session(sm.engine) as session:
        existing = session.exec(select(User).where(User.cedula == req.cedula)).first()
        if existing:
            raise HTTPException(status_code=400, detail="El administrador ya se encuentra registrado.")
            
        pwd_hash = hashlib.sha256(req.password.encode()).hexdigest()
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
        
@app.get("/api/admins")
def get_admins(request: Request):
    role_header = request.headers.get("X-User-Role")
    if role_header != "superadmin":
        raise HTTPException(status_code=403, detail="Acceso denegado.")
        
    with Session(sm.engine) as session:
        admins = session.exec(select(User).where(User.role == "admin")).all()
        return [a.model_dump(exclude={"password_hash", "temp_password"}) for a in admins]

@app.get("/api/units/{unit_id}/no-debt-certificate")
def download_no_debt_certificate(unit_id: str):
    unit = next((u for u in sm.data["units"] if u["id"] == str(unit_id)), None)
    if not unit:
        raise HTTPException(status_code=404, detail="Departamento no encontrado.")
        
    debtors = sm.get_debtors()
    is_debtor = any(d["unit"] == str(unit_id) and d["total_debt"] > 0 for d in debtors)
    if is_debtor:
        raise HTTPException(status_code=400, detail="El departamento posee deudas pendientes y no califica para el certificado de no adeudar.")
        
    president = next((m["name"] for m in sm.data.get("board_members", []) if "presidente" in m.get("role", "").lower()), sm.config.get("directive_president", "Carlos Mendoza"))
    treasurer = next((m["name"] for m in sm.data.get("board_members", []) if "tesorero" in m.get("role", "").lower()), sm.config.get("directive_treasurer", "Sofia Herrera"))
    
    condo_name = sm.config.get("condo_name", "Condominio El Mirador")
    condo_address = sm.config.get("condo_address", "")
    condo_ruc = sm.config.get("condo_ruc", "")
    
    pdf_filename = f"certificado_no_adeudar_{unit_id}.pdf"
    pdf_path = os.path.join(RECEIPTS_DIR, pdf_filename)
    
    generate_no_debt_certificate_pdf(
        dest_path=pdf_path,
        unit_id=unit_id,
        owner_name=unit["owner"],
        president_name=president,
        treasurer_name=treasurer,
        condo_name=condo_name,
        condo_address=condo_address,
        condo_ruc=condo_ruc
    )
    
    return FileResponse(pdf_path, media_type="application/pdf", filename=pdf_filename)

# Serve static files last
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
def read_root():
    return RedirectResponse(url="/static/index.html")
