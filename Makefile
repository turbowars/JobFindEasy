PY := .venv/bin/python
UVICORN := .venv/bin/uvicorn
HOST ?= 127.0.0.1
PORT ?= 8826

.PHONY: run dev kill scrape prefilter score notify pipeline stats install

run:
	@echo "→ JobFindEasy on http://$(HOST):$(PORT)  (Ctrl-C to stop)"
	@$(MAKE) -s kill >/dev/null 2>&1 || true
	$(UVICORN) web.app:app --host $(HOST) --port $(PORT) --reload --log-level info

dev: run

kill:
	@pkill -f "uvicorn web.app" 2>/dev/null && echo "killed" || echo "nothing running"

install:
	$(PY) -m pip install -e .

scrape:
	$(PY) -m src.cli scrape

prefilter:
	$(PY) -m src.cli prefilter

score:
	$(PY) -m src.cli score

notify:
	$(PY) -m src.cli notify

pipeline:
	$(PY) -m src.cli run

stats:
	$(PY) -m src.cli stats
