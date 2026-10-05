import os
import hashlib
from sqlmodel import Session, SQLModel, create_engine, select
from sqlalchemy import text
from models import Unit, Aliquot, AdditionalCharge, Expense, BankTransaction, NotificationLog, User, OtherIncome, BoardMember
from sheets_manager import SheetsManager

def reset_database(interactive_confirmed=False):
    """
    Restablece la base de datos a blanco ÚNICAMENTE bajo confirmación explícita e interactiva del usuario.
    Está deshabilitada cualquier ejecución automática o sin intervención del usuario.
    """
    if not interactive_confirmed:
        print("\n=======================================================")
        print("[SEGURIDAD DE DATOS] BORRADO AUTOMÁTICO BLOQUEADO")
        print("=======================================================")
        print("El borrado automático de la base de datos sin intervención")
        print("del usuario ha sido completamente deshabilitado en el código.")
        print("Para reiniciar la base de datos, ejecute el script manualmente")
        print("y confirme la acción de forma interactiva en la consola.")
        print("=======================================================\n")
        return False
        
    try:
        # Initialize SheetsManager (which connects to the db or creates it)
        sm = SheetsManager()
        
        # Clean all transaction and master tables except superadmin user
        with Session(sm.engine) as session:
            # Delete table rows using exact table names under explicit user confirmation
            session.execute(text("DELETE FROM units"))
            session.execute(text("DELETE FROM aliquots"))
            session.execute(text("DELETE FROM additional_charges"))
            session.execute(text("DELETE FROM expenses"))
            session.execute(text("DELETE FROM bank_statement"))
            session.execute(text("DELETE FROM notification_logs"))
            session.execute(text("DELETE FROM other_incomes"))
            session.execute(text("DELETE FROM board_members"))
            session.execute(text("DELETE FROM users WHERE role != 'superadmin'"))
            
            # Ensure superadmin user exists using clean SQLModel syntax
            superadmin = session.exec(select(User).where(User.role == "superadmin")).first()
            if not superadmin:
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
                
            session.commit()
            
        print("\n=======================================================")
        print("BASE DE DATOS LIMPIA CON ÉXITO")
        print("=======================================================")
        print("Se han eliminado todos los departamentos, alícuotas,")
        print("cargos, egresos, registros bancarios y logs de envío.")
        print("Se ha conservado únicamente la cuenta del Administrador.")
        print("\nCredenciales de Acceso Inicial:")
        print("  - Correo: superadmin@condo.com")
        print("  - Contraseña: super123")
        print("=======================================================")
        return True
    except Exception as e:
        safe_err = str(e).encode('ascii', errors='replace').decode('ascii')
        print(f"Error al limpiar la base de datos: {safe_err}")
        return False

if __name__ == "__main__":
    print("\n=======================================================")
    print("ADVERTENCIA DE SEGURIDAD - REINICIO DE BASE DE DATOS")
    print("=======================================================")
    print("Esta acción borrará de forma PERMANENTE e IRREVERSIBLE")
    print("todos los departamentos, alícuotas, pagos, gastos y registros.")
    print("=======================================================")
    try:
        user_input = input("Para confirmar esta operación, escriba 'CONFIRMAR': ")
        if user_input.strip() == "CONFIRMAR":
            reset_database(interactive_confirmed=True)
        else:
            print("\nOperación cancelada. No se modificó ningún dato de la base de datos.\n")
    except (EOFError, KeyboardInterrupt):
        print("\nOperación cancelada por el usuario. Base de datos intacta.\n")
