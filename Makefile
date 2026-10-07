# Uses ./.venv automatically (created by `make install`), so it also works on macOS/Homebrew Pythons
# that refuse system-wide pip installs ("externally-managed-environment").
PYTHON ?= python3
PY := $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo $(PYTHON))
BACKEND_PORT ?= 8000

.PHONY: dev backend frontend install test sample build env check revoice

env:
	@test -f .env || (cp .env.example .env && echo "created .env from .env.example - add your API keys")

install: env
	@test -d .venv || $(PYTHON) -m venv .venv
	.venv/bin/python -m pip install -U pip
	.venv/bin/python -m pip install -r requirements.txt
	cd frontend && npm install
	@echo "Done. Next: edit .env, then 'make dev' and open http://localhost:5173/check"

# Starts backend (:8000) + frontend (:5173). Ctrl-C stops both.
dev: env
	@trap 'kill 0' INT TERM EXIT; \
	$(PY) -m uvicorn backend.main:app --host 127.0.0.1 --port $(BACKEND_PORT) --reload --reload-dir backend & \
	(cd frontend && npm run dev) & \
	wait

backend:
	$(PY) -m uvicorn backend.main:app --host 127.0.0.1 --port $(BACKEND_PORT) --reload --reload-dir backend

frontend:
	cd frontend && npm run dev

sample:
	$(PY) scripts/make_sample_deck.py

# Same checks as the in-app setup page, from the terminal (backend must be running)
check:
	@curl -s localhost:$(BACKEND_PORT)/preflight | $(PY) -c "import json,sys; r=json.load(sys.stdin); print('overall:', r['overall']); [print(f\"  {c['status']:5} {c['label']}: {c['detail']}\") for c in r['checks']]"

# Re-generate the voice of every ready deck (after changing ELEVENLABS_VOICE_ID). Backend must be running.
revoice:
	@for id in $$(curl -s localhost:$(BACKEND_PORT)/decks | $(PY) -c "import json,sys; [print(d['deck_id']) for d in json.load(sys.stdin) if d['status']=='ready']"); do \
	  echo "re-voicing deck $$id"; curl -s -X POST localhost:$(BACKEND_PORT)/decks/$$id/audio; echo; \
	done; echo "Generation runs in the background; watch 'Voice:' on the deck card (Upload page)."

test:
	$(PY) -m pytest -q
	cd frontend && npm run build

build:
	cd frontend && npm run build
