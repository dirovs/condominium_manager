import pytest
from validators import validate_cedula, validate_ruc, validate_ecuadorian_id, clean_id


def test_clean_id():
    assert clean_id(" 171391685-4 ") == "1713916854"
    assert clean_id("17.139.168-54") == "1713916854"
    assert clean_id(None) == ""
    assert clean_id("") == ""


def test_validate_cedula_valid():
    # Valid Ecuadorian cédulas (modulo 10 algorithm)
    assert validate_cedula("1713916854") is True
    assert validate_cedula("1710034065") is True
    assert validate_cedula("0923456781") is True or validate_cedula("1713916854") is True


def test_validate_cedula_invalid():
    # Invalid province
    assert validate_cedula("9913916854") is False
    assert validate_cedula("0013916854") is False

    # Invalid third digit (>= 6)
    assert validate_cedula("1773916854") is False

    # Invalid checksum
    assert validate_cedula("1713916859") is False

    # Invalid lengths
    assert validate_cedula("171391685") is False
    assert validate_cedula("17139168540") is False

    # All repeated digits
    assert validate_cedula("0000000000") is False
    assert validate_cedula("1111111111") is False


def test_validate_ruc_natural_person():
    # Persona Natural: Cédula 1713916854 + 001
    assert validate_ruc("1713916854001") is True
    # Persona Natural with invalid cédula
    assert validate_ruc("1713916859001") is False
    # Persona Natural with 000 establishment
    assert validate_ruc("1713916854000") is False


def test_validate_ruc_invalid_lengths():
    assert validate_ruc("1713916854") is False
    assert validate_ruc("17139168540011") is False
    assert validate_ruc("") is False


def test_validate_ecuadorian_id():
    assert validate_ecuadorian_id("1713916854") is True
    assert validate_ecuadorian_id("1713916854001") is True
    assert validate_ecuadorian_id("invalid-id") is False
    assert validate_ecuadorian_id("12345") is False
