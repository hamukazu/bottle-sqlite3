import sqlite3

import bottle
import pytest

from bottle_sqlite3 import SQLitePlugin


class Env:
    """Test harness: a Bottle app with the plugin installed, plus a separate
    connection used to verify the database state after each request."""

    def __init__(self, dbfile):
        self.app = bottle.Bottle()
        self.app.config['catchall'] = False
        self.plugin = self.app.install(SQLitePlugin(dbfile=str(dbfile)))

        self.conn = sqlite3.connect(str(dbfile))
        self.conn.execute(
            "CREATE TABLE todo (id INTEGER PRIMARY KEY, task char(100) NOT NULL)"
        )
        self.conn.commit()

    def request(self, path, method='GET'):
        return self.app(
            {'PATH_INFO': path, 'REQUEST_METHOD': method},
            lambda status, headers, exc_info=None: None,
        )

    def insert_into(self, db):
        db.execute("INSERT INTO todo (task) VALUES ('PASS')")

    def assert_records(self, count):
        cursor = self.conn.execute("SELECT COUNT(*) FROM todo")
        assert (count,) == cursor.fetchone()


@pytest.fixture
def env(tmp_path):
    env = Env(tmp_path / 'test.sqlite')
    yield env
    env.conn.close()


def test_with_keyword(env):
    @env.app.get('/')
    def test(db):
        assert isinstance(db, sqlite3.Connection)

    env.request('/')


def test_without_keyword(env):
    @env.app.get('/')
    def test_1():
        pass

    env.request('/')

    @env.app.get('/2')
    def test_2(**kw):
        assert 'db' not in kw

    env.request('/2')


def test_install_conflicts(env):
    env.app.install(SQLitePlugin(keyword='db2'))

    @env.app.get('/')
    def test(db, db2):
        pass

    # Two plugins working with different keywords
    env.request('/')


def test_text_factory(env):
    env.app.install(SQLitePlugin(keyword='db2', text_factory=str))

    @env.app.get('/')
    def test(db, db2):
        char = 'ööö'
        db2.execute("CREATE TABLE todo (id INTEGER PRIMARY KEY, task char(100) NOT NULL)")
        db2.execute("INSERT INTO todo (id,task) VALUES ('1',:TEST)", {"TEST": char})
        count = len(db2.execute("SELECT * FROM todo").fetchall())
        assert count == 1

    env.request('/')


def test_text_factory_fail(env):
    # Python 2 relic: the default text_factory used to raise a
    # ProgrammingError on 8-bit bytestrings. On Python 3 str handles
    # unicode fine, so this just exercises the same code path.
    env.app.install(SQLitePlugin(keyword='db3', text_factory=str))

    @env.app.get('/')
    def test(db, db3):
        char = 'ööö'
        db3.execute("CREATE TABLE todo (id INTEGER PRIMARY KEY, task char(100) NOT NULL)")
        try:
            db3.execute("INSERT INTO todo (id,task) VALUES ('1',:TEST)", {"TEST": char})
        except sqlite3.ProgrammingError:
            pass

    env.request('/')


def test_user_functions(env):
    class SumSq:
        def __init__(self):
            self.result = 0

        def step(self, value):
            if value:
                self.result += value ** 2

        def finalize(self):
            return self.result

    def collate_reverse(string1, string2):
        if string1 == string2:
            return 0
        elif string1 < string2:
            return 1
        else:
            return -1

    testfunc1 = lambda: 'test'
    testfunc2 = lambda x: x + 1

    env.app.install(SQLitePlugin(
        keyword='db4',
        functions={'testfunc1': (0, testfunc1), 'testfunc2': (1, testfunc2)},
        aggregates={'sumsq': (1, SumSq)},
        collations={'reverse': collate_reverse},
    ))

    @env.app.get('/')
    def test(db, db4):
        db4.execute("CREATE TABLE todo (id INTEGER PRIMARY KEY, task char(100) NOT NULL)")
        result = db4.execute("SELECT testfunc1(), testfunc2(2)").fetchone()
        assert tuple(result) == ('test', 3)
        db4.execute("INSERT INTO todo VALUES (10, 'a')")
        db4.execute("INSERT INTO todo VALUES (11, 'a')")
        db4.execute("INSERT INTO todo VALUES (12, 'a')")
        result = db4.execute("SELECT sumsq(id) FROM todo WHERE task='a'").fetchone()
        assert tuple(result) == (365,)
        result = db4.execute("SELECT ('a' < 'b' COLLATE reverse)").fetchone()
        assert tuple(result) == (0,)

    env.request('/')


def test_raise_sqlite_integrity_error(env):
    @env.app.get('/')
    def test(db):
        # task can not be null, raise an IntegrityError
        db.execute("INSERT INTO todo (id) VALUES (1)")

    # TODO: assert HTTPError 500
    env.request('/')
    env.assert_records(0)


def test_autocommit(env):
    @env.app.get('/')
    def test(db):
        env.insert_into(db)

    env.request('/')
    env.assert_records(1)


def test_not_autocommit(env):
    @env.app.get('/', sqlite={'autocommit': False})
    def test(db):
        env.insert_into(db)

    env.request('/')
    env.assert_records(0)


def test_commit_on_redirect(env):
    @env.app.get('/')
    def test(db):
        env.insert_into(db)
        bottle.redirect('/')

    env.request('/')
    env.assert_records(1)


def test_commit_on_abort(env):
    @env.app.get('/')
    def test(db):
        env.insert_into(db)
        bottle.abort()

    env.request('/')
    env.assert_records(0)
