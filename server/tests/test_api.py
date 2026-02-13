import io
from pathlib import Path

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
        "N10000 HKOST(0.0,0.0,0.0,10001,1,0,0,0)\n"
        "HKPPP\n"
        "N20000 HKOST(1.0,1.0,0.0,20001,1,0,0,0)\n"
        "HKPPP\n"
        "N10001 HKSTR(1,1,0,0,0,0,0,0)\n"
        "HKCUT(0,0,0)\n"
        "G1 X0 Y0\n"
        "G1 X1 Y1\n"
        "HKSTO(0,0,0)\n"
        "N10002 HKSTR(1,1,0,0,0,0,0,0)\n"
        "HKCUT(0,0,0)\n"
        "G1 X0 Y0\n"
        "HKSTO(0,0,0)\n"
        "HKPED(0,0,0)\n"
        "N20001 HKSTR(1,1,0,0,0,0,0,0)\n"
        "HKCUT(0,0,0)\n"
        "G1 X0 Y0\n"
        "HKSTO(0,0,0)\n"
        "HKPED(0,0,0)\n"
    ).encode()

    response = await client.post("/upload", files={"file": ("job.mpf", io.BytesIO(payload), "text/plain")})
    assert response.status_code == 200
    body = response.json()
    assert "parts" in body
    assert len(body["parts"]) == 2
    contours = [part["contours"] for part in body["parts"]]
    part_lines = [part["part_line"] for part in body["parts"]]
    assert contours == [2, 1]
    assert part_lines == [10000, 20000]


@pytest.mark.anyio
async def test_upload_persists_metadata_and_extracts_part(client):
    payload = (
        "HKLDB(2,\"S304\",3,0,0,0)\n"
        "HKINI(2,21.5,6.2,0,0,0)\n"
        "N10000 HKOST(0.3,0.26,0.00,10001,5,0,0,0)\n"
        "HKPPP\n"
        "N20000 HKEND(0,0,0)\n"
        "N10 M30\n"
        "\n"
        "N10001 HKSTR(1,1,1.0,2.0,0,0.5,0.5,0)\n"
        "HKPIE(0,0,0)\n"
        "HKLEA(0,0,0)\n"
        "G1 X1.0 Y2.0\n"
        "HKCUT(0,0,0)\n"
        "G1 X2.0 Y3.0\n"
        "HKSTO(0,0,0)\n"
        "HKPED(0,0,0)\n"
    ).encode()

    response = await client.post(
        "/upload",
        files={
            "file": ("job.mpf", io.BytesIO(payload), "text/plain"),
            "attachment": ("job.pdf", io.BytesIO(b"%PDF-1.4 test"), "application/pdf"),
        },
        data={"description": "single part demo"},
    )
    assert response.status_code == 200
    upload_body = response.json()
    assert upload_body["stored_path"]
    assert upload_body["meta_path"]
    assert upload_body["link_meta_path"]
    assert upload_body["linked_files"]
    assert Path(upload_body["stored_path"]).exists()
    assert Path(upload_body["meta_path"]).exists()
    assert Path(upload_body["link_meta_path"]).exists()

    part_label = 10000
    extract_response = await client.post(
        "/extract",
        json={"job_id": upload_body["job_id"], "part_label": part_label, "margin": 0.1},
    )
    assert extract_response.status_code == 200
    extract_body = extract_response.json()
    assert Path(extract_body["stored_path"]).exists()
    extracted_text = Path(extract_body["stored_path"]).read_text()
    assert "HKINI(" in extracted_text
    assert "M30" in extracted_text
    assert "HKPPP" in extracted_text
    assert "HKPED" in extracted_text
    assert "HKSTR" in extracted_text
    assert extract_body["width"] > 0
    assert extract_body["height"] > 0


