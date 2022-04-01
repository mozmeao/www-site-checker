all: help

compile-requirements:
	./bin/compile-requirements.sh

help:
	@echo "Please use \`make <target>' where <target> is one of"
	@echo "  compile-requirements   - update Python requirements files using pip-compile-multi"

install-local-python-deps:
	pip install -r requirements.txt

test:
	pytest --cov=bin --cov-report html:python_coverage tests/



.PHONY: all compile-requirements help install-local-python-deps test
