from typing import Optional
from pydantic import BaseModel

# ==============================================================================
# Pydantic Request & Response Schemas for CondoManager
# ==============================================================================

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

class AliquotEmitRequest(BaseModel):
    month: str
    year: int
    unit_id: Optional[str] = None
    amount: Optional[float] = None

class BankTransactionUpdate(BaseModel):
    reference: str
    date: Optional[str] = None
    amount: Optional[float] = None
    detail: Optional[str] = None

class BankTransactionCreate(BaseModel):
    date: str
    reference: str
    amount: float
    detail: str

class ReconcileManual(BaseModel):
    id: str  # Aliquot ID or Charge ID
    type: str  # 'aliquot' or 'charge'
    reference: str
    payment_date: str
    late_fee: Optional[float] = 0.0
    amount: Optional[float] = None
    receipt_recipient: Optional[str] = "inquilino"

class ReconcilePair(BaseModel):
    payment_id: str
    payment_type: str
    bank_reference: str
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

class ReceiptResend(BaseModel):
    id: str
    type: str

class EmailTestRequest(BaseModel):
    recipient_email: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from: Optional[str] = None

class ReviewQueueApproveRequest(BaseModel):
    bank_reference: str
    payment_date: Optional[str] = None
    amount: Optional[float] = None
    receipt_recipient: Optional[str] = "inquilino"

class ReviewQueueRejectRequest(BaseModel):
    reason: Optional[str] = "Comprobante no válido o no encontrado en extracto bancario"

