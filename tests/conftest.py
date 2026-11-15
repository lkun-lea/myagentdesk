import os
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest

from server.db import migrate


@pytest.fixture
def isolated_database():
    url = os.environ.get("MAD_TEST_DATABASE_URL")
    if not url:
        pytest.fail("Set MAD_TEST_DATABASE_URL to a dedicated PostgreSQL test database")
    schema = f"mad_test_{uuid4().hex}"
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            yield make_conninfo(url, options=f"-csearch_path={schema}")
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def database(isolated_database):
    migrate(isolated_database)
    return isolated_database