@pytest.mark.anyio
async def test_cut_order_program_renumbers_parts(client):
    payload = (
        "N10000 HKOST(0.0,0.0,0.0,10001,1,0,0,0)\n"
        "HKPPP\n"
        "N20000 HKOST(1.0,1.0,0.0,20001,1,0,0,0)\n"
        "HKPPP\n"
        "N10001 HKSTR(1,1,1,1,0,0,0,0)\n"
        "HKPED(0,0,0)\n"
        "N20001 HKSTR(1,1,2,2,0,0,0,0)\n"
        "HKPED(0,0,0)\n"
    ).encode()

    response = await client.post("/upload", files={"file": ("job.mpf", io.BytesIO(payload), "text/plain")})
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    reorder_resp = await client.post(f"/jobs/{job_id}/cut-order/program", json={"order": [2, 1]})
    assert reorder_resp.status_code == 200
    text = reorder_resp.text

    assert "N10000 HKOST(1.0,1.0,0.0,10001,1,0,0,0)" in text
    assert "N20000 HKOST(0.0,0.0,0.0,20001,1,0,0,0)" in text
    assert "N10001 HKSTR(1,1,2,2,0,0,0,0)" in text
    assert "N20001 HKSTR(1,1,1,1,0,0,0,0)" in text




@pytest.mark.anyio
async def test_data_files_lists_uppercase_mpf_extension(client):
    payload = b"G1 X0 Y0\n"
    upload_resp = await client.post(
        "/upload",
        files={"file": ("UPPER.MPF", io.BytesIO(payload), "text/plain")},
    )
    assert upload_resp.status_code == 200

    data_files_resp = await client.get("/data-files")
    assert data_files_resp.status_code == 200
    files = data_files_resp.json()
    assert any(item["filename"] == "UPPER.MPF" for item in files)

@pytest.mark.anyio
async def test_index_and_match_pages_are_available(client):
    index_resp = await client.get("/")
    assert index_resp.status_code == 200
    assert "Uploaded MPF Files" in index_resp.text
    assert 'id="upload-form"' in index_resp.text
    assert 'window.location.href = `/jobs/${encodeURIComponent(body.job_id)}`;' in index_resp.text

    match_resp = await client.get("/match")
    assert match_resp.status_code == 200
    assert "Sample Library" in match_resp.text


@pytest.mark.anyio
async def test_part_detail_and_download_apply_contour_order_and_extra_contours(client):
    payload = (
        "N10000 HKOST(0.0,0.0,0.0,10001,1,0,0,0)\n"
        "HKPPP\n"
        "N20000 HKOST(5.0,5.0,0.0,20001,1,0,0,0)\n"
        "HKPPP\n"
        "N10001 HKSTR(1,1,0,0,0,0,0,0)\n"
        "G1 X0 Y0\n"
        "HKSTO(0,0,0)\n"
        "N10002 HKSTR(1,1,1,1,0,0,0,0)\n"
        "G1 X1 Y1\n"
        "HKSTO(0,0,0)\n"
        "HKPED(0,0,0)\n"
        "N20001 HKSTR(1,1,2,2,0,0,0,0)\n"
        "G1 X2 Y2\n"
        "HKSTO(0,0,0)\n"
        "HKPED(0,0,0)\n"
    ).encode()

    upload_resp = await client.post("/upload", files={"file": ("job.mpf", io.BytesIO(payload), "text/plain")})
    assert upload_resp.status_code == 200
    job_id = upload_resp.json()["job_id"]

    detail_resp = await client.get(
        f"/jobs/{job_id}/parts/1",
        params={"extra_contours": "2.1", "contour_order": "2.1,2,1"},
    )
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    joined = "\n".join(detail["part_program"])
    assert "HKSTR(1,1,2,2,0,0,0,0)" in joined
    assert joined.find("HKSTR(1,1,2,2,0,0,0,0)") < joined.find("HKSTR(1,1,1,1,0,0,0,0)")

    download_resp = await client.get(
        f"/jobs/{job_id}/parts/1/program",
        params={"extra_contours": "2.1", "contour_order": "2.1,2,1"},
    )
    assert download_resp.status_code == 200
    text = download_resp.text
    assert "HKSTR(1,1,2,2,0,0,0,0)" in text
    assert text.find("HKSTR(1,1,2,2,0,0,0,0)") < text.find("HKSTR(1,1,1,1,0,0,0,0)")
