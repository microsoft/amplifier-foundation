.PHONY: check-heredocs sync-heredocs

# ---------------------------------------------------------------------------
# Heredoc sync targets
# ---------------------------------------------------------------------------

# Verify that the PYEOF heredocs in recipe YAML files match the standalone
# Python scripts.  Exits non-zero when they drift.
check-heredocs:
	python recipes/scripts/generate_recipe_heredocs.py --check

# Regenerate the PYEOF heredocs from the standalone scripts (in-place).
sync-heredocs:
	python recipes/scripts/generate_recipe_heredocs.py
	@echo "Heredocs synced."