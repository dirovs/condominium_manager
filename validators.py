"""
Ecuadorian Identity and Tax ID (Cédula & RUC) validation algorithms.
"""

import re
from typing import Optional


def clean_id(val: Optional[str]) -> str:
    """Removes spaces, hyphens, and non-digit characters."""
    if not val:
        return ""
    return re.sub(r"[^\d]", "", str(val).strip())


def validate_cedula(cedula: str) -> bool:
    """
    Validates a 10-digit Ecuadorian Cédula de Identidad using the modulo 10 checksum algorithm.
    """
    cleaned = clean_id(cedula)
    if len(cleaned) != 10 or not cleaned.isdigit():
        return False

    # Check for all identical digits (e.g., 0000000000, 1111111111)
    if len(set(cleaned)) == 1:
        return False

    provincia = int(cleaned[:2])
    if not ((1 <= provincia <= 24) or provincia == 30):
        return False

    third_digit = int(cleaned[2])
    if third_digit >= 6:
        return False

    coefficients = [2, 1, 2, 1, 2, 1, 2, 1, 2]
    total = 0

    for i in range(9):
        val = int(cleaned[i]) * coefficients[i]
        if val >= 10:
            val -= 9
        total += val

    verifier = (10 - (total % 10)) % 10
    return verifier == int(cleaned[9])


def validate_ruc(ruc: str) -> bool:
    """
    Validates a 13-digit Ecuadorian RUC (Registro Único de Contribuyentes).
    Supports:
    1. Persona Natural (10 digits cédula + 001)
    2. Sociedad Privada / Extranjeros (3rd digit = 9, modulo 11 on 10th digit)
    3. Sociedad Pública / Entidad Estatal (3rd digit = 6, modulo 11 on 9th digit)
    """
    cleaned = clean_id(ruc)
    if len(cleaned) != 13 or not cleaned.isdigit():
        return False

    if len(set(cleaned)) == 1:
        return False

    provincia = int(cleaned[:2])
    if not ((1 <= provincia <= 24) or provincia == 30):
        return False

    third_digit = int(cleaned[2])

    # 1. Persona Natural (3rd digit < 6)
    if third_digit < 6:
        if not validate_cedula(cleaned[:10]):
            return False
        # Branch establishment code must be > 0 (usually 001)
        establishment = int(cleaned[10:])
        return establishment >= 1

    # 2. Sociedad Privada o Extranjeros sin cédula (3rd digit = 9)
    elif third_digit == 9:
        establishment = int(cleaned[10:])
        if establishment < 1:
            return False

        coefficients = [4, 3, 2, 7, 6, 5, 4, 3, 2]
        total = sum(int(cleaned[i]) * coefficients[i] for i in range(9))
        remainder = total % 11
        verifier = 0 if remainder == 0 else 11 - remainder
        return verifier == int(cleaned[9])

    # 3. Sociedad Pública o Institución del Estado (3rd digit = 6)
    elif third_digit == 6:
        establishment = int(cleaned[9:])
        if establishment < 1:
            return False

        coefficients = [3, 2, 7, 6, 5, 4, 3, 2]
        total = sum(int(cleaned[i]) * coefficients[i] for i in range(8))
        remainder = total % 11
        verifier = 0 if remainder == 0 else 11 - remainder
        return verifier == int(cleaned[8])

    return False


def validate_ecuadorian_id(id_val: str) -> bool:
    """
    Convenience validator that checks if the string is either a valid Ecuadorian Cédula or RUC.
    """
    cleaned = clean_id(id_val)
    if len(cleaned) == 10:
        return validate_cedula(cleaned)
    elif len(cleaned) == 13:
        return validate_ruc(cleaned)
    return False
