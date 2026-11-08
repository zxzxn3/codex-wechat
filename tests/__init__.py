"""Package marker so package-style test commands also isolate logging."""
from .support.helpers import isolate_diagnostics

isolate_diagnostics()
