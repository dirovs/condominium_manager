import os
import base64
import hashlib
from typing import Optional

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:
    Fernet = None
    InvalidToken = Exception

SECRET_KEY_PATH = os.path.join(os.path.dirname(__file__), ".secret_key")

_fernet_instance = None

def get_fernet() -> Optional[Fernet]:
    global _fernet_instance
    if _fernet_instance is not None:
        return _fernet_instance

    if not Fernet:
        return None

    # 1. Try environment variable
    env_key = os.environ.get("SECRET_ENCRYPTION_KEY")
    if env_key:
        key_bytes = env_key.strip().encode("utf-8")
        try:
            _fernet_instance = Fernet(key_bytes)
            return _fernet_instance
        except Exception:
            pass

    # 2. Try reading from persistent .secret_key file
    if os.path.exists(SECRET_KEY_PATH):
        try:
            with open(SECRET_KEY_PATH, "rb") as f:
                key = f.read().strip()
                if key:
                    _fernet_instance = Fernet(key)
                    return _fernet_instance
        except Exception as e:
            print(f"[Crypto] Error reading .secret_key: {e}")

    # 3. Generate a new stable key and persist it
    try:
        new_key = Fernet.generate_key()
        with open(SECRET_KEY_PATH, "wb") as f:
            f.write(new_key)
        _fernet_instance = Fernet(new_key)
        return _fernet_instance
    except Exception as e:
        # Fallback deterministic key based on machine / environment salt
        salt = os.path.abspath(os.path.dirname(__file__)).encode("utf-8")
        deterministic_key = base64.urlsafe_b64encode(hashlib.sha256(b"condo_manager_master_key_" + salt).digest())
        _fernet_instance = Fernet(deterministic_key)
        return _fernet_instance

def is_encrypted(val: Optional[str]) -> bool:
    if not val or not isinstance(val, str):
        return False
    # Fernet tokens start with gAAAAA and are base64-encoded
    if val.startswith("gAAAAA") and len(val) >= 60:
        return True
    return False

def encrypt_secret(plain_text: Optional[str]) -> str:
    """
    Encrypts a plain text string into a Fernet ciphertext token.
    If already encrypted or empty, returns appropriate string.
    """
    if not plain_text:
        return ""
    plain_text = str(plain_text)
    if is_encrypted(plain_text):
        return plain_text

    fernet = get_fernet()
    if not fernet:
        return plain_text

    try:
        token = fernet.encrypt(plain_text.encode("utf-8"))
        return token.decode("utf-8")
    except Exception as e:
        print(f"[Crypto] Encryption error: {e}")
        return plain_text

def decrypt_secret(cipher_text: Optional[str]) -> str:
    """
    Decrypts a Fernet ciphertext token into a plain text string.
    If not encrypted or plain text (legacy), returns string as-is without raising error.
    """
    if not cipher_text:
        return ""
    cipher_text = str(cipher_text)
    if not is_encrypted(cipher_text):
        return cipher_text

    fernet = get_fernet()
    if not fernet:
        return cipher_text

    try:
        decrypted = fernet.decrypt(cipher_text.encode("utf-8"))
        return decrypted.decode("utf-8")
    except Exception:
        # If decryption fails (e.g. key changed or plain text), return as-is
        return cipher_text
