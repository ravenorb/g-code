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

To run a beta instance alongside the main container (for example, on port 8000), use the beta compose file with a
different project name:

```bash
docker-compose -p hk-parser-beta -f docker-compose.beta.yml up --build
```

You can override the storage location (for example, to point at an NAS mount) by setting `STORAGE_ROOT`:

```bash
STORAGE_ROOT=/mnt/nas/gcode \
  docker-compose up --build
```

With the stack running, open http://localhost to access the upload UI (the default compose file maps port 80). Upload a
file, add a description, review the parsed diagnostics/parts, and click “Extract” on any part to generate a
standalone file at the storage root.

The API will be available on [http://localhost](http://localhost). If you run the app directly with Uvicorn, the
default port is 8000.

If you use `reload.sh` on a host where `/opt` is not writable, set a writable target directory:

```bash
TARGET_BASE_DIR=$HOME/mts ./reload.sh
```

The script now also auto-falls back to `$HOME/mts` when the target base directory is not writable.
