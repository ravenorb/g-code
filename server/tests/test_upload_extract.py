import io
import json
import os
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from server.app.main import app, get_settings
from server.app.settings import AppSettings


@pytest.fixture
def temp_settings(tmp_path, monkeypatch):
    nas_root = tmp_path / "nas"
    settings = AppSettings(nas_path=nas_root)
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.clear()


def _sample_program() -> bytes:
    return (
        "HKLDB(2,\"S304\",3,0,0,0)\n"
        "HKINI(15,10,5,0,0,0)\n"
        "N100 HKOST(0.1,0.2,0.00,10001,1,0,0,0)\n"
        "HKPPP\n"
        "N101 HKSTR(0,1,1,2,0,1.5,2.5,0)\n"
        "HKPIE(0,0,0)\n"
        "HKLEA(0,0,0)\n"
        "HKCUT(0,0,0)\n"
        "G1 X1 Y2\n"
        "G1 X2 Y3\n"
        "HKSTO(0,0,0)\n"
        "HKPED(0,0,0)\n"
        "HKEND(0,0,0)\n"
        "M30\n"
    ).encode()


@pytest.fixture
async def client(temp_settings):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.anyio
async def test_upload_and_list_files(client, temp_settings):
    payload = _sample_program()
    resp = await client.post(
        "/api/upload",
        files={"file": ("job.mpf", io.BytesIO(payload), "text/plain")},
        data={"description": "test upload"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["summary"]["parts"] == 1
    meta_path = Path(body["meta"])
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert meta["description"] == "test upload"
    assert meta["summary"]["parts"] == 1

    list_resp = await client.get("/api/files")
    assert list_resp.status_code == 200
    items = list_resp.json()
    assert len(items) == 1
    assert items[0]["id"] == body["id"]
    assert items[0]["summary"]["parts"] == 1


@pytest.mark.anyio
async def test_extract_part_writes_file(client, temp_settings):
    payload = _sample_program()
    upload = await client.post(
        "/api/upload",
        files={"file": ("job2.mpf", io.BytesIO(payload), "text/plain")},
        data={"description": "extract me"},
    )
    file_id = upload.json()["id"]

    extract = await client.post(f"/api/files/{file_id}/extract-part", json={"partId": "10001"})
    assert extract.status_code == 200, extract.text
    data = extract.json()
    extracted_path = Path(data["file"])
    assert extracted_path.exists()
    extracted_meta_path = Path(data["meta"]["paths"]["meta"])
    assert extracted_meta_path.exists()
    extracted_meta = json.loads(extracted_meta_path.read_text())
    assert extracted_meta["summary"]["parts"] == 1
    assert "HKOST" in extracted_path.read_text()
