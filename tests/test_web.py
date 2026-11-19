"""Drive the benchmark through HTTP, as each role would, and check role isolation."""
import re

import pytest
from fastapi.testclient import TestClient

from app import db as dbm


@pytest.fixture
def client():
    dbm.init("sqlite://")
    dbm.drop_all()
    from app.main import create_app
    return create_app()


def login(app, user):
    c = TestClient(app)
    r = c.post("/login", data={"username": user, "password": "demo123"}, follow_redirects=False)
    assert r.status_code == 303
    return c


def ids(html, pattern):
    return [int(x) for x in re.findall(pattern, html)]


def test_benchmark_via_http(client):
    prod, admin, sa, sb = (login(client, u) for u in ("prod", "admin", "salesA", "salesB"))
    item = lambda html, name: re.search(rf'value="(\d+)"[^>]*>{name}', html).group(1)  # noqa: E731

    # 1 purchase
    page = prod.get("/purchases").text
    r = prod.post("/purchases", data={"supplier_id": "1", "item_id": [item(page, "Flour")], "qty": ["60"],
                                      "unit": ["kg"], "rate": ["45"], "gst": ["0"]})
    assert "PUR/" in r.text
    # 2 start run
    page = prod.get("/production/new").text
    mats = re.findall(r'name="item_id" value="(\d+)"', page)
    r = prod.post("/production/start", data={"product_id": "6", "batches": "1", "item_id": mats,
                                             "qty": ["40", "10.5", "1.2"], "unit": ["kg", "l", "kg"],
                                             "wasted": ["0", "0", "0"]})
    run_id = ids(str(r.url), r"/production/(\d+)")[0]
    # 3 complete
    page = prod.get(f"/production/{run_id}").text
    pk = re.findall(r'name="item_id" value="(\d+)"', page)
    r = prod.post(f"/production/{run_id}/complete", data={"good_output": "188", "item_id": pk, "qty": ["188", "9"],
                                                          "wasted": ["6", "0"], "unit": ["pcs", "pcs"]})
    assert "MUR-" in r.text and "-6.00%" in r.text
    # 4 salesman A on mobile
    boot = sa.get("/api/m/bootstrap").json()
    assert [c["name"] for c in boot["customers"]] == ["Retailer A - Lakshmi Stores", "Retailer C - Murugan Bakery"]
    body = {"client_uuid": "u-1", "customer_id": boot["customers"][0]["id"],
            "lines": [{"product_id": boot["products"][0]["id"], "qty": 150, "unit": "packet"}]}
    o = sa.post("/api/m/orders", json=body).json()
    assert sa.post("/api/m/orders", json=body).json()["id"] == o["id"]  # resend after a dropped network
    # 5 approve, invoice, dispatch
    admin.post(f"/orders/{o['id']}/approve")
    r = admin.post(f"/orders/{o['id']}/invoice")
    inv_id = ids(str(r.url), r"/invoices/(\d+)")[0]
    assert "₹7,875.00" in r.text and "₹187.50" in r.text
    assert admin.get(f"/invoices/{inv_id}/pdf").content[:4] == b"%PDF"
    admin.post(f"/invoices/{inv_id}/dispatch", data={"vehicle_no": "TN45AB1234"})
    # 6 distributor B shortage
    boot = sb.get("/api/m/bootstrap").json()
    ob = sb.post("/api/m/orders", json={"client_uuid": "u-2", "customer_id": boot["customers"][0]["id"],
                                        "lines": [{"product_id": 6, "qty": 6, "unit": "carton"}]}).json()
    admin.post(f"/orders/{ob['id']}/approve")
    page = admin.get(f"/orders/{ob['id']}").text
    assert "Short by 32" in page and "Inter-state: IGST" in page
    r = admin.post(f"/orders/{ob['id']}/invoice")
    assert "Insufficient finished-goods stock" in r.text
    # 7 payment
    r = admin.post("/payments", data={"customer_id": "1", "amount": "4000", "invoice_id": str(inv_id), "mode": "upi"})
    assert "Outstanding now ₹3,875.00" in r.text
    # 8 overheads
    prod.post(f"/production/{run_id}/overhead", data={"category": "labour", "amount": "1200"})
    r = prod.post(f"/production/{run_id}/overhead", data={"category": "gas_power", "amount": "600"})
    assert "₹31.14" in r.text
    # 9 reports + live benchmark page
    audit = login(client, "audit")
    for key in ("sales_register", "gst_summary", "stock_summary", "production", "costing", "outstanding"):
        assert audit.get(f"/reports/{key}").status_code == 200
        assert audit.get(f"/reports/{key}?format=xlsx").content[:2] == b"PK"
    assert "11 / 11 checks pass" in audit.get("/benchmark").text


def test_roles_are_isolated(client):
    sa = login(client, "salesA")
    assert sa.get("/orders").status_code == 403
    assert sa.get("/api/m/orders").status_code in (403, 405)
    audit = login(client, "audit")
    assert audit.post("/orders/1/approve").status_code == 403
    assert audit.get("/api/m/bootstrap").status_code == 403
    prod = login(client, "prod")
    assert prod.get("/reports").status_code == 403
    assert TestClient(client).get("/orders", follow_redirects=False).status_code == 303  # not logged in


def test_every_page_renders(client):
    owner = login(client, "owner")
    for url in ("/dashboard", "/orders", "/invoices", "/payments", "/production", "/purchases", "/stock",
                "/stock/ledger", "/masters", "/reports", "/audit", "/benchmark", "/overheads"):
        assert owner.get(url).status_code == 200, url
    assert login(client, "salesA").get("/m/").status_code == 200
