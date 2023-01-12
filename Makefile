all: help

compile-requirements:
	./bin/compile-requirements.sh

update-dictionaries:
	./bin/update-dictionaries.sh

help:
	@echo "Please use \`make <target>' where <target> is one of"
	@echo "  compile-requirements   - update Python requirements files using pip-compile-multi"
	@echo "  update-dictionaries   - update Hunspell-compatible dictionaries, sourced from Firefox codebase"

test:
	pytest --cov=bin tests/

.PHONY: all compile-requirements update-dictionaries help test
