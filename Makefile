.PHONY: test test-integration test-eval lint pre-commit pre-commit-install reqs docker-build up

test:
	cd backend && uv run pytest tests/ --cov=. --cov-report=term-missing --cov-fail-under=70
	cd frontend && uv run pytest tests/ --cov=. --cov-report=term-missing --cov-fail-under=70

test-integration:
	cd backend && uv run pytest tests/test_evals.py -m integration -vs

test-eval:
	cd backend && uv run python tests/run_evals.py 2>&1 | tee ../_docs/$${ENV:-local}-eval-results.txt

lint:
	cd backend && uv run black . && uv run ruff check --fix . && uv run mypy .
	cd frontend && uv run black . && uv run ruff check --fix . && uv run mypy .

pre-commit-install:
	uv run pre-commit install

pre-commit:
	uv run pre-commit run --all-files

reqs:
	cd backend && uv export --format requirements-txt --no-dev > requirements.txt
	cd frontend && uv export --format requirements-txt --no-dev > requirements.txt

docker-build: reqs
	docker build -t honey-backend ./backend
	docker build -t honey-frontend ./frontend

up:
	docker compose up
