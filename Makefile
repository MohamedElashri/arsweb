SHELL := /bin/bash

.PHONY: help venv fonts fetch build clean preview deploy

VENV = .venv
ACTIVATE = source $(VENV)/bin/activate

help:
	@echo "Available commands:"
	@echo "  make venv      Create virtual environment and install deps"
	@echo "  make fonts     Download fonts locally"
	@echo "  make fetch     Fetch RSS feeds from sources.txt"
	@echo "  make build     Generate static site"
	@echo "  make clean     Remove generated output"
	@echo "  make preview   Open generated site in browser"
	@echo "  make deploy    Run full pipeline and open result"

venv:
	uv venv $(VENV)
	$(ACTIVATE) && uv pip install -r requirements.txt

fonts:
	$(ACTIVATE) && python scripts/download_fonts.py

fetch:
	$(ACTIVATE) && python scripts/fetch_feeds.py

build:
	$(ACTIVATE) && python scripts/generate_site.py

clean:
	rm -rf public/ feed_cache.json

preview:
	open public/index.html

deploy: clean fonts fetch build preview
