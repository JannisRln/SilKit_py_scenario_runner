"""Sphinx configuration for the SIL Kit Python Scenario Runner."""
from __future__ import annotations

project = "SIL Kit Python Scenario Runner"
author = "SilKit_py_scenario_runner contributors"
release = "0.1.1"

extensions = ["myst_parser"]
source_suffix = {".md": "markdown"}
root_doc = "index"
html_theme = "furo"
html_title = "SIL Kit Python Scenario Runner"
exclude_patterns = ["_build"]
myst_heading_anchors = 3
html_theme_options = {
    "source_repository": "https://github.com/JannisRln/SilKit_py_scenario_runner/",
    "source_branch": "main",
    "source_directory": "docs/",
}
