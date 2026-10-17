# Run from the prototype/ directory. `make help` lists the targets.
VENV := .venv
PY   := $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,python3)

.PHONY: help setup doctor doctor-offline eval eval-baseline test demo run judge e2e e2e-judge browser browser-judge deploy deploy-dry ucp-conformance ucp-reference ucp-server ucp-shopper academy

help:            ## list the targets
	@grep -E '^[a-z0-9-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-18s %s\n", $$1, $$2}'

setup:           ## create .venv, install, create .env, run the offline checks and the tests
	./scripts/setup.sh

doctor:          ## check every key in .env against the real services (Gemini, WhatsApp, Xendit, Biteship, Instagram)
	$(PY) -m etalase.doctor

doctor-offline:  ## same, without any network call
	$(PY) -m etalase.doctor --offline

eval:            ## catalog-extraction accuracy: offline rules, plus Gemini when a key is set (writes evals/results/latest.md)
	$(PY) -m evals.run_eval

eval-baseline:   ## accuracy of the offline rules only
	$(PY) -m evals.run_eval --extractor heuristic

test:            ## unit tests (no network, no keys)
	$(PY) -m pytest -q

demo:            ## the whole flow in the terminal on mock services
	$(PY) scripts/demo.py

judge:           ## the judge sandbox on http://localhost:8080/demo: shared demo seller, simulated seller phone, simulated payment (see ../JUDGES.md)
	DEMO_MODE=true $(PY) -m uvicorn etalase.api.main:app --port 8080

e2e-judge:       ## the reviewer's path through the sandbox over real HTTP (starts its own server)
	$(PY) scripts/e2e_judge.py

browser-judge:   ## the same path in a real browser, including the demo payment button (needs playwright)
	$(PY) scripts/browser_judge.py

e2e:             ## the whole story over real HTTP: starts the app, then seller, agent (UCP and MCP), payment, shipping, payout
	$(PY) scripts/e2e_http.py

browser:         ## real-browser check of the buyer pages: smooth navigation, back button, stepper, busy state, no-JS fallback (needs playwright)
	$(PY) scripts/browser_check.py

run:             ## API on http://localhost:8080 (reads .env)
	$(PY) -m uvicorn etalase.api.main:app --reload --port 8080

deploy:          ## deploy to Cloud Run using .env  (PROJECT_ID=my-project make deploy; DEMO=1 for the judge sandbox, a separate service)
	./scripts/deploy_cloud_run.sh

deploy-dry:      ## print every deploy step without changing anything  (PROJECT_ID=my-project make deploy-dry)
	DRY_RUN=1 ./scripts/deploy_cloud_run.sh

ucp-conformance: ## run Google's official UCP conformance suite against Etalase (needs git, uv, network)
	$(PY) -m ucp_conformance.run

ucp-reference:   ## same suite against the official sample server: proves the suite itself works
	$(PY) -m ucp_conformance.run --target reference

ucp-server:      ## Etalase with fixed demo items on :8190, to try UCP requests by hand
	$(PY) -m ucp_conformance.server --port 8190

ucp-shopper:     ## a simulated AI shopping surface buys two products over UCP, start to finish (own sandbox; not Google AI Mode)
	$(PY) scripts/ucp_shopper.py

academy:         ## build the documentation site (../learn)
	cd ../learn && npm ci --no-audit --no-fund && npm run build
