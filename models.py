from sqlmodel import SQLModel, Field
from typing import Optional

class Unit(SQLModel, table=True):
    __tablename__ = "units"
    id: str = Field(primary_key=True)
    owner: str
    cedula_owner: Optional[str] = ""
    phone1: Optional[str] = ""
    email1: Optional[str] = ""
    tenant: Optional[str] = ""
    cedula_tenant: Optional[str] = ""
    phone2: Optional[str] = ""
    email2: Optional[str] = ""
    aliquot_base: Optional[float] = 70.0
    receipt_recipient_type: Optional[str] = "general"

class User(SQLModel, table=True):
    __tablename__ = "users"
    id: Optional[int] = Field(default=None, primary_key=True)
    cedula: str = Field(unique=True, index=True)
    email: str = Field(unique=True, index=True)
    password_hash: Optional[str] = None
    role: str  # 'superadmin', 'admin', 'owner', 'tenant'
    name: str
    is_active: bool = True
    temp_password: Optional[str] = None
    temp_password_expiry: Optional[str] = None

class Aliquot(SQLModel, table=True):
    __tablename__ = "aliquots"
    id: str = Field(primary_key=True)
    unit: str
    month: str
    year: int
    amount: float
    late_fee: float = 0.0
    paid_amount: float = Field(default=0.0)
    payment_date: Optional[str] = ""
    reference: Optional[str] = ""
    status: str = "Pendiente"
    comprobante_url: Optional[str] = ""
    owner_name: Optional[str] = ""
    cedula_owner: Optional[str] = ""
    email_owner: Optional[str] = ""
    phone_owner: Optional[str] = ""
    tenant_name: Optional[str] = ""
    cedula_tenant: Optional[str] = ""
    email_tenant: Optional[str] = ""
    phone_tenant: Optional[str] = ""
    paid_by: Optional[str] = ""

class AdditionalCharge(SQLModel, table=True):
    __tablename__ = "additional_charges"
    id: str = Field(primary_key=True)
    unit: str
    type: str
    description: str
    amount: float
    issue_date: str
    status: str = "Pendiente"
    paid_amount: float = Field(default=0.0)
    payment_date: Optional[str] = ""
    reference: Optional[str] = ""
    comprobante_url: Optional[str] = ""
    owner_name: Optional[str] = ""
    cedula_owner: Optional[str] = ""
    email_owner: Optional[str] = ""
    phone_owner: Optional[str] = ""
    tenant_name: Optional[str] = ""
    cedula_tenant: Optional[str] = ""
    email_tenant: Optional[str] = ""
    phone_tenant: Optional[str] = ""
    paid_by: Optional[str] = ""

class Expense(SQLModel, table=True):
    __tablename__ = "expenses"
    id: str = Field(primary_key=True)
    date: str
    category: str
    description: str
    amount: float

class BankTransaction(SQLModel, table=True):
    __tablename__ = "bank_statement"
    reference: str = Field(primary_key=True)
    date: str
    amount: float
    detail: str
    reconciled: bool = False

class NotificationLog(SQLModel, table=True):
    __tablename__ = "notification_logs"
    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: str
    unit: str
    recipient: str
    channel: str
    destination: str
    subject: str
    status: str
    mode: str
    details: str

class OtherIncome(SQLModel, table=True):
    __tablename__ = "other_incomes"
    id: Optional[int] = Field(default=None, primary_key=True)
    date: str
    concept: str
    amount: float
    reference: Optional[str] = ""

class BoardMember(SQLModel, table=True):
    __tablename__ = "board_members"
    id: Optional[int] = Field(default=None, primary_key=True)
    unit_id: str
    name: str
    role: str

class SystemConfig(SQLModel, table=True):
    __tablename__ = "system_configs"
    id: int = Field(default=1, primary_key=True)
    condo_name: str = Field(default="Condominio El Mirador")
    currency: str = Field(default="$")
    late_fee_day: int = Field(default=15)
    late_fee_amount: float = Field(default=10.0)
    default_aliquot_base: float = Field(default=70.0)
    receipt_recipient_type: str = Field(default="inquilino")
    exonerate_directiva: bool = Field(default=False)
    directive_president: Optional[str] = Field(default="")
    directive_treasurer: Optional[str] = Field(default="")
    condo_address: Optional[str] = Field(default="")
    condo_ruc: Optional[str] = Field(default="")
    app_mode: str = Field(default="produccion")
    use_google_sheets: bool = Field(default=False)
    spreadsheet_id: Optional[str] = Field(default="")
    google_credentials_json: Optional[str] = Field(default="")
    
    # Notifications (SMTP)
    smtp_host: Optional[str] = Field(default="")
    smtp_port: int = Field(default=587)
    smtp_user: Optional[str] = Field(default="")
    smtp_password: Optional[str] = Field(default="")
    smtp_from: Optional[str] = Field(default="")
    
    # WhatsApp (Twilio / Meta)
    whatsapp_provider: str = Field(default="simulated")
    twilio_sid: Optional[str] = Field(default="")
    twilio_token: Optional[str] = Field(default="")
    twilio_whatsapp_from: Optional[str] = Field(default="")
    meta_wa_token: Optional[str] = Field(default="")
    meta_wa_phone_number_id: Optional[str] = Field(default="")
    meta_wa_verify_token: Optional[str] = Field(default="")
    meta_wa_business_account_id: Optional[str] = Field(default="")
    
    # DB Engine
    db_type: str = Field(default="sqlite")
    db_host: Optional[str] = Field(default="")
    db_port: int = Field(default=5432)
    db_user: Optional[str] = Field(default="")
    db_password: Optional[str] = Field(default="")
    db_name: Optional[str] = Field(default="")
    db_custom_url: Optional[str] = Field(default="")
    
    updated_at: Optional[str] = Field(default="")

class InitialBalance(SQLModel, table=True):
    __tablename__ = "initial_balances"
    id: int = Field(default=1, primary_key=True)
    initial_bank_balance: float = Field(default=0.0)
    initial_reserve_fund: float = Field(default=0.0)
    cut_off_date: Optional[str] = Field(default="")
    description: Optional[str] = Field(default="Saldo inicial de apertura de cuentas")
    notes: Optional[str] = Field(default="")
    updated_at: Optional[str] = Field(default="")

