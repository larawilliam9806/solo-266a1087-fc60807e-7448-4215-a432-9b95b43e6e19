"""SQLite 持久化：分级表版本、规则，以及换版事务。

并发约定：
- 换版在 ``BEGIN IMMEDIATE`` 事务内完成"校验版号 → 符号化核对 → 写入新版"，
  因此核对期间分级查询读到的仍是已生效（已提交）的表；
- 所见版号在发布时仍有效才会换表（乐观并发控制）；
- 核对不通过或任何失败都会回滚，继续沿用已生效的表。
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple, Union

from .verify import Interval, Rule, Violation, find_violation

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    current_version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tables (
    version INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TABLE IF NOT EXISTS rules (
    version INTEGER NOT NULL REFERENCES tables(version),
    ordinal INTEGER NOT NULL,
    t_min INTEGER NOT NULL,
    t_max INTEGER NOT NULL,
    s_min INTEGER NOT NULL,
    s_max INTEGER NOT NULL,
    grade TEXT NOT NULL,
    PRIMARY KEY (version, ordinal)
);
"""


class AlreadyRegistered(Exception):
    """首版已存在，不允许重复登记。"""


@dataclass(frozen=True)
class SwapOk:
    version: int


@dataclass(frozen=True)
class SwapStale:
    """发布时所见版号已失效。"""

    current_version: int


@dataclass(frozen=True)
class SwapRejected:
    """核对未通过，新表已退回。"""

    violation: Violation


@dataclass(frozen=True)
class NoTable:
    """尚未登记任何分级表。"""


SwapResult = Union[SwapOk, SwapStale, SwapRejected, NoTable]


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str) -> None:
    parent = os.path.dirname(os.path.abspath(db_path))
    os.makedirs(parent, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()


def _row_to_rule(row: sqlite3.Row) -> Rule:
    return Rule(
        thickness=Interval(row["t_min"], row["t_max"]),
        strength=Interval(row["s_min"], row["s_max"]),
        grade=row["grade"],
    )


def fetch_current(conn: sqlite3.Connection) -> Optional[Tuple[int, List[Rule]]]:
    """现行版号及其规则（按判定顺序）。未登记时返回 None。"""
    row = conn.execute("SELECT current_version FROM meta WHERE id = 1").fetchone()
    if row is None:
        return None
    version = row["current_version"]
    rules = [
        _row_to_rule(r)
        for r in conn.execute(
            "SELECT * FROM rules WHERE version = ? ORDER BY ordinal", (version,)
        )
    ]
    return version, rules


def fetch_version(conn: sqlite3.Connection, version: int) -> Optional[List[Rule]]:
    """指定版本的规则；版本不存在时返回 None。"""
    exists = conn.execute(
        "SELECT 1 FROM tables WHERE version = ?", (version,)
    ).fetchone()
    if exists is None:
        return None
    return [
        _row_to_rule(r)
        for r in conn.execute(
            "SELECT * FROM rules WHERE version = ? ORDER BY ordinal", (version,)
        )
    ]


def _insert_table(conn: sqlite3.Connection, version: int, rules: Sequence[Rule]) -> None:
    conn.execute("INSERT INTO tables (version) VALUES (?)", (version,))
    conn.executemany(
        "INSERT INTO rules (version, ordinal, t_min, t_max, s_min, s_max, grade)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                version,
                i,
                r.thickness.lo,
                r.thickness.hi,
                r.strength.lo,
                r.strength.hi,
                r.grade,
            )
            for i, r in enumerate(rules)
        ],
    )


def register_initial(db_path: str, rules: Sequence[Rule]) -> int:
    """首版直接登记，返回版号 1；已存在时抛 AlreadyRegistered。"""
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("SELECT 1 FROM meta WHERE id = 1").fetchone():
                raise AlreadyRegistered
            _insert_table(conn, 1, rules)
            conn.execute("INSERT INTO meta (id, current_version) VALUES (1, 1)")
            conn.execute("COMMIT")
            return 1
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def apply_swap(
    db_path: str,
    seen_version: int,
    rules: Sequence[Rule],
    allowed_thickness: Interval,
    allowed_strength: Interval,
) -> SwapResult:
    """换版：版号校验 + 符号化核对 + 发布，同一事务内完成。"""
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            current = fetch_current(conn)
            if current is None:
                result: SwapResult = NoTable()
            elif seen_version != current[0]:
                result = SwapStale(current_version=current[0])
            else:
                cur_version, old_rules = current
                violation = find_violation(
                    old_rules, rules, allowed_thickness, allowed_strength
                )
                if violation is not None:
                    result = SwapRejected(violation=violation)
                else:
                    _insert_table(conn, cur_version + 1, rules)
                    conn.execute(
                        "UPDATE meta SET current_version = ? WHERE id = 1",
                        (cur_version + 1,),
                    )
                    result = SwapOk(version=cur_version + 1)
            conn.execute("COMMIT")
            return result
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def grade_reading(
    db_path: str, thickness: int, strength: int
) -> Optional[Tuple[int, Optional[str]]]:
    """分级查询：返回 (所用版号, 等级)；等级为 None 表示未分级。

    未登记任何分级表时返回 None。首条命中规则由 ORDER BY ordinal 保证。
    """
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT current_version FROM meta WHERE id = 1").fetchone()
        if row is None:
            return None
        version = row["current_version"]
        hit = conn.execute(
            "SELECT grade FROM rules"
            " WHERE version = ? AND t_min <= ? AND t_max >= ?"
            "   AND s_min <= ? AND s_max >= ?"
            " ORDER BY ordinal LIMIT 1",
            (version, thickness, thickness, strength, strength),
        ).fetchone()
        return version, (hit["grade"] if hit else None)
    finally:
        conn.close()
