"""web-operator — policy-gated browser automation.

Every operation is checked against a host x operation-category matrix
(fail-closed). State-changing operations (category "mutate", e.g. form submit)
additionally require a preview that a human approves before it runs. The browser
backend is pluggable (a fake backend ships for offline tests; a Playwright
backend is optional). Every operation and denial is audited.
"""

from .core import __all__ as _core_all  # noqa: F401

__version__ = "0.1.0"
