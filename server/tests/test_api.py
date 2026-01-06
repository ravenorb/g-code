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
