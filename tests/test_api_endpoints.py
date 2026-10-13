import pytest


def test_get_summary_endpoint(client):
    response = client.get("/api/summary")
    assert response.status_code == 200
    data = response.json()
    assert "total_revenue" in data
    assert "total_expenses" in data
    assert "net_balance" in data
    assert "total_debt" in data


def test_get_units_endpoint(client):
    response = client.get("/api/units")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_get_settings_endpoint(client):
    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    assert "condo_name" in data


def test_get_debtors_endpoint(client):
    response = client.get("/api/debtors")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_role_access_control_block_mutation(client, owner_headers):
    # Resident / Owner attempting to create an expense should be blocked (403)
    payload = {
        "date": "2026-05-01",
        "category": "Mantenimiento",
        "description": "Gasto No Autorizado",
        "amount": 100.0
    }
    response = client.post("/api/expenses", json=payload, headers=owner_headers)
    assert response.status_code == 403


def test_admin_allowed_to_create_and_delete_expense(client, admin_headers):
    # Admin is authorized to create an expense
    payload = {
        "date": "2026-05-01",
        "category": "Mantenimiento",
        "description": "Gasto de Prueba Admin Pytest",
        "amount": 25.50
    }
    response = client.post("/api/expenses", json=payload, headers=admin_headers)
    assert response.status_code == 200
    created = response.json()
    assert created["amount"] == 25.50

    # Clean up by deleting
    del_res = client.delete(f"/api/expenses/{created['id']}", headers=admin_headers)
    assert del_res.status_code == 200
