"""API 端到端测试。"""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def rule(t0, t1, s0, s1, g):
    return {
        "thickness": {"min": t0, "max": t1},
        "strength": {"min": s0, "max": s1},
        "grade": g,
    }


RULES_V1 = [rule(0, 100, 0, 5000, "优"), rule(101, 200, 0, 5000, "良")]


@pytest.fixture()
def client(tmp_path):
    app = create_app(str(tmp_path / "grading.db"))
    with TestClient(app) as c:
        yield c


def register(client, rules=RULES_V1):
    return client.post("/tables", json={"rules": rules})


def swap(client, seen_version, rules, allowed_t, allowed_s):
    return client.post(
        "/tables/swap",
        json={
            "seen_version": seen_version,
            "rules": rules,
            "allowed_thickness": {"min": allowed_t[0], "max": allowed_t[1]},
            "allowed_strength": {"min": allowed_s[0], "max": allowed_s[1]},
        },
    )


def grade(client, t, s):
    return client.get("/grade", params={"thickness": t, "strength": s})


# ---------- 首版登记 ----------

def test_grade_before_any_table(client):
    r = grade(client, 10, 10)
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "no_table"


def test_register_first_version(client):
    r = register(client)
    assert r.status_code == 201
    assert r.json() == {"version": 1}


def test_register_twice_rejected(client):
    assert register(client).status_code == 201
    r = register(client)
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "already_registered"


def test_register_empty_table_allowed(client):
    assert register(client, []).status_code == 201
    body = grade(client, 0, 0).json()
    assert body["grade"] is None and body["graded"] is False


# ---------- 分级查询 ----------

def test_grade_first_match_wins(client):
    register(client, [rule(0, 100, 0, 100, "X"), rule(50, 150, 0, 100, "Y")])
    assert grade(client, 75, 50).json()["grade"] == "X"
    assert grade(client, 125, 50).json()["grade"] == "Y"


def test_grade_returns_version_and_grade(client):
    register(client)
    body = grade(client, 50, 3000).json()
    assert body["version"] == 1
    assert body["grade"] == "优"
    assert body["graded"] is True
    assert body["thickness"] == 50 and body["strength"] == 3000


def test_grade_unmatched_is_ungraded(client):
    register(client)
    body = grade(client, 201, 3000).json()
    assert body["version"] == 1
    assert body["grade"] is None
    assert body["graded"] is False


def test_grade_param_validation(client):
    register(client)
    assert grade(client, -1, 0).status_code == 422
    assert grade(client, 0, -5).status_code == 422
    assert client.get("/grade", params={"thickness": 1.5, "strength": 0}).status_code == 422
    assert client.get("/grade", params={"thickness": "abc", "strength": 0}).status_code == 422


# ---------- 请求校验 ----------

def test_rule_interval_must_be_ordered(client):
    assert register(client, [rule(10, 5, 0, 1, "A")]).status_code == 422
    assert register(client, [rule(0, 1, 100, 50, "A")]).status_code == 422


def test_rule_bounds_must_be_non_negative(client):
    assert register(client, [rule(-1, 5, 0, 1, "A")]).status_code == 422
    assert register(client, [rule(0, 5, 0, -1, "A")]).status_code == 422


def test_at_most_40_rules(client):
    assert register(client, [rule(0, 1, 0, 1, "A")] * 40).status_code == 201
    r = register(client, [rule(0, 1, 0, 1, "A")] * 41)
    assert r.status_code == 422


def test_grade_must_be_non_empty(client):
    assert register(client, [rule(0, 1, 0, 1, "")]).status_code == 422


# ---------- 换版 ----------

def test_swap_happy_path(client):
    register(client)
    new_rules = [rule(0, 100, 0, 5000, "特优"), rule(101, 200, 0, 5000, "良")]
    r = swap(client, 1, new_rules, (0, 100), (0, 5000))
    assert r.status_code == 200
    assert r.json() == {"version": 2}
    body = grade(client, 50, 10).json()
    assert body["version"] == 2 and body["grade"] == "特优"
    # 许可区间外的判级保持不变
    assert grade(client, 150, 10).json()["grade"] == "良"


def test_swap_without_initial_table(client):
    r = swap(client, 1, RULES_V1, (0, 100), (0, 5000))
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "no_table"


def test_swap_stale_version_rejected(client):
    register(client)
    r = swap(client, 7, RULES_V1, (0, 100), (0, 5000))
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["error"] == "version_conflict"
    assert detail["current_version"] == 1
    # 已生效的表不变
    assert grade(client, 50, 10).json()["version"] == 1


def test_swap_violation_returns_reading_and_rolls_back(client):
    register(client)
    new_rules = [rule(0, 100, 0, 5000, "优"), rule(101, 200, 0, 5000, "不合格")]
    r = swap(client, 1, new_rules, (0, 100), (0, 5000))
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "verification_failed"
    # 反例读数在许可区间之外，且新旧等级如实返回
    assert detail["reading"]["thickness"] >= 101
    assert detail["old_grade"] == "良"
    assert detail["new_grade"] == "不合格"
    # 失败时继续用已生效的表
    body = grade(client, 150, 10).json()
    assert body["version"] == 1 and body["grade"] == "良"


def test_swap_ungraded_to_graded_outside_allowed(client):
    register(client, [rule(0, 100, 0, 100, "A")])
    new_rules = [rule(0, 100, 0, 100, "A"), rule(500, 600, 0, 100, "B")]
    r = swap(client, 1, new_rules, (0, 100), (0, 100))
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["old_grade"] is None  # 旧表未分级
    assert detail["new_grade"] == "B"
    assert detail["reading"]["thickness"] >= 500


def test_swap_versions_increment(client):
    register(client)
    assert swap(client, 1, RULES_V1, (0, 0), (0, 0)).json()["version"] == 2
    assert swap(client, 2, RULES_V1, (0, 0), (0, 0)).json()["version"] == 3
    # 旧版号随即失效
    assert swap(client, 2, RULES_V1, (0, 0), (0, 0)).status_code == 409


def test_swap_wide_intervals_not_enumerated(client):
    """许可区间宽到 10^15 量级也瞬间完成（符号化核对，非逐点枚举）。"""
    big = 10**15
    register(client, [rule(0, big, 0, big, "A")])
    # 改判全部落在许可矩形内 → 通过
    r = swap(client, 1, [rule(0, big, 0, big, "B")], (0, big), (0, big))
    assert r.status_code == 200
    # 许可厚度上限减 1 → 厚度为 big 的那一条带判级不同，必须报出
    r = swap(client, 2, [rule(0, big, 0, big, "C")], (0, big - 1), (0, big))
    assert r.status_code == 422
    assert r.json()["detail"]["reading"]["thickness"] == big


# ---------- 版本查询 ----------

def test_current_and_history(client):
    register(client)
    cur = client.get("/tables/current").json()
    assert cur["version"] == 1 and len(cur["rules"]) == 2

    new_rules = [rule(0, 100, 0, 5000, "特优"), rule(101, 200, 0, 5000, "良")]
    swap(client, 1, new_rules, (0, 100), (0, 5000))

    cur = client.get("/tables/current").json()
    assert cur["version"] == 2 and cur["rules"][0]["grade"] == "特优"

    hist = client.get("/tables/1").json()
    assert hist["version"] == 1 and hist["rules"][0]["grade"] == "优"

    assert client.get("/tables/99").status_code == 404


def test_current_table_when_empty(client):
    r = client.get("/tables/current")
    assert r.status_code == 404
