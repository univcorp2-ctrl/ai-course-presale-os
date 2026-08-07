.PHONY: install test lint doctor run serve

install:
	python -m pip install -e '.[dev]'

test:
	pytest -q

lint:
	ruff check .

doctor:
	courseforge doctor

run:
	courseforge run-daily

serve:
	uvicorn courseforge.web:app --reload
