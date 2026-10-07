PY ?= python3
BACKEND_PORT ?= 8000

.PHONY: dev backend frontend install test sample build env

env:
	@test -f .env || (cp .env.example .env && echo "created .env from .env.example - add your API keys")

install: env
	$(PY) -m pip install -r requirements.txt
	cd frontend && npm install

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

test:
	$(PY) -m pytest -q
	cd frontend && npm run build

build:
	cd frontend && npm run build
