"""Re-export the shared auth fixtures for this directory.

The same fixtures are offered to the whole suite through
`pytest_plugins = ["app.tests.auth.fixtures"]` in the *root* conftest (orchestrator).
A `pytest_plugins` line is not allowed in a non-root conftest, so this file imports the
names instead — which is all pytest needs to collect them here.
"""

from app.tests.auth.fixtures import (  # noqa: F401
    ADMIN_EMAIL,
    BLOGGER_EMAIL,
    TEST_PASSWORD,
    admin_headers,
    admin_user,
    blogger_headers,
    blogger_user,
    make_test_user,
)
