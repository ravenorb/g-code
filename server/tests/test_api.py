import io

import pytest
from httpx import ASGITransport, AsyncClient

from server.app.main import app


@pytest.fixture(scope="module")
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.anyio
async def test_upload_validation_flags_blacklist(client):
    payload = b"G1 X0 Y0 F1200\nM112 ; emergency stop\n"
    response = await client.post("/upload", files={"file": ("job.gcode", io.BytesIO(payload), "text/plain")})
    assert response.status_code == 200
    body = response.json()
    messages = [d["message"] for d in body["diagnostics"]]
    assert any("blacklisted" in msg.lower() for msg in messages)
    assert body["summary"]["errors"] >= 1


@pytest.mark.anyio
async def test_validate_and_release_flow(client):
    gcode = "G1 X0 Y0 F1200\nG1 X1 Y1 F800\nM5\n"
    validate_resp = await client.post("/validate", json={"gcode": gcode})
    assert validate_resp.status_code == 200
    job_id = validate_resp.json()["job_id"]
    assert validate_resp.json()["summary"]["errors"] == 0

    release_resp = await client.post("/release", json={"job_id": job_id, "approver": "qa"})
    assert release_resp.status_code == 200
    data = release_resp.json()
    assert data["status"] == "released"
    assert data["approved_by"] == "qa"


@pytest.mark.anyio
async def test_release_rejects_invalid_job(client):
    gcode = "G1 X0 Y0 F12000\n"
    validate_resp = await client.post("/validate", json={"gcode": gcode, "job_id": "too_fast"})
    assert validate_resp.status_code == 200
    assert validate_resp.json()["summary"]["errors"] >= 1

    release_resp = await client.post("/release", json={"job_id": "too_fast", "approver": "qa"})
    assert release_resp.status_code == 409


@pytest.mark.anyio
async def test_upload_reports_part_and_contour_counts(client):
    payload = (
        "N10000 HKOST(0.3,6.8,0.00,10001,1,0,0,0)\n"
        "N1 M3 S1000\n"
        "N10001 HKSTR(1,1,0,0,0,0,0,0)\n"
        "HKCUT(0,0,0)\n"
        "G1 X0 Y0\n"
        "G1 X1 Y1\n"
        "HKSTO(0,0,0)\n"
        "N20000 HKOST(0.3,6.8,0.00,20001,1,0,0,0)\n"
        "N20001 HKSTR(1,1,0,0,0,0,0,0)\n"
        "HKCUT(0,0,0)\n"
        "G1 X0 Y0\n"
        "HKSTO(0,0,0)\n"
    ).encode()

    response = await client.post("/upload", files={"file": ("job.mpf", io.BytesIO(payload), "text/plain")})
    assert response.status_code == 200
    body = response.json()
    assert "parts" in body
    assert len(body["parts"]) == 2
    contours = [part["contours"] for part in body["parts"]]
    assert contours == [2, 1]
