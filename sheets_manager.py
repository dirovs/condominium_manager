import os
import json
import hashlib
import urllib.parse
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from sqlmodel import SQLModel, create_engine, Session, select, text
from models import Unit, Aliquot, AdditionalCharge, Expense, BankTransaction, NotificationLog, User, OtherIncome, BoardMember, SystemConfig, InitialBalance
from crypto_helper import encrypt_secret, decrypt_secret, is_encrypted

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
DB_PATH = os.path.join(os.path.dirname(__file__), "local_database.json")

class SheetsManager:
    def __init__(self):
        self.config = self.load_config_from_file_or_defaults()
        self.data = {}
        self.client = None
        self.spreadsheet = None
        
        # Database setup: Strictly enforce configured db_type without silent fallback
        db_type = str(self.config.get("db_type", "sqlite")).strip().lower()
        self.current_db_url = self.build_db_url()
        
        if db_type == "postgres":
            try:
                self.engine = create_engine(self.current_db_url, echo=False)
                with self.engine.connect() as conn:
                    pass
                SQLModel.metadata.create_all(self.engine)
                self.run_migrations()
            except Exception as e:
                safe_err = str(e).encode('ascii', errors='replace').decode('ascii')
                print(f"[DB Error] Error fatal al conectar a PostgreSQL ({getattr(self, 'current_db_url', 'None')}): {safe_err}")
                raise ConnectionError(f"No se pudo conectar a la base de datos PostgreSQL ({getattr(self, 'current_db_url', 'None')}): {safe_err}. Cuando PostgreSQL está configurado, no se permite el uso de SQLite.")
        else:
            # SQLite mode
            self.engine = create_engine(self.current_db_url, echo=False)
            with self.engine.connect() as conn:
                pass
            SQLModel.metadata.create_all(self.engine)
            self.run_migrations()
        
        # Seed database if empty (superadmin, units, system_configs, initial_balances)
        self.seed_db_if_empty()
        
        # Load active configuration from Database (decrypting secrets in memory)
        self.config = self.load_config()
        
        self.load_data()

    def get_postgres_driver_prefix(self):
        try:
            import psycopg
            return "postgresql+psycopg://"
        except ImportError:
            try:
                import psycopg2
                return "postgresql+psycopg2://"
            except ImportError:
                return "postgresql://"

    def build_db_url(self, force_config_db_type=False):
        is_testing = (os.environ.get("TESTING") == "True")
        if is_testing and not force_config_db_type:
            db_path = os.path.join(os.path.dirname(__file__), "condo_manager_test.db")
            return f"sqlite:///{db_path}"

        # 1. Support standard DATABASE_URL environment variable (Render / Heroku / Supabase)
        env_db_url = os.environ.get("DATABASE_URL")
        if env_db_url and not is_testing:
            env_db_url = env_db_url.strip()
            if env_db_url.startswith("postgres://"):
                driver_prefix = self.get_postgres_driver_prefix()
                return env_db_url.replace("postgres://", driver_prefix, 1)
            elif env_db_url.startswith("postgresql://") and not env_db_url.startswith("postgresql+"):
                driver_prefix = self.get_postgres_driver_prefix()
                return env_db_url.replace("postgresql://", driver_prefix, 1)
            return env_db_url

        # 2. Check config settings
        db_type = str(self.config.get("db_type", "sqlite")).strip().lower()
        if db_type == "postgres":
            custom_url = str(self.config.get("db_custom_url", "")).strip()
            if custom_url:
                if custom_url.startswith("postgresql://") and not custom_url.startswith("postgresql+"):
                    driver_prefix = self.get_postgres_driver_prefix()
                    return custom_url.replace("postgresql://", driver_prefix, 1)
                elif custom_url.startswith("postgres://"):
                    driver_prefix = self.get_postgres_driver_prefix()
                    return custom_url.replace("postgres://", driver_prefix, 1)
                return custom_url
            host = str(self.config.get("db_host", "")).strip() or "127.0.0.1"
            if host.lower() == "localhost":
                host = "127.0.0.1"
            port = self.config.get("db_port", 5432) or 5432
            user = str(self.config.get("db_user", "")).strip()
            password = str(self.config.get("db_password", "")).strip()
            name = str(self.config.get("db_name", "")).strip()
            
            user_enc = urllib.parse.quote_plus(user)
            pass_enc = urllib.parse.quote_plus(password)
            
            driver_prefix = self.get_postgres_driver_prefix()
            if password:
                return f"{driver_prefix}{user_enc}:{pass_enc}@{host}:{port}/{name}"
            else:
                return f"{driver_prefix}{user_enc}@{host}:{port}/{name}"
        else:
            # SQLite
            is_testing_mode = (os.environ.get("TESTING") == "True") or (self.config.get("app_mode") == "pruebas")
            db_filename = "condo_manager_test.db" if is_testing_mode else "condo_manager.db"
            db_path = os.path.join(os.path.dirname(__file__), db_filename)
            return f"sqlite:///{db_path}"

    def run_migrations(self):
        # Simple schema migration: check if units has receipt_recipient_type column
        try:
            with self.engine.begin() as conn:
                conn.execute(text("ALTER TABLE units ADD COLUMN receipt_recipient_type VARCHAR DEFAULT 'general'"))
        except Exception:
            pass

        # Migrations for aliquots historical fields
        for col in ["owner_name", "cedula_owner", "email_owner", "phone_owner", "tenant_name", "cedula_tenant", "email_tenant", "phone_tenant", "paid_by"]:
            try:
                with self.engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE aliquots ADD COLUMN {col} VARCHAR DEFAULT ''"))
            except Exception:
                pass

        # Migrations for additional_charges historical fields
        for col in ["owner_name", "cedula_owner", "email_owner", "phone_owner", "tenant_name", "cedula_tenant", "email_tenant", "phone_tenant", "paid_by"]:
            try:
                with self.engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE additional_charges ADD COLUMN {col} VARCHAR DEFAULT ''"))
            except Exception:
                pass

        # Migrations for paid_amount column
        try:
            with self.engine.begin() as conn:
                conn.execute(text("ALTER TABLE aliquots ADD COLUMN paid_amount FLOAT DEFAULT 0.0"))
        except Exception:
            pass

        try:
            with self.engine.begin() as conn:
                conn.execute(text("ALTER TABLE additional_charges ADD COLUMN paid_amount FLOAT DEFAULT 0.0"))
        except Exception:
            pass

        # Migrations for system_configs condo_address and condo_ruc
        for col in ["condo_address", "condo_ruc"]:
            try:
                with self.engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE system_configs ADD COLUMN {col} VARCHAR DEFAULT ''"))
            except Exception:
                pass

    def seed_db_if_empty(self):
        with Session(self.engine) as session:
            # Check if Superadmin user exists (this will determine if we need to seed)
            any_super = session.exec(select(User).where(User.role == "superadmin")).first()
            if any_super is None:
                # Seed default Superadmin
                default_password_hash = hashlib.sha256("admin123".encode()).hexdigest()
                superadmin = User(
                    cedula="9999999999",
                    email="superadmin@condo.com",
                    name="Super Administrador",
                    role="superadmin",
                    password_hash=default_password_hash,
                    is_active=True
                )
                session.add(superadmin)
                session.commit()
                print("[DB Migration] Default Superadmin seeded.")

            # Seed SystemConfig table if empty
            db_cfg = session.exec(select(SystemConfig).where(SystemConfig.id == 1)).first()
            if db_cfg is None:
                file_cfg = self.load_config_from_file_or_defaults()
                db_cfg = SystemConfig(
                    id=1,
                    condo_name=file_cfg.get("condo_name", "Condominio El Mirador"),
                    currency=file_cfg.get("currency", "$"),
                    late_fee_day=int(file_cfg.get("late_fee_day", 15)),
                    late_fee_amount=float(file_cfg.get("late_fee_amount", 10.0)),
                    default_aliquot_base=float(file_cfg.get("default_aliquot_base", 70.0)),
                    receipt_recipient_type=file_cfg.get("receipt_recipient_type", "inquilino"),
                    exonerate_directiva=bool(file_cfg.get("exonerate_directiva", False)),
                    directive_president=file_cfg.get("directive_president", ""),
                    directive_treasurer=file_cfg.get("directive_treasurer", ""),
                    condo_address=file_cfg.get("condo_address", ""),
                    condo_ruc=file_cfg.get("condo_ruc", ""),
                    app_mode=file_cfg.get("app_mode", "produccion"),
                    use_google_sheets=bool(file_cfg.get("use_google_sheets", False)),
                    spreadsheet_id=file_cfg.get("spreadsheet_id", ""),
                    google_credentials_json=encrypt_secret(file_cfg.get("google_credentials_json", "")),
                    smtp_host=file_cfg.get("smtp_host", ""),
                    smtp_port=int(file_cfg.get("smtp_port", 587)),
                    smtp_user=file_cfg.get("smtp_user", ""),
                    smtp_password=encrypt_secret(file_cfg.get("smtp_password", "")),
                    smtp_from=file_cfg.get("smtp_from", ""),
                    whatsapp_provider=file_cfg.get("whatsapp_provider", "simulated"),
                    twilio_sid=file_cfg.get("twilio_sid", ""),
                    twilio_token=encrypt_secret(file_cfg.get("twilio_token", "")),
                    twilio_whatsapp_from=file_cfg.get("twilio_whatsapp_from", ""),
                    meta_wa_token=encrypt_secret(file_cfg.get("meta_wa_token", "")),
                    meta_wa_phone_number_id=file_cfg.get("meta_wa_phone_number_id", ""),
                    meta_wa_verify_token=encrypt_secret(file_cfg.get("meta_wa_verify_token", "")),
                    meta_wa_business_account_id=file_cfg.get("meta_wa_business_account_id", ""),
                    db_type=file_cfg.get("db_type", "sqlite"),
                    db_host=file_cfg.get("db_host", ""),
                    db_port=int(file_cfg.get("db_port", 5432)),
                    db_user=file_cfg.get("db_user", ""),
                    db_password=encrypt_secret(file_cfg.get("db_password", "")),
                    db_name=file_cfg.get("db_name", ""),
                    db_custom_url=encrypt_secret(file_cfg.get("db_custom_url", "")),
                    updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                )
                session.add(db_cfg)
                session.commit()
                print("[DB Migration] SystemConfig migrated to database with encrypted secrets.")

            # Seed InitialBalance table if empty
            db_bal = session.exec(select(InitialBalance).where(InitialBalance.id == 1)).first()
            if db_bal is None:
                file_cfg = self.load_config_from_file_or_defaults()
                db_bal = InitialBalance(
                    id=1,
                    initial_bank_balance=float(file_cfg.get("initial_bank_balance", 0.0)),
                    initial_reserve_fund=float(file_cfg.get("initial_reserve_fund", 0.0)),
                    cut_off_date=file_cfg.get("initial_balance_date", ""),
                    description="Saldo inicial de apertura de cuentas",
                    notes="",
                    updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                )
                session.add(db_bal)
                session.commit()
                print("[DB Migration] InitialBalance table initialized in database.")

            # Only seed mock test data from local_database.json if in TESTING mode and not using postgres
            db_type = str(self.config.get("db_type", "sqlite")).strip().lower()
            is_testing = (os.environ.get("TESTING") == "True")
            if db_type == "postgres" or not is_testing:
                return

            # Check if Units table is empty
            any_unit = session.exec(select(Unit)).first()
            if any_unit is None and os.path.exists(DB_PATH):
                try:
                    with open(DB_PATH, "r", encoding="utf-8") as f:
                        initial_data = json.load(f)
                    
                    # Legacy Cédula Mapping for existing units
                    cedula_map = {
                        "101": ("1700000101", "1700000102"),
                        "102": ("1700000103", ""),
                        "201": ("1700000201", ""),
                        "202": ("1700000202", ""),
                        "301": ("1700000301", ""),
                        "302": ("1700000302", ""),
                        "701": ("171391685", "")
                    }

                    # Seed Units
                    for u in initial_data.get("units", []):
                        unit_id = u["id"]
                        if unit_id in cedula_map:
                            u["cedula_owner"] = cedula_map[unit_id][0]
                            u["cedula_tenant"] = cedula_map[unit_id][1]
                        else:
                            u["cedula_owner"] = f"170000{unit_id}"
                            u["cedula_tenant"] = ""
                        session.add(Unit(**u))
                    
                    # Seed Aliquots
                    for a in initial_data.get("aliquots", []):
                        session.add(Aliquot(**a))
                        
                    # Seed Additional Charges
                    for c in initial_data.get("additional_charges", []):
                        session.add(AdditionalCharge(**c))
                        
                    # Seed Expenses
                    for e in initial_data.get("expenses", []):
                        session.add(Expense(**e))
                        
                    # Seed Bank statement
                    for b in initial_data.get("bank_statement", []):
                        session.add(BankTransaction(**b))
                        
                    # Seed Notification Logs
                    for n in initial_data.get("notification_logs", []):
                        session.add(NotificationLog(**n))
                        
                    session.commit()
                    print("[DB Migration] Initial data successfully migrated to condo_manager.db")
                except Exception as err:
                    print(f"[DB Migration] Error migrating initial data: {err}")

    def get_default_config_dict(self):
        return {
            "app_mode": "produccion",
            "spreadsheet_id": "",
            "google_credentials_json": "",
            "use_google_sheets": False,
            "late_fee_day": 15,
            "late_fee_amount": 10.0,
            "currency": "$",
            "initial_bank_balance": 0.0,
            "initial_reserve_fund": 0.0,
            "default_aliquot_base": 70.0,
            "smtp_host": "",
            "smtp_port": 587,
            "smtp_user": "",
            "smtp_password": "",
            "smtp_from": "",
            "twilio_sid": "",
            "twilio_token": "",
            "twilio_whatsapp_from": "",
            "whatsapp_provider": "simulated",
            "meta_wa_token": "",
            "meta_wa_phone_number_id": "",
            "meta_wa_verify_token": "",
            "meta_wa_business_account_id": "",
            "directive_president": "",
            "directive_treasurer": "",
            "condo_name": "Condominio El Mirador",
            "condo_address": "",
            "condo_ruc": "",
            "exonerate_directiva": False,
            "receipt_recipient_type": "inquilino",
            "db_type": "sqlite",
            "db_host": "",
            "db_port": 5432,
            "db_user": "",
            "db_password": "",
            "db_name": "",
            "db_custom_url": ""
        }

    def load_config_from_file_or_defaults(self):
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    defaults = self.get_default_config_dict()
                    for k, v in defaults.items():
                        if k not in cfg:
                            cfg[k] = v
                    return cfg
            except Exception:
                pass
        return self.get_default_config_dict()

    def load_config(self):
        # 1. Try reading from Database
        if getattr(self, "engine", None):
            try:
                with Session(self.engine) as session:
                    db_cfg = session.exec(select(SystemConfig).where(SystemConfig.id == 1)).first()
                    db_bal = session.exec(select(InitialBalance).where(InitialBalance.id == 1)).first()
                    if db_cfg:
                        cfg = db_cfg.model_dump()
                        # Decrypt sensitive secrets in memory for application operations
                        cfg["smtp_password"] = decrypt_secret(db_cfg.smtp_password)
                        cfg["twilio_token"] = decrypt_secret(db_cfg.twilio_token)
                        cfg["meta_wa_token"] = decrypt_secret(db_cfg.meta_wa_token)
                        cfg["meta_wa_verify_token"] = decrypt_secret(db_cfg.meta_wa_verify_token)
                        cfg["db_password"] = decrypt_secret(db_cfg.db_password)
                        cfg["db_custom_url"] = decrypt_secret(db_cfg.db_custom_url)
                        if db_cfg.google_credentials_json:
                            cfg["google_credentials_json"] = decrypt_secret(db_cfg.google_credentials_json)
                            
                        if db_bal:
                            cfg["initial_bank_balance"] = float(db_bal.initial_bank_balance or 0.0)
                            cfg["initial_reserve_fund"] = float(db_bal.initial_reserve_fund or 0.0)
                        else:
                            cfg["initial_bank_balance"] = 0.0
                            cfg["initial_reserve_fund"] = 0.0
                        return cfg
            except Exception as e:
                pass

        # 2. Fallback to file or defaults
        return self.load_config_from_file_or_defaults()

    def save_config(self):
        # We temporarily update engine mode. If it fails, it will raise ValueError and NOT save to file!
        self.update_engine_mode()
        
        # Save to database if engine is connected
        if getattr(self, "engine", None):
            try:
                with Session(self.engine) as session:
                        db_cfg = session.exec(select(SystemConfig).where(SystemConfig.id == 1)).first()
                        if not db_cfg:
                            db_cfg = SystemConfig(id=1)
                            session.add(db_cfg)
                            
                        for k, v in self.config.items():
                            if hasattr(db_cfg, k):
                                if k in ["smtp_password", "twilio_token", "meta_wa_token", "meta_wa_verify_token", "db_password", "db_custom_url", "google_credentials_json"]:
                                    setattr(db_cfg, k, encrypt_secret(v))
                                else:
                                    setattr(db_cfg, k, v)
                        db_cfg.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        
                        db_bal = session.exec(select(InitialBalance).where(InitialBalance.id == 1)).first()
                        if not db_bal:
                            db_bal = InitialBalance(id=1)
                            session.add(db_bal)
                        db_bal.initial_bank_balance = float(self.config.get("initial_bank_balance", 0.0))
                        db_bal.initial_reserve_fund = float(self.config.get("initial_reserve_fund", 0.0))
                        db_bal.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        
                        session.commit()
            except Exception as err:
                print(f"[DB Error] Error saving config to database: {err}")
                
        # Also write a clean config.json for fallback / offline reference
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def update_engine_mode(self):
        db_url = self.build_db_url()
        if getattr(self, "current_db_url", None) != db_url:
            old_db_url = getattr(self, "current_db_url", None)
            old_engine = getattr(self, "engine", None)
            try:
                temp_engine = create_engine(db_url, echo=False)
                with temp_engine.connect() as conn:
                    pass
                
                # If connection succeeds, apply
                self.current_db_url = db_url
                self.engine = temp_engine
                
                # Setup tables and load data
                SQLModel.metadata.create_all(self.engine)
                self.run_migrations()
                self.seed_db_if_empty()
                self.load_data()
            except Exception as e:
                # Revert memory states if we failed to connect
                if old_db_url and old_engine:
                    self.current_db_url = old_db_url
                    self.engine = old_engine
                    self.config = self.load_config()
                raise ValueError(f"Error al conectar a la base de datos: {e}")

    def load_data(self):
        self.data = {
            "units": [],
            "aliquots": [],
            "additional_charges": [],
            "expenses": [],
            "bank_statement": [],
            "notification_logs": [],
            "other_incomes": [],
            "board_members": [],
            "system_config": None,
            "initial_balances": None
        }
        
        # Load from SQLite / PostgreSQL
        try:
            with Session(self.engine) as session:
                self.data["units"] = [u.model_dump() for u in session.exec(select(Unit)).all()]
                self.data["aliquots"] = [a.model_dump() for a in session.exec(select(Aliquot)).all()]
                self.data["additional_charges"] = [c.model_dump() for c in session.exec(select(AdditionalCharge)).all()]
                self.data["expenses"] = [e.model_dump() for e in session.exec(select(Expense)).all()]
                self.data["bank_statement"] = [b.model_dump() for b in session.exec(select(BankTransaction)).all()]
                self.data["notification_logs"] = [n.model_dump() for n in session.exec(select(NotificationLog)).all()]
                self.data["other_incomes"] = [o.model_dump() for o in session.exec(select(OtherIncome)).all()]
                self.data["board_members"] = [m.model_dump() for m in session.exec(select(BoardMember)).all()]
                db_cfg = session.exec(select(SystemConfig).where(SystemConfig.id == 1)).first()
                if db_cfg:
                    self.data["system_config"] = db_cfg.model_dump()
                db_bal = session.exec(select(InitialBalance).where(InitialBalance.id == 1)).first()
                if db_bal:
                    self.data["initial_balances"] = db_bal.model_dump()
        except Exception as e:
            safe_err = str(e).encode('ascii', errors='replace').decode('ascii')
            print(f"[DB Error] Error loading data: {safe_err}")

        # Try to load Google Sheets if enabled
        if self.config.get("use_google_sheets") and self.config.get("spreadsheet_id") and self.config.get("google_credentials_json"):
            try:
                self.connect_google_sheets()
                self.pull_from_google_sheets()
                self.save_local_db()
            except Exception as e:
                print(f"Error connecting to Google Sheets, using local database: {e}")
                # Fallback to local DB
                self.config["use_google_sheets"] = False

    def connect_google_sheets(self):
        creds_data = json.loads(self.config["google_credentials_json"])
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = Credentials.from_service_account_info(creds_data, scopes=scopes)
        self.client = gspread.authorize(creds)
        self.spreadsheet = self.client.open_by_key(self.config["spreadsheet_id"])

    def pull_from_google_sheets(self):
        # We fetch sheet by sheet. If they don't exist, initialize them.
        self.data["units"] = self.get_or_create_sheet("Unidades", ["ID", "Propietario", "Telefono1", "Mail1", "Inquilino", "Telefono2", "Mail2", "AlicuotaBase", "DestinatarioRecibo"], self.data["units"])
        self.data["aliquots"] = self.get_or_create_sheet("Alicuotas", ["ID", "Unidad", "Mes", "Año", "Monto", "Multa", "FechaPago", "Referencia", "Estado", "ComprobanteURL", "PropietarioNombre", "PropietarioCedula", "PropietarioEmail", "PropietarioTelefono", "InquilinoNombre", "InquilinoCedula", "InquilinoEmail", "InquilinoTelefono", "PagadoPor"], self.data["aliquots"])
        self.data["additional_charges"] = self.get_or_create_sheet("CargosAdicionales", ["ID", "Unidad", "Tipo", "Descripción", "Monto", "FechaEmisión", "Estado", "FechaPago", "Referencia", "ComprobanteURL", "PropietarioNombre", "PropietarioCedula", "PropietarioEmail", "PropietarioTelefono", "InquilinoNombre", "InquilinoCedula", "InquilinoEmail", "InquilinoTelefono", "PagadoPor"], self.data["additional_charges"])
        self.data["expenses"] = self.get_or_create_sheet("Gastos", ["ID", "Fecha", "Categoría", "Descripción", "Monto"], self.data["expenses"])
        self.data["bank_statement"] = self.get_or_create_sheet("EstadoCuenta", ["Fecha", "Referencia", "Monto", "Detalle", "Conciliado"], self.data["bank_statement"])
        self.data["other_incomes"] = self.get_or_create_sheet("OtrosIngresos", ["ID", "Fecha", "Concepto", "Monto", "Referencia"], self.data["other_incomes"])
        self.data["board_members"] = self.get_or_create_sheet("Directiva", ["ID", "Depto", "Nombre", "Cargo"], self.data["board_members"])

    def get_or_create_sheet(self, sheet_name, headers, default_data):
        try:
            worksheet = self.spreadsheet.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = self.spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=10)
            worksheet.append_row(headers)
            # Push default data
            rows = []
            for row in default_data:
                rows.append([row.get(h.lower().replace("año", "year").replace("fechaemisión", "issue_date").replace("fechapago", "payment_date").replace("descripción", "description").replace("categoría", "category").replace("alicuotabase", "aliquot_base").replace("multa", "late_fee").replace("destinatariorecibo", "receipt_recipient_type").replace("propietarionombre", "owner_name").replace("propietariocedula", "cedula_owner").replace("propietarioemail", "email_owner").replace("propietariotelefono", "phone_owner").replace("inquilinonombre", "tenant_name").replace("inquilinocedula", "cedula_tenant").replace("inquilinoemail", "email_tenant").replace("inquilinotelefono", "phone_tenant").replace("pagadopor", "paid_by")) for h in headers])
            if rows:
                worksheet.append_rows(rows)
            return default_data

        records = worksheet.get_all_records()
        if not records and default_data:
            rows = []
            for row in default_data:
                rows.append([row.get(h.lower().replace("año", "year").replace("fechaemisión", "issue_date").replace("fechapago", "payment_date").replace("descripción", "description").replace("categoría", "category").replace("alicuotabase", "aliquot_base").replace("multa", "late_fee").replace("destinatariorecibo", "receipt_recipient_type").replace("propietarionombre", "owner_name").replace("propietariocedula", "cedula_owner").replace("propietarioemail", "email_owner").replace("propietariotelefono", "phone_owner").replace("inquilinonombre", "tenant_name").replace("inquilinocedula", "cedula_tenant").replace("inquilinoemail", "email_tenant").replace("inquilinotelefono", "phone_tenant").replace("pagadopor", "paid_by")) for h in headers])
            if rows:
                worksheet.append_rows(rows)
            return default_data

        parsed_records = []
        for r in records:
            # Map sheet headers to python dict keys
            item = {}
            for k, v in r.items():
                key_map = {
                    "ID": "id", "Unidad": "unit", "Mes": "month", "Año": "year", 
                    "Monto": "amount", "Multa": "late_fee", "FechaPago": "payment_date", 
                    "Referencia": "reference", "Estado": "status", "Propietario": "owner",
                    "AlicuotaBase": "aliquot_base", "Tipo": "type", "Descripción": "description",
                    "FechaEmisión": "issue_date", "Fecha": "date", "Categoría": "category",
                    "Detalle": "detail", "Conciliado": "reconciled",
                    "Telefono1": "phone1", "Mail1": "email1", "Inquilino": "tenant",
                    "Telefono2": "phone2", "Mail2": "email2", "ComprobanteURL": "comprobante_url",
                    "DestinatarioRecibo": "receipt_recipient_type",
                    "PropietarioNombre": "owner_name",
                    "PropietarioCedula": "cedula_owner",
                    "PropietarioEmail": "email_owner",
                    "PropietarioTelefono": "phone_owner",
                    "InquilinoNombre": "tenant_name",
                    "InquilinoCedula": "cedula_tenant",
                    "InquilinoEmail": "email_tenant",
                    "InquilinoTelefono": "phone_tenant",
                    "PagadoPor": "paid_by"
                }
                mapped_key = key_map.get(k, k.lower())
                
                # Format conversions
                if mapped_key in ["amount", "late_fee", "aliquot_base"]:
                    try:
                        item[mapped_key] = float(v) if v != "" else 0.0
                    except:
                        item[mapped_key] = 0.0
                elif mapped_key in ["year"]:
                    try:
                        item[mapped_key] = int(v)
                    except:
                        item[mapped_key] = 2026
                elif mapped_key == "reconciled":
                    item[mapped_key] = str(v).lower() in ["true", "sí", "si", "yes", "1"]
                else:
                    item[mapped_key] = str(v)
            parsed_records.append(item)
        return parsed_records

    def push_to_google_sheets(self, sheet_name, data_list, headers):
        if not self.config.get("use_google_sheets") or not self.spreadsheet:
            return
        try:
            worksheet = self.spreadsheet.worksheet(sheet_name)
            # Clear all except header
            worksheet.resize(rows=1)
            worksheet.resize(rows=1000)
            
            rows = []
            for item in data_list:
                row = []
                for h in headers:
                    key = h.lower().replace("año", "year").replace("fechaemisión", "issue_date").replace("fechapago", "payment_date").replace("descripción", "description").replace("categoría", "category").replace("alicuotabase", "aliquot_base").replace("multa", "late_fee").replace("telefono1", "phone1").replace("mail1", "email1").replace("inquilino", "tenant").replace("telefono2", "phone2").replace("mail2", "email2").replace("comprobanteurl", "comprobante_url").replace("destinatariorecibo", "receipt_recipient_type").replace("propietarionombre", "owner_name").replace("propietariocedula", "cedula_owner").replace("propietarioemail", "email_owner").replace("propietariotelefono", "phone_owner").replace("inquilinonombre", "tenant_name").replace("inquilinocedula", "cedula_tenant").replace("inquilinoemail", "email_tenant").replace("inquilinotelefono", "phone_tenant").replace("pagadopor", "paid_by")
                    val = item.get(key, "")
                    if isinstance(val, bool):
                        val = "Sí" if val else "No"
                    row.append(val)
                rows.append(row)
            if rows:
                worksheet.append_rows(rows)
        except Exception as e:
            print(f"Error pushing to Google Sheets: {e}")

    def sync_user_from_unit(self, session, unit):
        # Synchronize owner
        if unit.get("cedula_owner") and unit.get("email1"):
            user = session.exec(select(User).where(User.email == unit["email1"])).first()
            if not user:
                user = session.exec(select(User).where(User.cedula == unit["cedula_owner"])).first()
            
            if not user:
                user = User(
                    cedula=unit["cedula_owner"],
                    email=unit["email1"],
                    name=unit["owner"],
                    role="owner",
                    is_active=True
                )
                session.add(user)
            else:
                user.cedula = unit["cedula_owner"]
                user.email = unit["email1"]
                user.name = unit["owner"]
                user.role = "owner"
                user.is_active = True
                session.add(user)
        
        # Synchronize tenant
        if unit.get("cedula_tenant") and unit.get("email2"):
            user = session.exec(select(User).where(User.email == unit["email2"])).first()
            if not user:
                user = session.exec(select(User).where(User.cedula == unit["cedula_tenant"])).first()
            
            if not user:
                user = User(
                    cedula=unit["cedula_tenant"],
                    email=unit["email2"],
                    name=unit["tenant"],
                    role="tenant",
                    is_active=True
                )
                session.add(user)
            else:
                user.cedula = unit["cedula_tenant"]
                user.email = unit["email2"]
                user.name = unit["tenant"]
                user.role = "tenant"
                user.is_active = True
                session.add(user)

    def save_local_db(self):
        try:
            with Session(self.engine) as session:
                # Merge Units and Sync Users
                for u in self.data["units"]:
                    session.merge(Unit(**u))
                    self.sync_user_from_unit(session, u)
                
                # Merge Aliquots
                for a in self.data["aliquots"]:
                    session.merge(Aliquot(**a))
                
                # Merge Additional Charges
                for c in self.data["additional_charges"]:
                    session.merge(AdditionalCharge(**c))
                
                # Merge Expenses
                for e in self.data["expenses"]:
                    session.merge(Expense(**e))
                
                # Merge Bank Transactions
                for b in self.data["bank_statement"]:
                    session.merge(BankTransaction(**b))
                
                # Merge Notification Logs correctly handling new auto-increment IDs
                for n in self.data["notification_logs"]:
                    if n.get("id") is None:
                        db_n = NotificationLog(**n)
                        session.add(db_n)
                        session.flush() # assign ID
                        n["id"] = db_n.id
                    else:
                        session.merge(NotificationLog(**n))
                        
                # Merge Other Incomes correctly handling new auto-increment IDs
                for o in self.data["other_incomes"]:
                    if o.get("id") is None:
                        db_o = OtherIncome(**o)
                        session.add(db_o)
                        session.flush()
                        o["id"] = db_o.id
                    else:
                        session.merge(OtherIncome(**o))
                        
                # Merge Board Members correctly handling new auto-increment IDs
                for m in self.data["board_members"]:
                    if m.get("id") is None:
                        db_m = BoardMember(**m)
                        session.add(db_m)
                        session.flush()
                        m["id"] = db_m.id
                    else:
                        session.merge(BoardMember(**m))
                        
                session.commit()
        except Exception as e:
            safe_err = str(e).encode('ascii', errors='replace').decode('ascii')
            print(f"[DB Error] Error saving data: {safe_err}")

    def sync(self):
        self.save_local_db()
        if self.config.get("use_google_sheets"):
            try:
                self.push_to_google_sheets("Unidades", self.data["units"], ["ID", "Propietario", "Telefono1", "Mail1", "Inquilino", "Telefono2", "Mail2", "AlicuotaBase", "DestinatarioRecibo"])
                self.push_to_google_sheets("Alicuotas", self.data["aliquots"], ["ID", "Unidad", "Mes", "Año", "Monto", "Multa", "FechaPago", "Referencia", "Estado", "ComprobanteURL", "PropietarioNombre", "PropietarioCedula", "PropietarioEmail", "PropietarioTelefono", "InquilinoNombre", "InquilinoCedula", "InquilinoEmail", "InquilinoTelefono", "PagadoPor"])
                self.push_to_google_sheets("CargosAdicionales", self.data["additional_charges"], ["ID", "Unidad", "Tipo", "Descripción", "Monto", "FechaEmisión", "Estado", "FechaPago", "Referencia", "ComprobanteURL", "PropietarioNombre", "PropietarioCedula", "PropietarioEmail", "PropietarioTelefono", "InquilinoNombre", "InquilinoCedula", "InquilinoEmail", "InquilinoTelefono", "PagadoPor"])
                self.push_to_google_sheets("Gastos", self.data["expenses"], ["ID", "Fecha", "Categoría", "Descripción", "Monto"])
                self.push_to_google_sheets("EstadoCuenta", self.data["bank_statement"], ["Fecha", "Referencia", "Monto", "Detalle", "Conciliado"])
                self.push_to_google_sheets("OtrosIngresos", self.data["other_incomes"], ["ID", "Fecha", "Concepto", "Monto", "Referencia"])
                self.push_to_google_sheets("Directiva", self.data["board_members"], ["ID", "Depto", "Nombre", "Cargo"])
            except Exception as e:
                print(f"Error syncing with Google Sheets: {e}")

    def get_summary(self):
        # Calculate revenue, expenses, debtors
        # Get base amount mapping for units
        unit_bases = {}
        default_base = float(self.config.get("default_aliquot_base", 70.0))
        for u in self.data["units"]:
            unit_bases[u["id"]] = float(u.get("aliquot_base", default_base))

        total_revenue = 0.0
        
        # Aliquot revenue (full payments and partial abonos)
        for a in self.data["aliquots"]:
            # Exonerated board members do NOT generate money income
            if a.get("status") == "Exonerado" or str(a.get("reference", "")).startswith("EXONERADO"):
                continue
            amt = float(a.get("amount", 0.0) or 0.0)
            fee = float(a.get("late_fee", 0.0) or 0.0)
            paid = float(a.get("paid_amount", 0.0) or 0.0)
            if a.get("status") == "Pagado":
                total_revenue += amt + fee
            elif paid > 0.0:
                total_revenue += paid

        # Additional charges revenue (extraordinary fees, fines, initial debt, debt abonos)
        for c in self.data["additional_charges"]:
            if c.get("status") == "Exonerado" or str(c.get("reference", "")).startswith("EXONERADO"):
                continue
            amt = float(c.get("amount", 0.0) or 0.0)
            paid = float(c.get("paid_amount", 0.0) or 0.0)
            if c.get("status") == "Pagado":
                total_revenue += paid if paid > 0.0 else amt
            elif paid > 0.0:
                total_revenue += paid
                
        # Other incomes revenue (bank interest, rentals, donations, miscellaneous)
        total_revenue += sum(float(o.get("amount", 0.0) or 0.0) for o in self.data.get("other_incomes", []))
        
        total_expenses = sum(e["amount"] for e in self.data["expenses"])
        
        # Calculate debtors list
        debtors_summary = self.get_debtors()
        total_debt = sum(d["total_debt"] for d in debtors_summary)
        
        # Calculate monthly collection progress for the current/latest month
        months_order = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
        now = datetime.now()
        current_month = months_order[now.month - 1]
        current_year = now.year
        
        curr_month_aliquots = [a for a in self.data["aliquots"] if a["month"] == current_month and int(a["year"]) == current_year and a.get("status") != "Exonerado"]
        if not curr_month_aliquots and self.data["aliquots"]:
            latest_aliquot = max(self.data["aliquots"], key=lambda x: (int(x.get("year", 0)), months_order.index(x.get("month", "Enero")) if x.get("month") in months_order else 0))
            current_month = latest_aliquot.get("month")
            current_year = int(latest_aliquot.get("year"))
            curr_month_aliquots = [a for a in self.data["aliquots"] if a["month"] == current_month and int(a["year"]) == current_year and a.get("status") != "Exonerado"]
            
        paid_curr = sum(1 for a in curr_month_aliquots if a["status"] == "Pagado")
        total_curr = len(curr_month_aliquots)
        progress = (paid_curr / total_curr * 100) if total_curr > 0 else 0
        initial_balance = 0.0
        if self.data.get("initial_balances") and self.data["initial_balances"].get("initial_bank_balance") is not None:
            initial_balance = float(self.data["initial_balances"]["initial_bank_balance"])
        else:
            initial_balance = float(self.config.get("initial_bank_balance", 0.0))
        
        return {
            "total_revenue": total_revenue,
            "total_expenses": total_expenses,
            "net_balance": initial_balance + total_revenue - total_expenses,
            "total_debt": total_debt,
            "debtors_count": len([d for d in debtors_summary if d["total_debt"] > 0]),
            "current_month_progress": round(progress, 1),
            "currency": self.config.get("currency", "$"),
            "initial_bank_balance": initial_balance
        }

    def get_debtors(self):
        # Group by unit
        debtors = {}
        for u in self.data["units"]:
            u_id = str(u["id"]).strip()
            debtors[u_id] = {
                "unit": u_id,
                "owner": u.get("owner", ""),
                "unpaid_aliquots_count": 0,
                "unpaid_aliquots_amount": 0.0,
                "unpaid_charges_amount": 0.0,
                "late_fees": 0.0,
                "total_debt": 0.0,
                "details": []
            }
            
        def _get_entry(raw_uid):
            clean_uid = str(raw_uid).strip()
            if clean_uid not in debtors:
                u_obj = next((u for u in self.data["units"] if str(u.get("id")).strip() == clean_uid), {})
                debtors[clean_uid] = {
                    "unit": clean_uid,
                    "owner": u_obj.get("owner") or f"Depto {clean_uid}",
                    "unpaid_aliquots_count": 0,
                    "unpaid_aliquots_amount": 0.0,
                    "unpaid_charges_amount": 0.0,
                    "late_fees": 0.0,
                    "total_debt": 0.0,
                    "details": []
                }
            return debtors[clean_uid]

        # Add unpaid aliquots
        for a in self.data["aliquots"]:
            if a.get("status") in ["Pendiente", "Validación Manual"]:
                unit_entry = _get_entry(a.get("unit"))
                
                stored_fee = float(a.get("late_fee", 0.0) or 0.0)
                amt = float(a.get("amount", 0.0) or 0.0)
                
                if amt > 0:
                    implied_fee = self.calculate_pending_aliquot_late_fee(
                        a.get("month"),
                        a.get("year"),
                        current_late_fee=stored_fee
                    )
                else:
                    implied_fee = stored_fee
                
                if amt > 0 or implied_fee > 0:
                    if amt > 0:
                        unit_entry["unpaid_aliquots_count"] += 1
                    unit_entry["unpaid_aliquots_amount"] += amt
                    unit_entry["late_fees"] += implied_fee
                    
                    if amt > 0 and implied_fee > 0:
                        detail_str = f"Alícuota {a.get('month')} {a.get('year')} ({self.config.get('currency', '$')}{amt:.2f}) + Multa ({self.config.get('currency', '$')}{implied_fee:.2f})"
                    elif amt > 0:
                        detail_str = f"Alícuota {a.get('month')} {a.get('year')} ({self.config.get('currency', '$')}{amt:.2f})"
                    else:
                        detail_str = f"Multa pendiente {a.get('month')} {a.get('year')} ({self.config.get('currency', '$')}{implied_fee:.2f})"
                    unit_entry["details"].append(detail_str)

        # Add unpaid additional charges
        for c in self.data["additional_charges"]:
            if c.get("status") in ["Pendiente", "Validación Manual"]:
                unit_entry = _get_entry(c.get("unit"))
                amt = float(c.get("amount", 0.0) or 0.0)
                unit_entry["unpaid_charges_amount"] += amt
                unit_entry["details"].append(f"{c.get('type')}: {c.get('description')} ({self.config.get('currency', '$')}{amt:.2f})")

        # Final totals
        result = []
        for unit_id, d in debtors.items():
            d["total_debt"] = round(d["unpaid_aliquots_amount"] + d["unpaid_charges_amount"] + d["late_fees"], 2)
            if d["total_debt"] > 0:
                result.append(d)
                
        # Sort by highest debt
        result.sort(key=lambda x: x["total_debt"], reverse=True)
        return result

    def get_aliquots_with_details(self):
        # We merge aliquot info with owner details
        owner_map = {u["id"]: u["owner"] for u in self.data["units"]}
        enhanced_aliquots = []
        for a in self.data["aliquots"]:
            item = a.copy()
            item["owner"] = owner_map.get(a["unit"], "Desconocido")
            if item.get("status") in ["Pendiente", "Validación Manual"]:
                stored_fee = float(item.get("late_fee", 0.0) or 0.0)
                item["late_fee"] = self.calculate_pending_aliquot_late_fee(
                    item.get("month"),
                    item.get("year"),
                    current_late_fee=stored_fee
                )
            enhanced_aliquots.append(item)
        return enhanced_aliquots

    def get_expenses_list(self):
        return sorted(self.data["expenses"], key=lambda x: x["date"], reverse=True)

    def get_additional_charges_with_details(self):
        owner_map = {u["id"]: u["owner"] for u in self.data["units"]}
        enhanced_charges = []
        for c in self.data["additional_charges"]:
            item = c.copy()
            item["owner"] = owner_map.get(c["unit"], "Desconocido")
            enhanced_charges.append(item)
        return enhanced_charges

    def get_bank_statement_list(self):
        return sorted(self.data["bank_statement"], key=lambda x: x["date"], reverse=True)

    def get_payments_report_data(self, start_date=None, end_date=None, unit=None, category=None):
        """
        Gathers all collected payments across:
        - Paid aliquots & partial abonos
        - Paid additional charges & fines
        - Other condominium incomes
        """
        rows = []
        owner_map = {str(u["id"]): u for u in self.data["units"]}
        
        # 1. Aliquots
        for a in self.data.get("aliquots", []):
            is_paid = (a.get("status") == "Pagado")
            paid_amount = float(a.get("paid_amount", 0.0) or 0.0)
            aliquot_amount = float(a.get("amount", 0.0) or 0.0)
            late_fee = float(a.get("late_fee", 0.0) or 0.0)
            
            if is_paid or paid_amount > 0:
                base_collected = paid_amount if (paid_amount > 0.0 and (not is_paid or aliquot_amount == 0.0)) else aliquot_amount
                amount_collected = (base_collected + late_fee) if is_paid else paid_amount
                pay_date = a.get("payment_date") or ""
                unit_str = str(a.get("unit", ""))
                u_obj = owner_map.get(unit_str, {})
                
                payer = a.get("paid_by") or a.get("owner_name") or u_obj.get("owner") or a.get("tenant_name") or u_obj.get("tenant") or self.get_receipt_recipient_name(unit_str)
                
                status_label = "Pagado Total" if is_paid else "Abono Parcial"
                concept = f"Alícuota Ordinaria {a.get('month')} {a.get('year')}"
                if not is_paid:
                    concept = f"Abono Alícuota {a.get('month')} {a.get('year')}"
                    
                rows.append({
                    "id": a.get("id"),
                    "date": pay_date,
                    "unit": unit_str,
                    "category": "Alícuota",
                    "concept": concept,
                    "payer": payer,
                    "reference": a.get("reference") or "-",
                    "amount": round(amount_collected, 2),
                    "base_amount": round(base_collected if is_paid else paid_amount, 2),
                    "late_fee": round(late_fee if is_paid else 0.0, 2),
                    "status": status_label,
                    "is_reconciled": True,
                    "comprobante_url": a.get("comprobante_url") or "",
                    "receipt_url": f"/api/receipts/aliquot/{a.get('id')}/pdf" if is_paid else ""
                })

        # 2. Additional Charges
        for c in self.data.get("additional_charges", []):
            is_paid = (c.get("status") == "Pagado")
            paid_amount = float(c.get("paid_amount", 0.0) or 0.0)
            charge_amount = float(c.get("amount", 0.0) or 0.0)
            
            if is_paid or paid_amount > 0:
                amount_collected = paid_amount if paid_amount > 0.0 else charge_amount
                pay_date = c.get("payment_date") or c.get("issue_date") or ""
                unit_str = str(c.get("unit", ""))
                u_obj = owner_map.get(unit_str, {})
                
                payer = c.get("paid_by") or c.get("owner_name") or u_obj.get("owner") or c.get("tenant_name") or u_obj.get("tenant") or self.get_receipt_recipient_name(unit_str)
                
                charge_type = c.get("type") or "Cargo Adicional"
                status_label = "Pagado Total" if is_paid else "Abono Parcial"
                concept = f"{charge_type}: {c.get('description', '')}"
                
                rows.append({
                    "id": c.get("id"),
                    "date": pay_date,
                    "unit": unit_str,
                    "category": charge_type,
                    "concept": concept,
                    "payer": payer,
                    "reference": c.get("reference") or "-",
                    "amount": round(amount_collected, 2),
                    "base_amount": round(amount_collected, 2),
                    "late_fee": 0.0,
                    "status": status_label,
                    "is_reconciled": True,
                    "comprobante_url": c.get("comprobante_url") or "",
                    "receipt_url": f"/api/receipts/charge/{c.get('id')}/pdf" if is_paid else ""
                })

        # 3. Other Incomes
        for o in self.data.get("other_incomes", []):
            amount_collected = float(o.get("amount", 0.0) or 0.0)
            rows.append({
                "id": f"OI-{o.get('id', '')}",
                "date": o.get("date") or "",
                "unit": "N/A",
                "category": "Otro Ingreso",
                "concept": o.get("concept") or "Otro Ingreso",
                "payer": "Condominio / Terceros",
                "reference": o.get("reference") or "-",
                "amount": round(amount_collected, 2),
                "base_amount": round(amount_collected, 2),
                "late_fee": 0.0,
                "status": "Recibido",
                "is_reconciled": True,
                "comprobante_url": "",
                "receipt_url": ""
            })

        # 4. Unreconciled Bank Statement Transactions (Movimientos no conciliados del estado de cuenta)
        for b in self.data.get("bank_statement", []):
            if not b.get("reconciled"):
                b_amt = float(b.get("amount", 0.0) or 0.0)
                ref = str(b.get("reference") or "-")
                detail = b.get("detail") or "Depósito en cuenta bancaria"
                rows.append({
                    "id": f"BS-{ref}",
                    "date": b.get("date") or "",
                    "unit": "- / Banco",
                    "category": "Depósito No Conciliado",
                    "concept": f"Depósito en Banco (No Conciliado): {detail}",
                    "payer": detail,
                    "reference": ref,
                    "amount": round(b_amt, 2),
                    "base_amount": round(b_amt, 2),
                    "late_fee": 0.0,
                    "status": "No Conciliado",
                    "is_reconciled": False,
                    "comprobante_url": "",
                    "receipt_url": ""
                })

        # Apply Filters
        filtered_rows = []
        for r in rows:
            # Date filtering
            if start_date and r["date"] and r["date"] < start_date:
                continue
            if end_date and r["date"] and r["date"] > end_date:
                continue
            # Unit filtering
            if unit and unit != "all" and str(r["unit"]) != str(unit):
                continue
            # Category filtering
            if category and category != "all":
                cat_lower = category.lower()
                r_cat_lower = r["category"].lower()
                if cat_lower == "alicuotas" and "alícuota" not in r_cat_lower:
                    continue
                elif cat_lower == "multas" and "multa" not in r_cat_lower:
                    continue
                elif cat_lower == "extras" and not any(x in r_cat_lower for x in ["extra", "deuda"]):
                    continue
                elif cat_lower == "otros" and "otro" not in r_cat_lower:
                    continue
                elif cat_lower in ["no_conciliados", "noconciliados", "no_conciliado"] and "no conciliado" not in r_cat_lower:
                    continue
                elif cat_lower in ["conciliados", "conciliado"] and not r.get("is_reconciled", True):
                    continue
            filtered_rows.append(r)

        # Sort by date descending
        filtered_rows.sort(key=lambda x: x["date"] or "0000-00-00", reverse=True)

        # Calculate KPIs
        reconciled_rows = [r for r in filtered_rows if r.get("is_reconciled", True)]
        unreconciled_rows = [r for r in filtered_rows if not r.get("is_reconciled", True)]

        total_reconciled = sum(r["amount"] for r in reconciled_rows)
        total_unreconciled = sum(r["amount"] for r in unreconciled_rows)
        total_collected = round(total_reconciled + total_unreconciled, 2)

        total_aliquots = sum(r["amount"] for r in reconciled_rows if "alícuota" in r["category"].lower())
        total_charges = sum(r["amount"] for r in reconciled_rows if any(x in r["category"].lower() for x in ["multa", "extra", "deuda", "cargo"]))
        total_other_incomes = sum(r["amount"] for r in reconciled_rows if "otro" in r["category"].lower())

        kpis = {
            "total_collected": total_collected,
            "total_global": total_collected,
            "total_amount": total_collected,
            "total_reconciled": round(total_reconciled, 2),
            "total_unreconciled": round(total_unreconciled, 2),
            "total_aliquots": round(total_aliquots, 2),
            "total_charges": round(total_charges, 2),
            "total_other_incomes": round(total_other_incomes, 2),
            "count_reconciled": len(reconciled_rows),
            "count_unreconciled": len(unreconciled_rows),
            "count_payments": len(filtered_rows),
            "total_count": len(filtered_rows)
        }

        return {
            "report_type": "payments",
            "kpis": kpis,
            "rows": filtered_rows,
            "records": filtered_rows,
            "filters": {
                "start_date": start_date or "",
                "end_date": end_date or "",
                "unit": unit or "all",
                "category": category or "all"
            }
        }

    def get_debtors_report_data(self, unit=None, min_debt=0.0, status_filter=None):
        """
        Consolidates debts per unit including unpaid aliquots, late fees, and additional charges.
        """
        min_debt = float(min_debt or 0.0)
        debtors_map = {}

        for u in self.data.get("units", []):
            u_id = str(u["id"]).strip()
            debtors_map[u_id] = {
                "unit": u_id,
                "owner": u.get("owner", ""),
                "phone": u.get("phone1") or u.get("phone2") or "-",
                "email": u.get("email1") or u.get("email2") or "-",
                "unpaid_aliquots_count": 0,
                "aliquots_debt": 0.0,
                "late_fees": 0.0,
                "charges_debt": 0.0,
                "total_debt": 0.0,
                "unpaid_periods": [],
                "unpaid_charges": []
            }

        def _get_entry(raw_uid):
            clean_uid = str(raw_uid).strip()
            if clean_uid not in debtors_map:
                u_obj = next((u for u in self.data.get("units", []) if str(u.get("id")).strip() == clean_uid), {})
                debtors_map[clean_uid] = {
                    "unit": clean_uid,
                    "owner": u_obj.get("owner") or f"Depto {clean_uid}",
                    "phone": u_obj.get("phone1") or u_obj.get("phone2") or "-",
                    "email": u_obj.get("email1") or u_obj.get("email2") or "-",
                    "unpaid_aliquots_count": 0,
                    "aliquots_debt": 0.0,
                    "late_fees": 0.0,
                    "charges_debt": 0.0,
                    "total_debt": 0.0,
                    "unpaid_periods": [],
                    "unpaid_charges": []
                }
            return debtors_map[clean_uid]

        # Calculate Unpaid Aliquots
        for a in self.data.get("aliquots", []):
            if a.get("status") in ["Pendiente", "Validación Manual"]:
                unit_entry = _get_entry(a.get("unit"))
                base_amt = float(a.get("amount", 0.0) or 0.0)
                stored_fee = float(a.get("late_fee", 0.0) or 0.0)
                
                if base_amt > 0:
                    late_fee = self.calculate_pending_aliquot_late_fee(
                        a.get("month"),
                        a.get("year"),
                        current_late_fee=stored_fee
                    )
                else:
                    late_fee = stored_fee
                
                if base_amt > 0 or late_fee > 0:
                    if base_amt > 0:
                        unit_entry["unpaid_aliquots_count"] += 1
                    unit_entry["aliquots_debt"] += base_amt
                    unit_entry["late_fees"] += late_fee
                    
                    if base_amt > 0 and late_fee > 0:
                        period_str = f"{a.get('month')} {a.get('year')} ({self.config.get('currency', '$')}{base_amt:.2f} + Multa: {self.config.get('currency', '$')}{late_fee:.2f})"
                    elif base_amt > 0:
                        period_str = f"{a.get('month')} {a.get('year')} ({self.config.get('currency', '$')}{base_amt:.2f})"
                    else:
                        period_str = f"Multa {a.get('month')} {a.get('year')} ({self.config.get('currency', '$')}{late_fee:.2f})"
                    unit_entry["unpaid_periods"].append(period_str)

        # Calculate Unpaid Additional Charges
        for c in self.data.get("additional_charges", []):
            if c.get("status") in ["Pendiente", "Validación Manual"]:
                unit_entry = _get_entry(c.get("unit"))
                amt = float(c.get("amount", 0.0) or 0.0)
                net_charge = amt
                unit_entry["charges_debt"] += net_charge
                unit_entry["unpaid_charges"].append(f"{c.get('type')}: {c.get('description')} ({self.config.get('currency', '$')}{net_charge:.2f})")

        # Compile final rows
        rows = []
        for u_id, d in debtors_map.items():
            d["total_debt"] = round(d["aliquots_debt"] + d["late_fees"] + d["charges_debt"], 2)
            d["aliquots_debt"] = round(d["aliquots_debt"], 2)
            d["late_fees"] = round(d["late_fees"], 2)
            d["charges_debt"] = round(d["charges_debt"], 2)
            
            # Determine Morosity Level
            months_cnt = d["unpaid_aliquots_count"]
            if d["total_debt"] <= 0.01:
                d["morosity_level"] = "Al Día"
                d["morosity_badge"] = "paid"
            elif months_cnt <= 1 and d["charges_debt"] == 0:
                d["morosity_level"] = "Mora Leve (1 mes)"
                d["morosity_badge"] = "warning"
            else:
                d["morosity_level"] = f"Mora Crítica ({months_cnt} meses)" if months_cnt > 1 else "Mora (Cargos Pendientes)"
                d["morosity_badge"] = "danger"
                
            d["periods_str"] = ", ".join(d["unpaid_periods"]) if d["unpaid_periods"] else "-"
            d["charges_str"] = ", ".join(d["unpaid_charges"]) if d["unpaid_charges"] else "-"

            # Filtering
            if unit and unit != "all" and str(u_id) != str(unit):
                continue
            if d["total_debt"] < min_debt:
                continue
            if status_filter:
                if status_filter == "debtors_only" and d["total_debt"] <= 0.01:
                    continue
                elif status_filter == "critical_only" and d["morosity_badge"] != "danger":
                    continue
                elif status_filter == "up_to_date" and d["total_debt"] > 0.01:
                    continue
                    
            rows.append(d)

        # Sort by total debt descending
        rows.sort(key=lambda x: x["total_debt"], reverse=True)

        # Calculate KPIs
        total_debt_sum = sum(r["total_debt"] for r in rows)
        debtors_count = sum(1 for r in rows if r["total_debt"] > 0.01)
        up_to_date_count = sum(1 for r in rows if r["total_debt"] <= 0.01)
        critical_count = sum(1 for r in rows if r.get("morosity_badge") == "danger")
        highest_debtor = max(rows, key=lambda x: x["total_debt"]) if rows else {"unit": "-", "total_debt": 0.0}

        kpis = {
            "total_debt": round(total_debt_sum, 2),
            "debtors_count": debtors_count,
            "up_to_date_count": up_to_date_count,
            "critical_count": critical_count,
            "total_units": len(rows),
            "highest_debtor_unit": highest_debtor.get("unit", "-"),
            "highest_debtor_amount": round(highest_debtor.get("total_debt", 0.0), 2)
        }

        return {
            "report_type": "debtors",
            "kpis": kpis,
            "rows": rows,
            "records": rows,
            "filters": {
                "unit": unit or "all",
                "min_debt": min_debt,
                "status_filter": status_filter or "all"
            }
        }

    def get_unreconciled_report_data(self, start_date=None, end_date=None, record_type=None):
        """
        Consolidates unreconciled records:
        - Resident vouchers in 'Validación Manual'
        - Unreconciled bank statement transactions
        """
        rows = []
        owner_map = {str(u["id"]): u.get("owner", "") for u in self.data.get("units", [])}
        
        # 1. Aliquots in Validación Manual
        for a in self.data.get("aliquots", []):
            if a.get("status") == "Validación Manual":
                u_str = str(a.get("unit"))
                amt = float(a.get("amount", 0.0) or 0.0) + float(a.get("late_fee", 0.0) or 0.0)
                pay_date = a.get("payment_date") or ""
                rows.append({
                    "id": a.get("id"),
                    "source": "Residente (Alícuota)",
                    "source_type": "resident",
                    "date": pay_date,
                    "unit": u_str,
                    "owner": owner_map.get(u_str, "-"),
                    "concept": f"Alícuota {a.get('month')} {a.get('year')}",
                    "reference": a.get("reference") or "(Sin Referencia)",
                    "amount": round(amt, 2),
                    "status": "Validación Manual",
                    "comprobante_url": a.get("comprobante_url") or "",
                    "suggestion": "Verificar comprobante o cargar extracto bancario con la referencia correspondiente"
                })

        # 2. Additional Charges in Validación Manual
        for c in self.data.get("additional_charges", []):
            if c.get("status") == "Validación Manual":
                u_str = str(c.get("unit"))
                amt = float(c.get("amount", 0.0) or 0.0)
                pay_date = c.get("payment_date") or c.get("issue_date") or ""
                rows.append({
                    "id": c.get("id"),
                    "source": f"Residente ({c.get('type', 'Cargo')})",
                    "source_type": "resident",
                    "date": pay_date,
                    "unit": u_str,
                    "owner": owner_map.get(u_str, "-"),
                    "concept": f"{c.get('type')}: {c.get('description')}",
                    "reference": c.get("reference") or "(Sin Referencia)",
                    "amount": round(amt, 2),
                    "status": "Validación Manual",
                    "comprobante_url": c.get("comprobante_url") or "",
                    "suggestion": "Verificar comprobante o conciliar con movimiento bancario"
                })

        # 3. Bank Statement Unreconciled
        for b in self.data.get("bank_statement", []):
            if not b.get("reconciled", False):
                amt = float(b.get("amount", 0.0) or 0.0)
                b_date = b.get("date") or ""
                rows.append({
                    "id": f"BANK-{b.get('reference')}",
                    "source": "Movimiento Bancario",
                    "source_type": "bank",
                    "date": b_date,
                    "unit": "Por Asignar",
                    "owner": "-",
                    "concept": b.get("detail") or "Depósito / Transferencia Recibida",
                    "reference": b.get("reference") or "-",
                    "amount": round(amt, 2),
                    "status": "Sin Conciliar",
                    "comprobante_url": "",
                    "suggestion": "Asignar a cobro pendiente o conciliar como 'Otro Ingreso'"
                })

        # Apply Filters
        filtered_rows = []
        for r in rows:
            if start_date and r["date"] and r["date"] < start_date:
                continue
            if end_date and r["date"] and r["date"] > end_date:
                continue
            if record_type and record_type != "all":
                if record_type == "resident" and r["source_type"] != "resident":
                    continue
                elif record_type == "bank" and r["source_type"] != "bank":
                    continue
            filtered_rows.append(r)

        # Sort by date descending
        filtered_rows.sort(key=lambda x: x["date"] or "0000-00-00", reverse=True)

        # KPIs
        total_unrec_amount = sum(r["amount"] for r in filtered_rows)
        resident_count = sum(1 for r in filtered_rows if r["source_type"] == "resident")
        resident_amount = sum(r["amount"] for r in filtered_rows if r["source_type"] == "resident")
        bank_count = sum(1 for r in filtered_rows if r["source_type"] == "bank")
        bank_amount = sum(r["amount"] for r in filtered_rows if r["source_type"] == "bank")

        kpis = {
            "total_unreconciled_amount": round(total_unrec_amount, 2),
            "resident_count": resident_count,
            "resident_amount": round(resident_amount, 2),
            "bank_count": bank_count,
            "bank_amount": round(bank_amount, 2),
            "total_records": len(filtered_rows)
        }

        return {
            "report_type": "unreconciled",
            "kpis": kpis,
            "rows": filtered_rows,
            "records": filtered_rows,
            "filters": {
                "start_date": start_date or "",
                "end_date": end_date or "",
                "record_type": record_type or "all"
            }
        }

    def add_expense(self, date, category, description, amount):
        exp_id = f"EXP-{len(self.data['expenses']) + 1}"
        expense = {
            "id": exp_id,
            "date": date,
            "category": category,
            "description": description,
            "amount": float(amount)
        }
        self.data["expenses"].append(expense)
        self.sync()
        return expense

    def update_expense(self, exp_id, date, category, description, amount):
        expense = None
        for e in self.data["expenses"]:
            if e["id"] == exp_id:
                expense = e
                break
        if not expense:
            return None
            
        expense["date"] = date
        expense["category"] = category
        expense["description"] = description
        expense["amount"] = float(amount)
        
        # update SQLite
        with Session(self.engine) as session:
            db_item = session.get(Expense, exp_id)
            if db_item:
                db_item.date = date
                db_item.category = category
                db_item.description = description
                db_item.amount = float(amount)
                session.commit()
                
        self.sync()
        return expense

    def delete_expense(self, exp_id):
        expense = None
        for e in self.data["expenses"]:
            if e["id"] == exp_id:
                expense = e
                break
        if not expense:
            return False
            
        # delete from SQLite
        with Session(self.engine) as session:
            db_item = session.get(Expense, exp_id)
            if db_item:
                session.delete(db_item)
                session.commit()
                
        self.data["expenses"].remove(expense)
        self.sync()
        return True

    def add_other_income(self, date, concept, amount, reference=""):
        next_id = 1
        if self.data["other_incomes"]:
            ids = [o.get("id") for o in self.data["other_incomes"] if o.get("id")]
            if ids:
                next_id = max(ids) + 1
        
        other_inc = {
            "id": next_id,
            "date": date,
            "concept": concept,
            "amount": float(amount),
            "reference": reference
        }
        self.data["other_incomes"].append(other_inc)
        
        if reference:
            for b in self.data["bank_statement"]:
                if b["reference"] == reference:
                    b["reconciled"] = True
                    break
        
        self.sync()
        return other_inc

    def update_other_income(self, item_id, date, concept, amount, reference=""):
        other_inc = None
        for o in self.data["other_incomes"]:
            if o.get("id") == item_id:
                other_inc = o
                break
        if not other_inc:
            return None
            
        old_ref = other_inc.get("reference", "")
        
        # update fields
        other_inc["date"] = date
        other_inc["concept"] = concept
        other_inc["amount"] = float(amount)
        other_inc["reference"] = reference
        
        # update bank statements reconciliation status if reference changed
        if old_ref != reference:
            if old_ref:
                for b in self.data["bank_statement"]:
                    if b["reference"] == old_ref:
                        b["reconciled"] = False
                        break
            if reference:
                for b in self.data["bank_statement"]:
                    if b["reference"] == reference:
                        b["reconciled"] = True
                        break
                        
        # update SQLite
        with Session(self.engine) as session:
            db_item = session.get(OtherIncome, item_id)
            if db_item:
                db_item.date = date
                db_item.concept = concept
                db_item.amount = float(amount)
                db_item.reference = reference
                session.commit()
                
        self.sync()
        return other_inc

    def delete_other_income(self, item_id):
        other_inc = None
        for o in self.data["other_incomes"]:
            if o.get("id") == item_id:
                other_inc = o
                break
        if not other_inc:
            return False
            
        ref = other_inc.get("reference", "")
        if ref:
            for b in self.data["bank_statement"]:
                if b["reference"] == ref:
                    b["reconciled"] = False
                    break
                    
        # Also clean up database record directly if needed, but since sync() merges all,
        # we must delete it from the SQLite database.
        # Wait! To delete a record from SQLite database when we delete it from self.data,
        # self.save_local_db() only merges. It does NOT delete!
        # Ah! Let's check how delete works for expenses or other tables!
        # If we delete a record from self.data, we should also delete it from SQLite!
        # Let's write a DELETE statement inside this method to delete it from the DB!
        with Session(self.engine) as session:
            db_item = session.get(OtherIncome, item_id)
            if db_item:
                session.delete(db_item)
                session.commit()
                
        self.data["other_incomes"].remove(other_inc)
        self.sync()
        return True

    def add_additional_charge(self, unit, type, description, amount, issue_date=None):
        if not issue_date:
            issue_date = datetime.now().strftime("%Y-%m-%d")
        charge_id = f"AC-{len(self.data['additional_charges']) + 1}"
        charge = {
            "id": charge_id,
            "unit": str(unit),
            "type": type,
            "description": description,
            "amount": float(amount),
            "issue_date": issue_date,
            "status": "Pendiente",
            "payment_date": "",
            "reference": ""
        }
        self.freeze_historical_info(charge)
        self.data["additional_charges"].append(charge)
        self.sync()
        return charge

    def update_additional_charge(self, charge_id, amount, description, reference=None):
        for c in self.data["additional_charges"]:
            if c["id"] == charge_id:
                c["amount"] = float(amount)
                c["description"] = description
                if reference is not None:
                    c["reference"] = reference
                # Update in SQLite
                with Session(self.engine) as session:
                    db_c = session.exec(select(AdditionalCharge).where(AdditionalCharge.id == charge_id)).first()
                    if db_c:
                        db_c.amount = float(amount)
                        db_c.description = description
                        if reference is not None:
                            db_c.reference = reference
                        session.add(db_c)
                        session.commit()
                self.sync()
                return c
        return None

    def update_bank_transaction(self, old_reference, new_reference, date=None, amount=None, detail=None):
        old_ref_clean = str(old_reference).strip()
        new_ref_clean = str(new_reference).strip()
        if not new_ref_clean:
            raise ValueError("El número de referencia no puede estar vacío.")
            
        tx_found = None
        for b in self.data["bank_statement"]:
            if str(b.get("reference", "")).strip().lower() == old_ref_clean.lower():
                tx_found = b
                break
                
        if not tx_found:
            return None
            
        # If reference changed, verify uniqueness
        if old_ref_clean.lower() != new_ref_clean.lower():
            for b in self.data["bank_statement"]:
                if str(b.get("reference", "")).strip().lower() == new_ref_clean.lower() and b is not tx_found:
                    raise ValueError(f"Ya existe una transacción bancaria con la referencia {new_ref_clean}.")
                    
        tx_found["reference"] = new_ref_clean
        if date is not None:
            tx_found["date"] = date
        if amount is not None:
            tx_found["amount"] = float(amount)
        if detail is not None:
            tx_found["detail"] = detail
            
        with Session(self.engine) as session:
            db_tx = session.exec(select(BankTransaction).where(BankTransaction.reference == old_reference)).first()
            if db_tx:
                if old_ref_clean.lower() != new_ref_clean.lower():
                    session.delete(db_tx)
                    session.commit()
                    new_db_tx = BankTransaction(
                        reference=new_ref_clean,
                        date=tx_found["date"],
                        amount=tx_found["amount"],
                        detail=tx_found["detail"],
                        reconciled=tx_found.get("reconciled", False)
                    )
                    session.add(new_db_tx)
                    session.commit()
                else:
                    db_tx.date = tx_found["date"]
                    db_tx.amount = tx_found["amount"]
                    db_tx.detail = tx_found["detail"]
                    session.add(db_tx)
                    session.commit()
                    
        self.sync()
        return tx_found

    def delete_additional_charge(self, charge_id):
        charge_found = None
        for c in self.data["additional_charges"]:
            if c["id"] == charge_id:
                charge_found = c
                break
        if not charge_found:
            with Session(self.engine) as session:
                db_c = session.exec(select(AdditionalCharge).where(AdditionalCharge.id == charge_id)).first()
                if db_c:
                    session.delete(db_c)
                    session.commit()
                    self.sync()
                    return True
            return False
        
        # Remove from memory list
        self.data["additional_charges"] = [c for c in self.data["additional_charges"] if c["id"] != charge_id]
        
        # Remove from Database
        with Session(self.engine) as session:
            db_c = session.exec(select(AdditionalCharge).where(AdditionalCharge.id == charge_id)).first()
            if db_c:
                session.delete(db_c)
                session.commit()
        self.sync()
        return True

    def add_bank_transaction(self, date, reference, amount, detail):
        transaction = {
            "date": date,
            "reference": reference,
            "amount": float(amount),
            "detail": detail,
            "reconciled": False
        }
        self.data["bank_statement"].append(transaction)
        self.sync()
        return transaction

    def add_bank_transactions_bulk(self, transactions):
        existing_refs = {str(b["reference"]).strip().lower() for b in self.data["bank_statement"] if b.get("reference")}
        added_count = 0
        duplicate_count = 0
        
        for tx in transactions:
            ref = str(tx.get("reference", "")).strip()
            if ref and ref.lower() in existing_refs:
                duplicate_count += 1
                continue
                
            new_tx = {
                "date": tx.get("date", datetime.now().strftime("%Y-%m-%d")),
                "reference": ref,
                "amount": float(tx.get("amount", 0.0)),
                "detail": tx.get("detail", "Carga Masiva"),
                "reconciled": False
            }
            self.data["bank_statement"].append(new_tx)
            if ref:
                existing_refs.add(ref.lower())
            added_count += 1
            
        if added_count > 0:
            self.sync()
            
        return added_count, duplicate_count

    def delete_bank_transaction(self, reference):
        ref_str = str(reference).strip().lower()
        tx_found = None
        for b in self.data["bank_statement"]:
            if str(b["reference"]).strip().lower() == ref_str:
                tx_found = b
                break
        if not tx_found:
            return False
            
        # If the bank transaction was reconciled, automatically revert associated payments
        if tx_found.get("reconciled"):
            # Check and revert matching aliquots
            for a in list(self.data["aliquots"]):
                a_ref = str(a.get("reference", "")).strip().lower()
                if a_ref and (a_ref == ref_str or ref_str in a_ref):
                    self.undo_payment(a["id"], "aliquot")
                    
            # Check and revert matching additional charges
            for c in list(self.data["additional_charges"]):
                c_ref = str(c.get("reference", "")).strip().lower()
                if c_ref and (c_ref == ref_str or ref_str in c_ref):
                    self.undo_payment(c["id"], "charge")
                    
        # Remove from in-memory list
        self.data["bank_statement"] = [b for b in self.data["bank_statement"] if str(b["reference"]).strip().lower() != ref_str]
        
        # Remove from database
        with Session(self.engine) as session:
            db_tx = session.exec(select(BankTransaction).where(BankTransaction.reference == reference)).first()
            if not db_tx:
                db_tx = session.exec(select(BankTransaction).where(BankTransaction.reference == tx_found["reference"])).first()
            if db_tx:
                session.delete(db_tx)
                session.commit()
                
        self.sync()
        return True

    def get_receipt_recipient_name(self, unit_id, recipient_override=None):
        unit = next((u for u in self.data["units"] if u["id"] == unit_id), None)
        if not unit:
            return "Residente"
        rec_type = recipient_override
        if not rec_type:
            unit_rec_type = unit.get("receipt_recipient_type")
            if unit_rec_type and unit_rec_type != "general":
                rec_type = unit_rec_type
            else:
                rec_type = self.config.get("receipt_recipient_type", "inquilino")
        if rec_type == "inquilino":
            tenant_name = str(unit.get("tenant") or "").strip()
            if tenant_name:
                return tenant_name
            return unit["owner"]
        else:
            return unit["owner"]

    def freeze_historical_info(self, item, recipient_override=None):
        unit_id = item.get("unit")
        if not unit_id:
            return
        unit_obj = next((u for u in self.data["units"] if u["id"] == str(unit_id)), None)
        if unit_obj:
            item["owner_name"] = unit_obj.get("owner", "")
            item["cedula_owner"] = unit_obj.get("cedula_owner", "")
            item["email_owner"] = unit_obj.get("email1", "")
            item["phone_owner"] = unit_obj.get("phone1", "")
            item["tenant_name"] = unit_obj.get("tenant", "")
            item["cedula_tenant"] = unit_obj.get("cedula_tenant", "")
            item["email_tenant"] = unit_obj.get("email2", "")
            item["phone_tenant"] = unit_obj.get("phone2", "")
            item["paid_by"] = self.get_receipt_recipient_name(str(unit_id), recipient_override)

    def calculate_pending_aliquot_late_fee(self, month_name, year, current_late_fee=0.0, check_date=None):
        """
        Calculates the late fee applicable to an unpaid/pending aliquot based on late_fee_day.
        If current_late_fee > 0 is already set, returns current_late_fee.
        Otherwise, if the check_date (or today) is strictly past the deadline (late_fee_day of that month/year),
        returns late_fee_amount from config.
        """
        if current_late_fee and float(current_late_fee) > 0.0:
            return float(current_late_fee)

        month_map = {
            "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
            "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12
        }
        target_month = month_map.get(str(month_name).strip().lower())
        if not target_month:
            return 0.0

        try:
            target_year = int(year)
            limit_day = int(self.config.get("late_fee_day", 15))
            if target_month == 2:
                limit_day = min(limit_day, 28)
            elif target_month in [4, 6, 9, 11]:
                limit_day = min(limit_day, 30)

            deadline = datetime(target_year, target_month, limit_day, 23, 59, 59)
            
            if check_date is None:
                current_time = datetime.now()
            elif isinstance(check_date, str):
                from receipt_processor import ReceiptProcessor
                clean_d = ReceiptProcessor.clean_date(check_date) or check_date
                current_time = datetime.strptime(clean_d, "%Y-%m-%d")
            else:
                current_time = check_date

            if current_time > deadline:
                return float(self.config.get("late_fee_amount", 10.0))
        except Exception as e:
            print(f"Error calculating pending aliquot late fee: {e}")
            
        return 0.0

    def check_late_fee(self, payment_date_str, month_name, year):
        # Check if the payment date exceeds late_fee_day of that month/year
        try:
            from receipt_processor import ReceiptProcessor
            normalized_date = ReceiptProcessor.clean_date(payment_date_str) if payment_date_str else None
            if not normalized_date:
                normalized_date = payment_date_str
            pay_date = datetime.strptime(normalized_date, "%Y-%m-%d")
            limit_day = int(self.config.get("late_fee_day", 15))
            
            # Map month name to index
            month_map = {
                "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
                "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12
            }
            target_month = month_map.get(str(month_name).strip().lower(), 1)
            
            # Create deadline date: limit_day of target_month in target_year
            deadline = datetime(year=int(year), month=target_month, day=limit_day)
            
            # If payment is after deadline, apply late fee
            if pay_date > deadline:
                return float(self.config.get("late_fee_amount", 10.0))
        except Exception as e:
            print(f"Error checking late fee: {e}")
        return 0.0

    def reconcile_payment(self, unit, month, year, amount, reference, payment_date, late_fee=None, recipient_override=None, reset_existing=False):
        amount = float(amount)
        
        # Get base amount for this unit
        base_amt = float(self.config.get("default_aliquot_base", 70.0))
        for u in self.data["units"]:
            if u["id"] == str(unit):
                base_amt = float(u.get("aliquot_base", base_amt))
                break
                
        # Find aliquot record
        aliquot = None
        for a in self.data["aliquots"]:
            if a["unit"] == str(unit) and a["month"].lower() == month.lower() and int(a["year"]) == int(year):
                aliquot = a
                break
                 
        if not aliquot:
            aliquot = {
                "id": f"{unit}-{year}-{month}",
                "unit": str(unit),
                "month": month,
                "year": int(year),
                "amount": base_amt,
                "late_fee": 0.0,
                "paid_amount": 0.0,
                "payment_date": "",
                "reference": "",
                "status": "Pendiente"
            }
            self.freeze_historical_info(aliquot, recipient_override)
            self.data["aliquots"].append(aliquot)
        else:
            self.freeze_historical_info(aliquot, recipient_override)
            if reset_existing or (aliquot.get("paid_amount", 0.0) > 0 and reference and reference in str(aliquot.get("reference", ""))):
                current_total = float(aliquot.get("amount", 0.0)) + float(aliquot.get("paid_amount", 0.0))
                aliquot["amount"] = current_total if current_total > 0 else base_amt
                aliquot["paid_amount"] = 0.0
                aliquot["late_fee"] = 0.0
                aliquot["status"] = "Pendiente"

        # Check late fee
        if late_fee is None:
            late_fee = self.check_late_fee(payment_date, month, year)
        else:
            late_fee = float(late_fee)
        
        # Calculate payment breakdown
        total_due = aliquot["amount"] + late_fee
        
        aliquot_paid = 0.0
        late_fee_paid = 0.0
        abono_deuda = 0.0
        pago_extra = 0.0
        saldo_deuda = 0.0
        advance_aliquots = []
        
        if amount < total_due:
            # Prioritize paying base aliquot first
            if amount <= aliquot["amount"]:
                aliquot_paid = amount
                rem = 0.0
            else:
                aliquot_paid = aliquot["amount"]
                rem = amount - aliquot_paid
                
            if rem > 0 and late_fee > 0:
                late_fee_paid = min(rem, late_fee)
            else:
                late_fee_paid = 0.0
                
            aliquot["amount"] -= aliquot_paid
            aliquot["paid_amount"] = (aliquot.get("paid_amount") or 0.0) + aliquot_paid
            aliquot["late_fee"] = late_fee - late_fee_paid
            aliquot["status"] = "Pendiente"
            aliquot["payment_date"] = ((aliquot["payment_date"] + ", " + payment_date) if aliquot["payment_date"] and payment_date not in aliquot["payment_date"] else (payment_date or aliquot.get("payment_date", "")))
            aliquot["reference"] = ((aliquot["reference"] + ", " + reference) if aliquot["reference"] and reference not in aliquot["reference"] else (reference or aliquot.get("reference", "")))
        else:
            # Full payment of aliquot
            aliquot_paid = aliquot["amount"]
            late_fee_paid = late_fee
            
            accum_paid = (aliquot.get("paid_amount") or 0.0) + aliquot_paid
            aliquot["paid_amount"] = accum_paid
            aliquot["amount"] = accum_paid
            aliquot["status"] = "Pagado"
            aliquot["payment_date"] = payment_date
            if aliquot.get("reference") and reference and reference not in aliquot["reference"]:
                aliquot["reference"] = aliquot["reference"] + ", " + reference
            else:
                aliquot["reference"] = reference
            aliquot["late_fee"] = late_fee
            
            extra_amount = amount - total_due
            if extra_amount > 0:
                # 1. Pay pending historical debt first
                pending_charge = None
                for c in self.data["additional_charges"]:
                    if c["unit"] == str(unit) and c["status"] == "Pendiente" and any(k in c["description"].lower() or k in c["type"].lower() for k in ["deuda", "saldo deudor"]):
                        pending_charge = c
                        break
                        
                if pending_charge:
                    self.freeze_historical_info(pending_charge, recipient_override)
                    if extra_amount >= pending_charge["amount"]:
                        abono_deuda = pending_charge["amount"]
                        extra_amount -= pending_charge["amount"]
                        pending_charge["amount"] = 0.0
                        pending_charge["paid_amount"] = (pending_charge.get("paid_amount") or 0.0) + abono_deuda
                        pending_charge["status"] = "Pagado"
                        pending_charge["payment_date"] = payment_date
                        pending_charge["reference"] = reference
                        saldo_deuda = 0.0
                    else:
                        abono_deuda = extra_amount
                        pending_charge["amount"] -= extra_amount
                        pending_charge["paid_amount"] = (pending_charge.get("paid_amount") or 0.0) + abono_deuda
                        pending_charge["payment_date"] = ((pending_charge["payment_date"] + ", " + payment_date) if pending_charge["payment_date"] else payment_date)
                        pending_charge["reference"] = ((pending_charge["reference"] + ", " + reference) if pending_charge["reference"] else reference)
                        saldo_deuda = pending_charge["amount"]
                        extra_amount = 0.0
                        
                # 2. Pay future aliquots next
                if extra_amount > 0:
                    MONTHS_ORDER = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
                    try:
                        current_month_idx = MONTHS_ORDER.index(month.capitalize())
                    except ValueError:
                        current_month_idx = -1
                        
                    if current_month_idx != -1:
                        idx = current_month_idx + 1
                        temp_year = year
                        months_processed = 0
                        while extra_amount > 0 and months_processed < 12:
                            if idx >= 12:
                                idx = 0
                                temp_year += 1
                            next_month = MONTHS_ORDER[idx]
                            
                            aliquot_base = base_amt
                            
                            future_aliquot = None
                            for a in self.data["aliquots"]:
                                if a["unit"] == str(unit) and a["month"].lower() == next_month.lower() and int(a["year"]) == temp_year:
                                    future_aliquot = a
                                    break
                                    
                            if future_aliquot:
                                if future_aliquot["status"] == "Pagado":
                                    idx += 1
                                    months_processed += 1
                                    continue
                                due_amt = future_aliquot["amount"]
                            else:
                                due_amt = aliquot_base
                                
                            if extra_amount >= due_amt:
                                extra_amount -= due_amt
                                if future_aliquot:
                                    future_aliquot["paid_amount"] = (future_aliquot.get("paid_amount") or 0.0) + due_amt
                                    future_aliquot["status"] = "Pagado"
                                    future_aliquot["payment_date"] = payment_date
                                    future_aliquot["reference"] = reference
                                    self.freeze_historical_info(future_aliquot, recipient_override)
                                else:
                                    future_aliquot = {
                                        "id": f"{unit}-{temp_year}-{next_month}",
                                        "unit": str(unit),
                                        "month": next_month,
                                        "year": temp_year,
                                        "amount": aliquot_base,
                                        "paid_amount": due_amt,
                                        "late_fee": 0.0,
                                        "payment_date": payment_date,
                                        "reference": reference,
                                        "status": "Pagado"
                                    }
                                    self.freeze_historical_info(future_aliquot, recipient_override)
                                    self.data["aliquots"].append(future_aliquot)
                                advance_aliquots.append({
                                    "month": next_month,
                                    "year": temp_year,
                                    "amount": due_amt,
                                    "status": "Pagado"
                                })
                            else:
                                # Partial payment of future aliquot
                                if future_aliquot:
                                    future_aliquot["amount"] -= extra_amount
                                    future_aliquot["paid_amount"] = (future_aliquot.get("paid_amount") or 0.0) + extra_amount
                                    future_aliquot["payment_date"] = ((future_aliquot["payment_date"] + ", " + payment_date) if future_aliquot["payment_date"] else payment_date)
                                    future_aliquot["reference"] = ((future_aliquot["reference"] + ", " + reference) if future_aliquot["reference"] else reference)
                                    self.freeze_historical_info(future_aliquot, recipient_override)
                                else:
                                    future_aliquot = {
                                        "id": f"{unit}-{temp_year}-{next_month}",
                                        "unit": str(unit),
                                        "month": next_month,
                                        "year": temp_year,
                                        "amount": aliquot_base - extra_amount,
                                        "paid_amount": extra_amount,
                                        "late_fee": 0.0,
                                        "payment_date": payment_date,
                                        "reference": reference,
                                        "status": "Pendiente"
                                    }
                                    self.freeze_historical_info(future_aliquot, recipient_override)
                                    self.data["aliquots"].append(future_aliquot)
                                advance_aliquots.append({
                                    "month": next_month,
                                    "year": temp_year,
                                    "amount": extra_amount,
                                    "status": "Abono"
                                })
                                extra_amount = 0.0
                                
                            idx += 1
                            months_processed += 1
                            
                # 3. Leftover is general credit
                if extra_amount > 0:
                    pago_extra = extra_amount
                        
        # Mark bank statement transaction as reconciled
        for b in self.data["bank_statement"]:
            if b["reference"] == reference and not b["reconciled"]:
                b["reconciled"] = True
                break
                
        self.sync()
        return {
            "aliquot": aliquot,
            "aliquot_paid": aliquot_paid,
            "late_fee_paid": late_fee_paid,
            "abono_deuda": abono_deuda,
            "pago_extra": pago_extra,
            "saldo_deuda": saldo_deuda,
            "advance_aliquots": advance_aliquots
        }

    def reconcile_additional_charge(self, charge_id, reference, payment_date, amount=None, recipient_override=None):
        charge = None
        for c in self.data["additional_charges"]:
            if c["id"] == charge_id:
                charge = c
                break
                
        if not charge:
            return None

        self.freeze_historical_info(charge, recipient_override)
            
        abono_deuda = 0.0
        pago_extra = 0.0
        saldo_deuda = 0.0
        is_fully_paid = True
        
        if amount is not None:
            amount = float(amount)
            if amount < charge["amount"]:
                abono_deuda = amount
                charge["amount"] -= amount
                charge["paid_amount"] = (charge.get("paid_amount") or 0.0) + abono_deuda
                charge["payment_date"] = ((charge["payment_date"] + ", " + payment_date) if charge["payment_date"] else payment_date)
                charge["reference"] = ((charge["reference"] + ", " + reference) if charge["reference"] else reference)
                saldo_deuda = charge["amount"]
                is_fully_paid = False
            else:
                abono_deuda = charge["amount"]
                pago_extra = amount - charge["amount"]
                charge["amount"] = 0.0
                charge["paid_amount"] = (charge.get("paid_amount") or 0.0) + abono_deuda
                charge["status"] = "Pagado"
                charge["payment_date"] = payment_date
                charge["reference"] = reference
                saldo_deuda = 0.0
                is_fully_paid = True
        else:
            abono_deuda = charge["amount"]
            charge["amount"] = 0.0
            charge["paid_amount"] = (charge.get("paid_amount") or 0.0) + abono_deuda
            charge["status"] = "Pagado"
            charge["payment_date"] = payment_date
            charge["reference"] = reference
            saldo_deuda = 0.0
            is_fully_paid = True
            
        # Mark bank statement transaction as reconciled
        for b in self.data["bank_statement"]:
            if b["reference"] == reference and not b["reconciled"]:
                b["reconciled"] = True
                break
                
        self.sync()
        return {
            "charge": charge,
            "aliquot_paid": 0.0,
            "late_fee_paid": 0.0,
            "abono_deuda": abono_deuda,
            "pago_extra": pago_extra,
            "saldo_deuda": saldo_deuda,
            "is_fully_paid": is_fully_paid
        }

    def update_aliquot_status(self, aliquot_id, status, payment_date="", reference="", late_fee=0.0):
        for a in self.data["aliquots"]:
            if a["id"] == aliquot_id:
                a["status"] = status
                a["payment_date"] = payment_date
                a["reference"] = reference
                a["late_fee"] = float(late_fee)
                if status == "Pagado":
                    a["paid_amount"] = float(a["amount"])
                
                # If marking as paid and reference exists, reconcile bank statement
                if status == "Pagado" and reference:
                    for b in self.data["bank_statement"]:
                        if b["reference"] == reference:
                            b["reconciled"] = True
                            break
                self.sync()
                return a
        return None

    def update_aliquot_amount(self, aliquot_id, amount):
        for a in self.data["aliquots"]:
            if a["id"] == aliquot_id:
                if a.get("status") == "Pagado":
                    raise ValueError("No se puede editar el valor de una alícuota que ya ha sido pagada.")
                
                new_amount = float(amount)
                if new_amount < 0:
                    raise ValueError("El monto de la alícuota no puede ser negativo.")
                
                a["amount"] = new_amount
                # Update in SQLite
                with Session(self.engine) as session:
                    db_a = session.exec(select(Aliquot).where(Aliquot.id == aliquot_id)).first()
                    if db_a:
                        db_a.amount = new_amount
                        session.add(db_a)
                        session.commit()
                self.sync()
                return a
        return None

    def delete_aliquot(self, aliquot_id):
        aliquot_found = None
        for a in self.data["aliquots"]:
            if a["id"] == aliquot_id:
                aliquot_found = a
                break
                
        if not aliquot_found:
            # Also check directly in DB
            with Session(self.engine) as session:
                db_a = session.exec(select(Aliquot).where(Aliquot.id == aliquot_id)).first()
                if db_a:
                    session.delete(db_a)
                    session.commit()
                    self.sync()
                    return True
            return False

        # If it was paid and linked to bank reference, unmark bank transaction if no other record uses it
        if aliquot_found.get("reference") and aliquot_found.get("status") == "Pagado":
            ref = str(aliquot_found["reference"]).strip().lower()
            other_using = any(
                str(other.get("reference", "")).strip().lower() == ref 
                for other in self.data["aliquots"] if other["id"] != aliquot_id and other.get("status") == "Pagado"
            ) or any(
                str(c.get("reference", "")).strip().lower() == ref 
                for c in self.data["additional_charges"] if c.get("status") == "Pagado"
            )
            if not other_using:
                for b in self.data["bank_statement"]:
                    if str(b.get("reference", "")).strip().lower() == ref:
                        b["reconciled"] = False
                        break

        # Remove from memory list
        self.data["aliquots"] = [a for a in self.data["aliquots"] if a["id"] != aliquot_id]

        # Remove from database
        with Session(self.engine) as session:
            db_a = session.exec(select(Aliquot).where(Aliquot.id == aliquot_id)).first()
            if db_a:
                session.delete(db_a)
                session.commit()
                
        self.sync()
        return True

    def reconcile_mass(self):
        matched_aliquots = []
        matched_charges = []
        
        # 1. Aliquots matching
        for a in self.data["aliquots"]:
            if a["status"] in ["Validación Manual", "Pendiente"] and a.get("reference"):
                ref = str(a["reference"]).strip().lower()
                amount = float(a["amount"])
                
                # Try to find a matching bank transaction
                for b in self.data["bank_statement"]:
                    if b.get("reconciled"):
                        continue
                    
                    b_ref = str(b["reference"]).strip().lower()
                    b_amount = float(b["amount"])
                    
                    # Check reference matching (substring) and amount matching
                    late_fee = self.check_late_fee(b["date"], a["month"], a["year"])
                    if ref and b_ref and (ref in b_ref or b_ref in ref):
                        if abs(amount - b_amount) < 0.01 or abs((amount + late_fee) - b_amount) < 0.01:
                            # Match found! Reconcile
                            a["status"] = "Pagado"
                            a["payment_date"] = b["date"]
                            a["reference"] = b["reference"]
                            a["late_fee"] = late_fee
                            a["paid_amount"] = float(a["amount"])
                            self.freeze_historical_info(a)
                            b["reconciled"] = True
                            matched_aliquots.append(a.copy())
                            break
                            
        # 2. Additional charges matching
        for c in self.data["additional_charges"]:
            if c["status"] in ["Validación Manual", "Pendiente"] and c.get("reference"):
                ref = str(c["reference"]).strip().lower()
                amount = float(c["amount"])
                
                for b in self.data["bank_statement"]:
                    if b.get("reconciled"):
                        continue
                    
                    b_ref = str(b["reference"]).strip().lower()
                    b_amount = float(b["amount"])
                    
                    if ref and b_ref and (ref in b_ref or b_ref in ref):
                        if abs(amount - b_amount) < 0.01:
                            c["status"] = "Pagado"
                            c["payment_date"] = b["date"]
                            c["reference"] = b["reference"]
                            c["paid_amount"] = (c.get("paid_amount") or 0.0) + b_amount
                            c["amount"] = 0.0
                            self.freeze_historical_info(c)
                            b["reconciled"] = True
                            matched_charges.append(c.copy())
                            break
                        elif b_amount < amount and b_amount > 0:
                            c["amount"] -= b_amount
                            c["paid_amount"] = (c.get("paid_amount") or 0.0) + b_amount
                            c["payment_date"] = b["date"]
                            c["reference"] = b["reference"]
                            self.freeze_historical_info(c)
                            b["reconciled"] = True
                            matched_charges.append(c.copy())
                            break
                            
        if matched_aliquots or matched_charges:
            self.sync()
            
            # Generate PDFs and send notifications for matched aliquots
            from receipt_processor import ReceiptProcessor
            from notifications import NotificationManager
            import os
            
            static_dir = os.path.join(os.path.dirname(__file__), "static")
            receipts_dir = os.path.join(static_dir, "receipts")
            
            for a in matched_aliquots:
                receipt_no = f"REC-AL-{a['id']}"
                resident_name = self.get_receipt_recipient_name(a["unit"])
                dest_pdf = os.path.join(receipts_dir, f"recibo_{a['id']}.pdf")
                concept = f"Pago de Alícuota Ordinaria - Mes: {a['month']} / {a['year']}"
                
                ReceiptProcessor.generate_receipt_pdf(
                    dest_path=dest_pdf,
                    receipt_no=receipt_no,
                    resident_name=resident_name,
                    unit=a["unit"],
                    concept=concept,
                    amount=a["amount"],
                    late_fee=a["late_fee"],
                    reference=a["reference"],
                    payment_date=a["payment_date"],
                    sm=self
                )
                
                try:
                    NotificationManager.send_receipt_notifications(
                        self,
                        unit_id=a["unit"],
                        receipt_pdf_path=dest_pdf,
                        receipt_no=receipt_no,
                        resident_name=resident_name,
                        concept=concept,
                        amount=a["amount"] + a["late_fee"],
                        payment_date=a["payment_date"],
                        reference=a["reference"]
                    )
                except Exception as e:
                    print(f"Error sending automatic aliquot notification: {e}")
                
            # Generate PDFs and send notifications for matched charges
            for c in matched_charges:
                receipt_no = f"REC-AC-{c['id']}"
                resident_name = self.get_receipt_recipient_name(c["unit"])
                dest_pdf = os.path.join(receipts_dir, f"recibo_charge_{c['id']}.pdf")
                concept = f"{c['type']}: {c['description']}"
                
                ReceiptProcessor.generate_receipt_pdf(
                    dest_path=dest_pdf,
                    receipt_no=receipt_no,
                    resident_name=resident_name,
                    unit=c["unit"],
                    concept=concept,
                    amount=c["amount"],
                    late_fee=0.0,
                    reference=c["reference"],
                    payment_date=c["payment_date"],
                    sm=self
                )
                
                try:
                    NotificationManager.send_receipt_notifications(
                        self,
                        unit_id=c["unit"],
                        receipt_pdf_path=dest_pdf,
                        receipt_no=receipt_no,
                        resident_name=resident_name,
                        concept=concept,
                        amount=c["amount"],
                        payment_date=c["payment_date"],
                        reference=c["reference"]
                    )
                except Exception as e:
                    print(f"Error sending automatic charge notification: {e}")
                
        return len(matched_aliquots), len(matched_charges)

    def add_or_update_unit(self, unit_id, owner, phone1, email1, tenant, phone2, email2, aliquot_base=None, cedula_owner="", cedula_tenant="", receipt_recipient_type="general"):
        if aliquot_base is None:
            aliquot_base = float(self.config.get("default_aliquot_base", 70.0))
        else:
            aliquot_base = float(aliquot_base)
        unit = None
        for u in self.data["units"]:
            if u["id"] == str(unit_id):
                unit = u
                break
                
        if unit:
            unit["owner"] = owner
            unit["cedula_owner"] = cedula_owner
            unit["phone1"] = phone1
            unit["email1"] = email1
            unit["tenant"] = tenant
            unit["cedula_tenant"] = cedula_tenant
            unit["phone2"] = phone2
            unit["email2"] = email2
            unit["aliquot_base"] = float(aliquot_base)
            unit["receipt_recipient_type"] = receipt_recipient_type
        else:
            unit = {
                "id": str(unit_id),
                "owner": owner,
                "cedula_owner": cedula_owner,
                "phone1": phone1,
                "email1": email1,
                "tenant": tenant,
                "cedula_tenant": cedula_tenant,
                "phone2": phone2,
                "email2": email2,
                "aliquot_base": float(aliquot_base),
                "receipt_recipient_type": receipt_recipient_type
            }
            self.data["units"].append(unit)
                    
        self.sync()
        return unit

    def delete_unit(self, unit_id):
        unit_id_str = str(unit_id).strip()
        in_memory = any(u["id"] == unit_id_str for u in self.data["units"])
        
        db_unit = None
        with Session(self.engine) as session:
            db_unit = session.exec(select(Unit).where(Unit.id == unit_id_str)).first()
            if db_unit:
                # Delete associated users
                cedulas = [c for c in [db_unit.cedula_owner, db_unit.cedula_tenant] if c]
                for c in cedulas:
                    db_user = session.exec(select(User).where(User.cedula == c)).first()
                    if db_user:
                        session.delete(db_user)
                
                # Delete aliquots
                db_aliquots = session.exec(select(Aliquot).where(Aliquot.unit == unit_id_str)).all()
                for a in db_aliquots:
                    session.delete(a)
                    
                # Delete charges
                db_charges = session.exec(select(AdditionalCharge).where(AdditionalCharge.unit == unit_id_str)).all()
                for c in db_charges:
                    session.delete(c)
                    
                # Delete unit
                session.delete(db_unit)
                session.commit()
        
        if in_memory or db_unit:
            # Clean up memory structures
            self.data["units"] = [u for u in self.data["units"] if u["id"] != unit_id_str]
            self.data["aliquots"] = [a for a in self.data["aliquots"] if a["unit"] != unit_id_str]
            self.data["additional_charges"] = [c for c in self.data["additional_charges"] if c["unit"] != unit_id_str]
            
            # Sync deletes to local DB and Google Sheets
            self.sync()
            return True
            
        return False

    def update_settings(self, spreadsheet_id=None, google_credentials_json=None, use_google_sheets=None, late_fee_day=None, late_fee_amount=None, initial_bank_balance=None, default_aliquot_base=None, smtp_host=None, smtp_port=None, smtp_user=None, smtp_password=None, smtp_from=None, twilio_sid=None, twilio_token=None, twilio_whatsapp_from=None, whatsapp_provider=None, meta_wa_token=None, meta_wa_phone_number_id=None, meta_wa_verify_token=None, meta_wa_business_account_id=None, directive_president=None, directive_treasurer=None, condo_name=None, condo_address=None, condo_ruc=None, exonerate_directiva=None, receipt_recipient_type=None, app_mode=None, db_type=None, db_host=None, db_port=None, db_user=None, db_password=None, db_name=None, db_custom_url=None):
        if spreadsheet_id is not None:
            self.config["spreadsheet_id"] = spreadsheet_id
        if google_credentials_json is not None and google_credentials_json != "******":
            self.config["google_credentials_json"] = google_credentials_json
        if use_google_sheets is not None:
            self.config["use_google_sheets"] = bool(use_google_sheets)
        if late_fee_day is not None:
            self.config["late_fee_day"] = int(late_fee_day)
        if late_fee_amount is not None:
            self.config["late_fee_amount"] = float(late_fee_amount)
        if initial_bank_balance is not None:
            self.config["initial_bank_balance"] = float(initial_bank_balance)
        if default_aliquot_base is not None:
            self.config["default_aliquot_base"] = float(default_aliquot_base)
            for u in self.data.get("units", []):
                u["aliquot_base"] = float(default_aliquot_base)
        if smtp_host is not None:
            self.config["smtp_host"] = smtp_host
        if smtp_port is not None:
            self.config["smtp_port"] = int(smtp_port) if smtp_port else 587
        if smtp_user is not None:
            self.config["smtp_user"] = smtp_user
        if smtp_password is not None and smtp_password != "******":
            self.config["smtp_password"] = smtp_password
        if smtp_from is not None:
            self.config["smtp_from"] = smtp_from
        if twilio_sid is not None:
            self.config["twilio_sid"] = twilio_sid
        if twilio_token is not None and twilio_token != "******":
            self.config["twilio_token"] = twilio_token
        if twilio_whatsapp_from is not None:
            self.config["twilio_whatsapp_from"] = twilio_whatsapp_from
        if whatsapp_provider is not None:
            self.config["whatsapp_provider"] = whatsapp_provider
        if meta_wa_token is not None and meta_wa_token != "******":
            self.config["meta_wa_token"] = meta_wa_token
        if meta_wa_phone_number_id is not None:
            self.config["meta_wa_phone_number_id"] = meta_wa_phone_number_id
        if meta_wa_verify_token is not None and meta_wa_verify_token != "******":
            self.config["meta_wa_verify_token"] = meta_wa_verify_token
        if meta_wa_business_account_id is not None:
            self.config["meta_wa_business_account_id"] = meta_wa_business_account_id
        if directive_president is not None:
            self.config["directive_president"] = directive_president
        if directive_treasurer is not None:
            self.config["directive_treasurer"] = directive_treasurer
        if condo_name is not None:
            self.config["condo_name"] = condo_name
        if condo_address is not None:
            self.config["condo_address"] = condo_address
        if condo_ruc is not None:
            self.config["condo_ruc"] = condo_ruc
        if exonerate_directiva is not None:
            self.config["exonerate_directiva"] = bool(exonerate_directiva)
        if receipt_recipient_type is not None:
            self.config["receipt_recipient_type"] = str(receipt_recipient_type)
        if app_mode is not None:
            self.config["app_mode"] = str(app_mode)
        if db_type is not None:
            self.config["db_type"] = str(db_type)
        if db_host is not None:
            self.config["db_host"] = str(db_host)
        if db_port is not None:
            self.config["db_port"] = int(db_port) if db_port else 5432
        if db_user is not None:
            self.config["db_user"] = str(db_user)
        if db_password is not None and db_password != "******":
            self.config["db_password"] = db_password
        if db_name is not None:
            self.config["db_name"] = str(db_name)
        if db_custom_url is not None and db_custom_url != "******":
            self.config["db_custom_url"] = db_custom_url
            
        self.save_config()
        self.sync()
        self.load_data() # Reload data using new configs
        res = self.config.copy()
        res["status"] = "success"
        return res

    def update_initial_balances(self, initial_bank_balance=0.0, initial_reserve_fund=0.0, cut_off_date="", description="", notes=""):
        initial_bank_balance = float(initial_bank_balance)
        initial_reserve_fund = float(initial_reserve_fund)
        self.config["initial_bank_balance"] = initial_bank_balance
        self.config["initial_reserve_fund"] = initial_reserve_fund

        if getattr(self, "engine", None):
            try:
                with Session(self.engine) as session:
                        db_bal = session.exec(select(InitialBalance).where(InitialBalance.id == 1)).first()
                        if not db_bal:
                            db_bal = InitialBalance(id=1)
                            session.add(db_bal)
                        db_bal.initial_bank_balance = initial_bank_balance
                        db_bal.initial_reserve_fund = initial_reserve_fund
                        if cut_off_date:
                            db_bal.cut_off_date = cut_off_date
                        if description:
                            db_bal.description = description
                        if notes is not None:
                            db_bal.notes = notes
                        db_bal.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        session.commit()
                        self.data["initial_balances"] = db_bal.model_dump()
            except Exception as e:
                print(f"[DB Error] Error updating initial balances: {e}")

        self.save_config()
        return self.data.get("initial_balances", {
            "initial_bank_balance": initial_bank_balance,
            "initial_reserve_fund": initial_reserve_fund,
            "cut_off_date": cut_off_date,
            "description": description,
            "notes": notes
        })

    def emit_aliquots_bulk(self, month, year):
        month = str(month).strip().capitalize()
        year = int(year)
        
        default_aliquot_base = float(self.config.get("default_aliquot_base", 70.0))
        exonerate_directiva = self.config.get("exonerate_directiva", False)
        
        # Build set of unit IDs that are active board members
        board_units = set()
        if exonerate_directiva:
            board_units = {str(bm["unit_id"]) for bm in self.data.get("board_members", []) if bm.get("unit_id")}
            
        emitted_count = 0
        
        for u in self.data["units"]:
            unit_id = u["id"]
            aliquot_id = f"{unit_id}-{year}-{month}"
            
            exists = False
            for a in self.data["aliquots"]:
                if str(a.get("unit")) == str(unit_id) and str(a.get("month", "")).lower() == month.lower() and int(a.get("year", 0)) == year:
                    exists = True
                    break
                    
            if not exists:
                is_exonerated = str(unit_id) in board_units
                aliquot_base = 0.0 if is_exonerated else float(u.get("aliquot_base") or default_aliquot_base)
                status = "Exonerado" if is_exonerated else "Pendiente"
                
                al_dict = {
                    "id": aliquot_id,
                    "unit": str(unit_id),
                    "month": month,
                    "year": year,
                    "amount": aliquot_base,
                    "late_fee": 0.0,
                    "payment_date": "" if not is_exonerated else datetime.now().strftime("%Y-%m-%d"),
                    "reference": "" if not is_exonerated else "EXONERADO-DIRECTIVA",
                    "status": status,
                    "comprobante_url": ""
                }
                self.freeze_historical_info(al_dict)
                self.data["aliquots"].append(al_dict)
                emitted_count += 1
                
        if emitted_count > 0:
            self.sync()
            
        return emitted_count

    def emit_aliquot_individual(self, unit, month, year, amount=None):
        month = str(month).strip().capitalize()
        year = int(year)
        unit_str = str(unit).strip()
        
        # Verify unit exists
        unit_obj = None
        for u in self.data["units"]:
            if str(u["id"]) == unit_str:
                unit_obj = u
                break
                
        if not unit_obj:
            raise ValueError(f"El departamento/unidad '{unit_str}' no existe en el sistema.")
            
        aliquot_id = f"{unit_str}-{year}-{month}"
        
        # Check if aliquot already exists
        for a in self.data["aliquots"]:
            if str(a["unit"]) == unit_str and str(a["month"]).lower() == month.lower() and int(a["year"]) == year:
                raise ValueError(f"La alícuota para el Depto {unit_str} correspondiente a {month} {year} ya existe (Estado: {a.get('status')}).")
                
        default_aliquot_base = float(self.config.get("default_aliquot_base", 70.0))
        exonerate_directiva = self.config.get("exonerate_directiva", False)
        
        # Check if board member
        is_board_member = False
        if exonerate_directiva:
            is_board_member = any(str(bm.get("unit_id")) == unit_str for bm in self.data.get("board_members", []))
            
        if amount is not None and float(amount) >= 0:
            aliquot_base = float(amount)
            is_exonerated = False
        else:
            is_exonerated = is_board_member
            aliquot_base = 0.0 if is_exonerated else float(unit_obj.get("aliquot_base") or default_aliquot_base)
            
        status = "Exonerado" if is_exonerated else "Pendiente"
        
        al_dict = {
            "id": aliquot_id,
            "unit": unit_str,
            "month": month,
            "year": year,
            "amount": aliquot_base,
            "late_fee": 0.0,
            "paid_amount": 0.0,
            "payment_date": "" if not is_exonerated else datetime.now().strftime("%Y-%m-%d"),
            "reference": "" if not is_exonerated else "EXONERADO-DIRECTIVA",
            "status": status,
            "comprobante_url": ""
        }
        self.freeze_historical_info(al_dict)
        self.data["aliquots"].append(al_dict)
        self.sync()
        return al_dict

    def undo_payment(self, item_id, item_type):
        if item_type == "aliquot":
            # Find aliquot
            aliquot = None
            for a in self.data["aliquots"]:
                if a["id"] == item_id:
                    aliquot = a
                    break
            if not aliquot:
                return False
                
            ref = aliquot.get("reference", "")
            unit_id = aliquot.get("unit", "")
            
            # Find unit to restore default aliquot amount
            base_amt = float(self.config.get("default_aliquot_base", 70.0))
            for u in self.data["units"]:
                if u["id"] == unit_id:
                    base_amt = float(u.get("aliquot_base", base_amt))
                    break
            
            # Restore aliquot properties
            aliquot["amount"] = base_amt
            aliquot["paid_amount"] = 0.0
            aliquot["status"] = "Pendiente"
            aliquot["payment_date"] = ""
            aliquot["reference"] = ""
            aliquot["late_fee"] = 0.0
            
            # Unreconcile bank statement transaction
            if ref:
                for b in self.data["bank_statement"]:
                    if b["reference"] == ref:
                        b["reconciled"] = False
                        break
                        
                # Also restore any other future aliquots for this unit that were paid/partially paid with the SAME reference
                for a in self.data["aliquots"]:
                    if a["unit"] == unit_id and a["reference"] == ref and a["id"] != item_id:
                        base_amt_temp = float(self.config.get("default_aliquot_base", 70.0))
                        for u in self.data["units"]:
                            if u["id"] == unit_id:
                                base_amt_temp = float(u.get("aliquot_base", base_amt_temp))
                                break
                        a["amount"] = base_amt_temp
                        a["paid_amount"] = 0.0
                        a["status"] = "Pendiente"
                        a["payment_date"] = ""
                        a["reference"] = ""
                        a["late_fee"] = 0.0
                        
                # Look for any additional charge that was paid or partially paid using this reference
                for c in self.data["additional_charges"]:
                    if c["unit"] == unit_id and ref in c.get("reference", ""):
                        # Find matched bank statement transaction to calculate original surplus
                        tx_amt = base_amt
                        for b in self.data["bank_statement"]:
                            if b["reference"] == ref:
                                tx_amt = b["amount"]
                                break
                        # Surplus was calculated as: total payment - (base_amt + late_fee)
                        # We don't have the original late fee since it's now reset, but we can look it up in receipt if needed.
                        # However, a simpler/safer way: if the surplus was added to this charge, we can subtract it or reconstruct it.
                        # Wait, let's subtract the surplus from the bank transaction!
                        # The amount that was applied as abono was: tx_amt - base_amt. Let's use this approximation!
                        surplus = tx_amt - base_amt
                        if surplus > 0:
                            c["amount"] += surplus
                            c["paid_amount"] = max(0.0, (c.get("paid_amount") or 0.0) - surplus)
                            c["status"] = "Pendiente"
                            c["reference"] = c["reference"].replace(ref, "").strip(", ")
                            c["payment_date"] = ""
                            
            # Delete physical receipt image if any
            comprobante_url = aliquot.get("comprobante_url", "")
            if comprobante_url:
                img_path = os.path.join(os.path.dirname(__file__), *comprobante_url.lstrip("/").split("/"))
                if os.path.exists(img_path):
                    try:
                        os.remove(img_path)
                    except Exception as e:
                        print(f"Error removing comprobante image: {e}")
            aliquot["comprobante_url"] = ""
            
            # Delete PDF receipt
            receipt_path = os.path.join(os.path.dirname(__file__), "static", "receipts", f"recibo_{item_id}.pdf")
            if os.path.exists(receipt_path):
                try:
                    os.remove(receipt_path)
                except Exception as e:
                    print(f"Error removing PDF: {e}")
                    
            self.sync()
            return True
            
        elif item_type == "charge":
            # Find charge
            charge = None
            for c in self.data["additional_charges"]:
                if c["id"] == item_id:
                    charge = c
                    break
            if not charge:
                return False
                
            ref = charge.get("reference", "")
            
            # Restores charge properties
            tx_amt = 0.0
            if ref:
                for b in self.data["bank_statement"]:
                    if b["reference"] == ref:
                        tx_amt = b["amount"]
                        b["reconciled"] = False
                        break
            if tx_amt > 0:
                charge["amount"] += tx_amt
            charge["paid_amount"] = 0.0
            charge["status"] = "Pendiente"
            charge["payment_date"] = ""
            charge["reference"] = ""
            
            # Delete physical receipt image if any
            comprobante_url = charge.get("comprobante_url", "")
            if comprobante_url:
                img_path = os.path.join(os.path.dirname(__file__), *comprobante_url.lstrip("/").split("/"))
                if os.path.exists(img_path):
                    try:
                        os.remove(img_path)
                    except Exception as e:
                        print(f"Error removing comprobante image: {e}")
            charge["comprobante_url"] = ""
            
            # Delete PDF receipt
            receipt_path = os.path.join(os.path.dirname(__file__), "static", "receipts", f"recibo_charge_{item_id}.pdf")
            if os.path.exists(receipt_path):
                try:
                    os.remove(receipt_path)
                except Exception as e:
                    print(f"Error removing PDF: {e}")
                    
            self.sync()
            return True
            
        return False

    def add_board_member(self, unit_id, name, role):
        next_id = 1
        if self.data.get("board_members"):
            ids = [m.get("id") for m in self.data["board_members"] if m.get("id")]
            if ids:
                next_id = max(ids) + 1
        
        member = {
            "id": next_id,
            "unit_id": str(unit_id),
            "name": name,
            "role": role
        }
        self.data["board_members"].append(member)
        self.sync()
        return member

    def delete_board_member(self, member_id):
        member = None
        for m in self.data["board_members"]:
            if m.get("id") == member_id:
                member = m
                break
        if not member:
            return False
            
        # Delete from SQLite
        with Session(self.engine) as session:
            db_item = session.get(BoardMember, member_id)
            if db_item:
                session.delete(db_item)
                session.commit()
                
        self.data["board_members"].remove(member)
        self.sync()
        return True
