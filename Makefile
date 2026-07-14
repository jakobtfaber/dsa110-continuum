PYTHON ?= python
CLOUD_SAFE_BASETEMP ?=

.PHONY: test-cloud

# Stable pure-Python gate shared by local development and GitHub Actions.
# The full CASA/data-dependent suite remains authoritative on H17.
test-cloud:
	$(PYTHON) scripts/run_cloud_safe_tests.py \
		$(if $(CLOUD_SAFE_BASETEMP),--basetemp "$(CLOUD_SAFE_BASETEMP)")
