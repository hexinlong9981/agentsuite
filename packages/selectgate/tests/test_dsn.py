from selectgate.core.drivers.dsn import mysql_kwargs, parse_dsn, postgres_kwargs


def test_parse_dsn_full_url():
    c = parse_dsn("postgresql://u:secretpw@db.example.invalid:5432/appdb")
    assert c == {
        "host": "db.example.invalid",
        "port": 5432,
        "user": "u",
        "password": "secretpw",
        "dbname": "appdb",
    }


def test_parse_dsn_percent_encoded_credentials():
    c = parse_dsn("mysql://user%40corp:p%40ss@host.example.invalid/db")
    assert c["user"] == "user@corp"
    assert c["password"] == "p@ss"
    assert c["host"] == "host.example.invalid"
    assert c["dbname"] == "db"
    assert "port" not in c


def test_parse_dsn_minimal():
    c = parse_dsn("mysql://host.example.invalid")
    assert c == {"host": "host.example.invalid"}


def test_parse_dsn_non_string():
    assert parse_dsn(None) == {}  # type: ignore[arg-type]


def test_postgres_kwargs_maps_database():
    k = postgres_kwargs(parse_dsn("postgresql://u:p@h:5/d"))
    assert k == {"host": "h", "port": 5, "user": "u", "password": "p", "database": "d"}


def test_mysql_kwargs_database_keyword():
    comps = parse_dsn("mysql://u:p@h:3306/d")
    assert mysql_kwargs("pymysql", comps)["database"] == "d"
    assert mysql_kwargs("mysql.connector", comps)["database"] == "d"


def test_mysql_kwargs_mysqldb_uses_db():
    comps = parse_dsn("mysql://u:p@h/d")
    k = mysql_kwargs("MySQLdb", comps)
    assert k["db"] == "d"
    assert "database" not in k
