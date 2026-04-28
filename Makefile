PY := .venv/bin/python
UVICORN := .venv/bin/uvicorn
HOST ?= 127.0.0.1
PORT ?= 8826
RELOAD ?= 1

# When RELOAD=1, uvicorn watches Python sources AND templates/static so
# .html/.css/.js edits hot-reload too. Override with `make run RELOAD=0`
# for a prod-like single-process run.
RELOAD_FLAGS := --reload \
                --reload-dir src \
                --reload-dir web \
                --reload-include '*.py' \
                --reload-include '*.html' \
                --reload-include '*.css' \
                --reload-include '*.js'

.PHONY: run dev kill scrape prefilter score notify pipeline stats install

run:
	@echo "→ JobFindEasy on http://$(HOST):$(PORT)  (Ctrl-C to stop)  RELOAD=$(RELOAD)"
	@$(MAKE) -s kill >/dev/null 2>&1 || true
	$(UVICORN) web.app:app --host $(HOST) --port $(PORT) $(if $(filter 1,$(RELOAD)),$(RELOAD_FLAGS),) --log-level info

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
