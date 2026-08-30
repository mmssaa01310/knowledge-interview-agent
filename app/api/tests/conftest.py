import os

# Unit/contract tests use the deterministic test double. PostgreSQL is covered
# by the opt-in repository integration test using TEST_DATABASE_URL.
os.environ["DATABASE_URL"] = "memory://test"
