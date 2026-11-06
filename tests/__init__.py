"""Package marker so ``python -m unittest tests.test_x`` also isolates logging."""
try:
    from .test_support import isolate_diagnostics
except ImportError:  # ``discover -s tests`` imports this module as a top-level name.
    from test_support import isolate_diagnostics

isolate_diagnostics()
