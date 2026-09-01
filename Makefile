.PHONY: demo test page samples verify-js scan explain clean

VENV := .venv
PY   := $(VENV)/bin/python

$(VENV):
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -q -e ".[dev,parquet]"

demo: $(VENV)
	$(PY) demo/make_synthetic.py --records 100000
	$(PY) demo/make_torture_data.py
	@echo
	-$(PY) -m overpunch.cli scan demo/UTLBILL.cpy demo/UTLBILL.dat
	@echo
	$(PY) -m overpunch.cli explain demo/UTLBILL.cpy demo/UTLBILL.dat \
		--hypotheses demo/nemotron-reply.json --limit 20000

page: $(VENV)
	$(PY) demo/make_synthetic.py --records 100000 >/dev/null
	$(PY) page/build.py

verify-js: $(VENV)
	$(PY) page/reference.py
	node page/verify_js.js

samples: $(VENV)
	$(PY) samples/build_samples.py

test: $(VENV)
	$(PY) samples/build_samples.py
	$(PY) demo/make_torture_data.py
	$(PY) -m pytest -q

clean:
	rm -rf $(VENV) demo/UTLBILL.dat samples/data demo/UTLBILL.cpy out.parquet .pytest_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
