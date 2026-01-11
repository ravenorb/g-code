# g-code
G-code parser and storage

## HK Parser Service

A lightweight FastAPI service that validates HK G-code uploads, enforces safety limits, and records production releases.

The service now also:

- Stores uploaded programs and generated metadata on a configurable storage root (NAS mount friendly).
- Captures an operator-supplied description alongside parsed parts/setup info.
- Supports extracting a single part to its own program, re-based at the sheet origin with a right-sized HKINI.
- Provides a simple browser UI at `/` for uploads, validation results, and one-click part extraction.

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

If the build fails with a BuildKit snapshot error (for example, "parent snapshot ... does not exist"), clear the builder cache and retry or temporarily disable BuildKit:

```bash
docker builder prune
DOCKER_BUILDKIT=0 docker-compose up --build
```

You can override the storage location (for example, to point at an NAS mount) by setting `STORAGE_ROOT`:

```bash
STORAGE_ROOT=/mnt/nas/gcode \
  docker-compose up --build
```

With the stack running, open http://localhost:8000 to access the upload UI. Upload a file, add a description, review the parsed diagnostics/parts, and click “Extract” on any part to generate a standalone file at the storage root.

The API will be available on [http://localhost:8000](http://localhost:8000).
