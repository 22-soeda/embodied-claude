"""Keep the machine's desire-system/.env out of the tests.

desire_updater calls python-dotenv's load_dotenv() when it is imported. On a
machine set up for a body (scripts/windows/configure_v0.py --with-body), .env
points DESIRE_CONFIG_FILE at that body's desires, and the tests - written for
the built-in desires - would run against those instead.
"""

import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: False
