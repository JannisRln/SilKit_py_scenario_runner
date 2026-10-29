# Getting started

## Requirements

Use CPython 3.12–3.14 on a platform supported by SilKit_py. Install the runner from the repository until its PyPI release:

```bash
python -m pip install "git+https://github.com/JannisRln/SilKit_py_scenario_runner.git"
python -m pip install pytest
```

For local development:

```bash
python -m pip install -e ".[test]"
python -m pytest -q
```

## Your first integration scenario

See [the existing runnable example](../examples/test_basic_scenario.py). It schedules a speed publication and checks an *external* echo participant. Run the matching registry, system controller, and echo participant first; include the scenario participant in coordinated lifecycle configuration.

```bash
SILKIT_RUN_INTEGRATION=1 SILKIT_REGISTRY_URI=silkit://localhost:8500 python -m pytest -v examples/test_basic_scenario.py
```

Without SILKIT_RUN_INTEGRATION=1 the example skips instead of waiting for an unavailable live environment. Unit tests do not require a live SIL Kit deployment.
