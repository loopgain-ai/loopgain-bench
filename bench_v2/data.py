"""Task loading for Bench v2.

Two sources:
  - ``mock_tasks()``: a tiny in-code SQLite fixture + synthetic NL->SQL tasks
    with known gold queries. Used by the mock-mode self-test; zero network, $0.
  - ``load_bird_minidev(root)``: reads a locally-downloaded BIRD Mini-Dev
    directory (the 500-example dev subset). Download is a manual, free step
    documented in bench_v2/README.md; this loader does NOT fetch from the
    network (keeps the harness offline-by-default and reproducible).

A Task carries everything the oracle needs to score a prediction: the question,
the path to its SQLite database, and the gold SQL whose execution result is the
ground truth.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Task:
    task_id: str
    question: str
    db_path: str            # path to the SQLite file
    gold_sql: str           # reference query; its execution result is ground truth
    schema_ddl: str         # CREATE statements, fed to the model as context
    difficulty: Optional[str] = None


# --------------------------------------------------------------------------
# Mock fixture (self-test only)
# --------------------------------------------------------------------------
_MOCK_DDL = """\
CREATE TABLE employees (id INTEGER PRIMARY KEY, name TEXT, dept TEXT, salary INTEGER);
CREATE TABLE depts (dept TEXT PRIMARY KEY, location TEXT);
"""

_MOCK_ROWS = [
    (1, "Ada", "Engineering", 150000),
    (2, "Lin", "Engineering", 140000),
    (3, "Mara", "Sales", 90000),
    (4, "Ravi", "Sales", 95000),
    (5, "Tom", "Ops", 80000),
]
_MOCK_DEPTS = [("Engineering", "NYC"), ("Sales", "LA"), ("Ops", "Austin")]


def build_mock_db(path: str) -> None:
    con = sqlite3.connect(path)
    try:
        con.executescript(_MOCK_DDL)
        con.executemany("INSERT INTO employees VALUES (?,?,?,?)", _MOCK_ROWS)
        con.executemany("INSERT INTO depts VALUES (?,?)", _MOCK_DEPTS)
        con.commit()
    finally:
        con.close()


def mock_tasks(tmpdir: Optional[str] = None) -> list[Task]:
    """Three synthetic NL->SQL tasks over a tiny employees DB."""
    tmpdir = tmpdir or tempfile.mkdtemp(prefix="benchv2_mock_")
    db = os.path.join(tmpdir, "mock.db")
    if not os.path.exists(db):
        build_mock_db(db)
    return [
        Task("mock-engineers", "List the names of all employees in the Engineering department.",
             db, "SELECT name FROM employees WHERE dept = 'Engineering'", _MOCK_DDL, "simple"),
        Task("mock-topsalary", "What is the name of the highest-paid employee?",
             db, "SELECT name FROM employees ORDER BY salary DESC LIMIT 1", _MOCK_DDL, "moderate"),
        Task("mock-salesloc", "List the locations of departments that have at least one Sales employee.",
             db, "SELECT DISTINCT d.location FROM depts d JOIN employees e ON d.dept = e.dept "
                 "WHERE e.dept = 'Sales'", _MOCK_DDL, "challenging"),
    ]


# --------------------------------------------------------------------------
# BIRD Mini-Dev loader (real data; offline read of a manual download)
# --------------------------------------------------------------------------
def load_bird_minidev(root: str, limit: Optional[int] = None) -> list[Task]:
    """Load BIRD Mini-Dev tasks from a locally-downloaded directory.

    Expects the standard BIRD layout under ``root``:
      - ``mini_dev_sqlite.json`` (or ``mini_dev.json``): list of
        {db_id, question, SQL, difficulty}
      - ``dev_databases/<db_id>/<db_id>.sqlite``

    Raises FileNotFoundError with a pointer to the README if the data is not
    present — the harness never downloads silently.
    """
    import json

    manifest = None
    for name in ("mini_dev_sqlite.json", "mini_dev.json", "dev.json"):
        p = os.path.join(root, name)
        if os.path.exists(p):
            manifest = p
            break
    if manifest is None:
        raise FileNotFoundError(
            f"BIRD Mini-Dev manifest not found under {root!r}. "
            "Download it first (see bench_v2/README.md §Data). The harness does "
            "not fetch from the network."
        )
    rows = json.load(open(manifest))
    tasks: list[Task] = []
    for i, r in enumerate(rows):
        if limit is not None and i >= limit:
            break
        db_id = r["db_id"]
        db_path = os.path.join(root, "dev_databases", db_id, f"{db_id}.sqlite")
        tasks.append(
            Task(
                task_id=f"bird-{i}-{db_id}",
                question=r["question"],
                db_path=db_path,
                gold_sql=r.get("SQL") or r.get("gold_sql") or "",
                schema_ddl=_read_schema(db_path),
                difficulty=r.get("difficulty"),
            )
        )
    return tasks


def _read_schema(db_path: str) -> str:
    if not os.path.exists(db_path):
        return ""
    con = sqlite3.connect(db_path)
    try:
        cur = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL")
        return "\n".join(row[0] for row in cur.fetchall())
    finally:
        con.close()
