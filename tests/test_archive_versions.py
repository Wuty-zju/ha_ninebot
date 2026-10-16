"""Actual SQLite transaction/connection fences without persistent schema changes."""

import sqlite3

import pytest

from custom_components.ninebot.archive_versions import catalog_version, install


@pytest.fixture
def database():
    db = sqlite3.connect(":memory:")
    db.executescript(
        "CREATE TABLE rides(vehicle,id,data,started,ended,observed);"
        "CREATE TABLE months(vehicle,month,data,ids,backend,observed);"
        "CREATE TABLE membership(vehicle,month,ride,latest);"
        "CREATE TABLE details(vehicle,ride,latest_hash);"
        "CREATE TABLE progress(vehicle,value);"
    )
    install(db)
    yield db
    db.close()


def test_noop_receipts_other_vehicles_and_progress_are_not_catalog_changes(database):
    db = database
    with db:
        db.execute("INSERT INTO rides VALUES ('a','id','data','1','2','0')")
    before = catalog_version(db, "a")
    with db:
        db.execute("UPDATE rides SET observed='1' WHERE vehicle='a'")
        db.execute("INSERT INTO rides VALUES ('b','other','data','1','2','0')")
        db.execute("INSERT INTO progress VALUES ('a','budget_wait')")
    assert catalog_version(db, "a") == before
    with db:
        db.execute("UPDATE rides SET data='new' WHERE vehicle='a'")
    assert catalog_version(db, "a") != before


def test_membership_changes_details_presence_and_rollbacks_are_fenced(database):
    db = database
    before = catalog_version(db, "a")
    with pytest.raises(RuntimeError), db:
        db.execute("INSERT INTO membership VALUES ('a','month','ride',1)")
        assert catalog_version(db, "a") != before
        raise RuntimeError
    assert catalog_version(db, "a") == before
    for sql in (
        "INSERT INTO membership VALUES ('a','month','ride',1)",
        "UPDATE membership SET latest=0 WHERE vehicle='a'",
        "INSERT INTO details VALUES ('a','ride','hash')",
        "DELETE FROM details WHERE vehicle='a'",
        "DELETE FROM membership WHERE vehicle='a'",
    ):
        with db:
            db.execute(sql)
        after = catalog_version(db, "a")
        assert after != before
        before = after


def test_epochs_are_connection_local_and_do_not_change_saved_schema(tmp_path):
    path = tmp_path / "versions.sqlite3"
    db = sqlite3.connect(path)
    for statement in (
        "CREATE TABLE rides(vehicle,id,data,started,ended,observed)",
        "CREATE TABLE months(vehicle,month,data,ids,backend,observed)",
        "CREATE TABLE membership(vehicle,month,ride,latest)",
        "CREATE TABLE details(vehicle,ride,latest_hash)",
    ):
        db.execute(statement)
    db.commit()
    original = path.read_bytes()
    install(db)
    first = catalog_version(db, "a")
    assert path.read_bytes() == original
    assert not db.execute(
        "SELECT name FROM main.sqlite_master WHERE name LIKE 'ninebot%'"
    ).fetchall()
    db.close()
    db = sqlite3.connect(path)
    install(db)
    assert catalog_version(db, "a") != first
    assert path.read_bytes() == original
    db.close()
