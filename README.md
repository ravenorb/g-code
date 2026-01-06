# g-code
G-code parser and storage

## HK Parser Service

A lightweight FastAPI service that validates HK G-code uploads, enforces safety limits, and records production releases.

### Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r server/requirements.txt
uvicorn server.app.main:app --reload
```

### Container

```bash
docker-compose up --build
```

The API and upload UI will be available on [http://localhost:8000](http://localhost:8000).
