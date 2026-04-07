.PHONY: test lint pre-commit reqs docker-build up

test:
	cd backend && uv run pytest tests/ --cov=. --cov-report=term-missing --cov-fail-under=70
	cd frontend && uv run pytest tests/ --cov=. --cov-report=term-missing --cov-fail-under=70

lint:
	cd backend && uv run black . && uv run ruff check --fix . && uv run mypy .
	cd frontend && uv run black . && uv run ruff check --fix . && uv run mypy .

pre-commit:
	pre-commit install
	pre-commit run --all-files

reqs:
	cd backend && uv export --format requirements-txt --no-dev > requirements.txt
	cd frontend && uv export --format requirements-txt --no-dev > requirements.txt

docker-build: reqs
	docker build -t honey-backend ./backend
	docker build -t honey-frontend ./frontend

up:
	docker compose up
