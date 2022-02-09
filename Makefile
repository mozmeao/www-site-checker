all: help

compile-requirements:
	./bin/compile-requirements.sh

help:
	@echo "Please use \`make <target>' where <target> is one of"
	@echo "  compile-requirements   - update Python requirements files using pip-compile-multi"

.PHONY: all compile-requirements help