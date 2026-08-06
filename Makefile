.PHONY: test validate

test:
	python3 -m unittest discover -s tests -v

validate:
	python3 -m py_compile twinclip/scripts/*.py
