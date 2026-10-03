# Clipper AI — common tasks.  `make help` lists everything.
SHELL := /bin/bash
PORT ?= 8000
IMAGE ?= clipper-ai

.PHONY: help deps build dev run preview seed api overlays docker docker-run up down test smoke clean packs sfx memes

help: ## show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

deps: ## fetch ffmpeg + vosk model + python deps (idempotent)
	bash scripts/ensure_deps.sh

build: ## build the SPA into backend/static
	cd frontend && npm install && npm run build

dev: ## vite dev server on :5173 (proxies the API to :$(PORT))
	cd frontend && npm run dev

api: ## run the API (serves the SPA if it has been built)
	cd backend && PYTHONPATH=vendor python3 -m uvicorn app.main:app --host 0.0.0.0 --port $(PORT)

run: build api ## build the SPA then serve everything on :$(PORT)

preview: deps build memes api ## restore everything (ffmpeg + model + deps + SPA + meme placeholders) then serve

seed: ## create the bundled sample project and render its top clip
	python3 scripts/seed_preview.py

overlays: ## regenerate the built-in overlay loops (assets/overlays/*.mp4)
	bash scripts/make_overlays.sh

sfx: ## (re)build the committed offline VFX kit (assets/sfx/kit, no network)
	python3 scripts/make_sfx_pack.py

memes: ## (re)build the offline meme placeholders (assets/memes, no key needed)
	python3 scripts/make_meme_pack.py

packs: ## fetch the real packs: Pixabay VFX sounds + Vlipsy memes (needs network/keys)
	python3 scripts/fetch_packs.py --sound
	@test -n "$$VLIPSY_API_KEY" && python3 scripts/fetch_packs.py --memes --sounds || \
	  echo "set VLIPSY_API_KEY (api@vlipsy.com) for the meme pack, or drop clips into assets/memes/"

packs-check: ## show which packs are installed on this machine
	python3 scripts/fetch_packs.py --check

docker: ## build the container image
	docker build -t $(IMAGE) .

docker-run: docker ## run the container locally on :$(PORT)
	docker run --rm -it -p $(PORT):$(PORT) -e PORT=$(PORT) -v clipper-data:/var/data $(IMAGE)

up: ## docker compose up (detached)
	docker compose up -d --build

down: ## docker compose down
	docker compose down

test: ## backend tests + frontend production build
	python3 -m compileall -q backend/app && echo "backend: byte-compile OK"
	PYTHONPATH=backend:backend/vendor python3 -m pytest backend/tests -q
	cd frontend && npm run build

smoke: ## jsdom UI smoke test against a server on :8000 (needs a project)
	cd frontend && npm run smoke

clean: ## remove build output and runtime data
	rm -rf backend/static backend/uploads backend/renders backend/captions backend/chunks backend/data
	find backend -name __pycache__ -type d -prune -exec rm -rf {} +
