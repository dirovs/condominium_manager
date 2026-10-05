import os
import json
import hashlib
from sqlmodel import create_engine, Session, SQLModel, select, text
from models import User, Unit, Aliquot, AdditionalCharge, Expense, BankTransaction, NotificationLog

def main():
    print("=== INICIANDO LIMPIEZA DE BASE DE DATOS ===")
    
    # Mandatory interactive confirmation check for safety
    print("\n=======================================================")
    print("ADVERTENCIA DE SEGURIDAD - LIMPIEZA DE BASE DE DATOS")
    print("=======================================================")
    print("Está a punto de borrar la información de la base de datos.")
    print("Esta operación es IRREVERSIBLE.")
    try:
        confirm = input("Para confirmar y proceder, escriba exactamente 'CONFIRMAR': ")
        if confirm.strip() != "CONFIRMAR":
            print("\nOperación cancelada. No se ha modificado ningún dato de la base de datos.\n")
            print("=======================================================\n")
            return
    except (EOFError, KeyboardInterrupt):
        print("\nOperación cancelada por el usuario. Base de datos intacta.\n")
        return

    # 1. Rename local_database.json to prevent auto-seeding on restart
    local_db_path = "local_database.json"
    backup_path = "local_database.json.bak"
    if os.path.exists(local_db_path):
        try:
            if os.path.exists(backup_path):
                os.remove(backup_path)
            os.rename(local_db_path, backup_path)
            print(f"[OK] Renombrado {local_db_path} a {backup_path} para evitar auto-sembrado.")
        except Exception as e:
            print(f"[ERROR] No se pudo renombrar el archivo: {e}")
            
    # 2. Clear tables in condo_manager.db
    db_path = "condo_manager.db"
    engine = create_engine(f"sqlite:///{db_path}")
    
    try:
        # Recreate tables if needed
        SQLModel.metadata.create_all(engine)
        
        with Session(engine) as session:
            # Delete data from all tables except Superadmin
            session.exec(text("DELETE FROM units"))
            session.exec(text("DELETE FROM aliquots"))
            session.exec(text("DELETE FROM additional_charges"))
            session.exec(text("DELETE FROM expenses"))
            session.exec(text("DELETE FROM bank_statement"))
            session.exec(text("DELETE FROM notification_logs"))
            session.exec(text("DELETE FROM users WHERE role != 'superadmin'"))
            
            # Check if Superadmin user exists, if not recreate it
            any_super = session.exec(select(User).where(User.role == "superadmin")).first()
            if any_super is None:
                default_password_hash = hashlib.sha256("super123".encode()).hexdigest()
                superadmin = User(
                    cedula="9999999999",
                    email="superadmin@condo.com",
                    name="Super Administrador",
                    role="superadmin",
                    password_hash=default_password_hash,
                    is_active=True
                )
                session.add(superadmin)
                print("[OK] Superadministrador recreado con credenciales por defecto.")
            
            session.commit()
            print("[OK] Base de datos condo_manager.db limpiada con éxito. Solo se conserva la cuenta de Superadministrador.")
            
    except Exception as e:
        print(f"[ERROR] Error al limpiar la base de datos: {e}")
        
    print("==========================================")
    print("La base de datos está ahora completamente en blanco.")
    print("Puedes iniciar sesión con:")
    print("  - Correo: superadmin@condo.com")
    print("  - Clave: super123")
    print("==========================================")

if __name__ == "__main__":
    main()
