.PHONY: install test lint run-backend run-frontend docker-up docker-down

install:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt

test:
	.venv/bin/python -m pytest tests/ -v

test-ci:
	python -m pytest tests/ -v

lint:
	.venv/bin/python -m py_compile backend/main.py backend/parser.py backend/ai_engine.py backend/models.py backend/database.py frontend/app.py

run-backend:
	.venv/bin/uvicorn backend.main:app --reload --port 8000

run-frontend:
	.venv/bin/streamlit run frontend/app.py

docker-up:
	docker compose up --build

docker-down:
	docker compose down
