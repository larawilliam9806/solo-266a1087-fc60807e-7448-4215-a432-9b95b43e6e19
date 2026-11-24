"""规则覆盖分析（未覆盖区域 + 遮蔽规则）的算法单测与 API 端到端测试。"""

import random

import pytest
from fastapi.testclient import TestClient

from app.coverage import Region, analyze_coverage
from app.main import create_app
from app.verify import Interval, Rule, grade_of


def R(t0, t1, s0, s1, g="A"):
    return Rule(Interval(t0, t1), Interval(s0, s1), g)


def rule_json(t0, t1, s0, s1, g):
    return {
        "thickness": {"min": t0, "max": t1},
        "strength": {"min": s0, "max": s1},
        "grade": g,
    }


def as_tuples(regions):
    return [(r.t_lo, r.t_hi, r.s_lo, r.s_hi) for r in regions]


# ---------- 算法：未覆盖区域 ----------


def test_empty_table_whole_plane_uncovered():
    report = analyze_coverage([])
    assert report.uncovered == [Region(t_lo=0, t_hi=None, s_lo=0, s_hi=None)]
    assert report.shadowed_rules == []


def test_single_rule_uncovered_frame():
    report = analyze_coverage([R(10, 20, 100, 200)])
    # 左条带、右无界条带、下方条带、上方无界条带，互不重叠
    assert as_tuples(report.uncovered) == [
        (0, 9, 0, None),
        (10, 20, 0, 99),
        (10, 20, 201, None),
        (21, None, 0, None),
    ]


def test_adjacent_rules_no_fake_gap():
    """相邻边界 [0,100] 与 [101,200] 之间不能出现假的未覆盖缝。"""
    report = analyze_coverage([R(0, 100, 0, 5000), R(101, 200, 0, 5000)])
    assert as_tuples(report.uncovered) == [
        (0, 200, 5001, None),
        (201, None, 0, None),
    ]


def test_overlapping_rules_uncovered_exact():
    """重叠规则的未覆盖区域不多算不少算，矩形互不重叠。"""
    report = analyze_coverage([R(0, 100, 0, 100), R(50, 150, 50, 150)])
    assert as_tuples(report.uncovered) == [
        (0, 49, 101, None),
        (50, 150, 151, None),
        (101, 150, 0, 49),
        (151, None, 0, None),
    ]


def test_unbounded_regions_have_no_upper_bound():
    """没有上限的区域必须算全：max 为 None 表示延伸到 +∞。"""
    report = analyze_coverage([R(0, 10**15, 0, 10**15)])
    tails = [r for r in report.uncovered if r.t_hi is None or r.s_hi is None]
    assert tails  # 规则有界，平面无界，必然存在无界未覆盖区域
    assert (10**15 + 1, None, 0, None) in as_tuples(report.uncovered)


# ---------- 算法：遮蔽规则 ----------


def test_identical_later_rule_shadowed():
    report = analyze_coverage([R(0, 100, 0, 100, "A"), R(0, 100, 0, 100, "B")])
    assert len(report.shadowed_rules) == 1
    s = report.shadowed_rules[0]
    assert s.ordinal == 1
    assert s.shadowed_by == 0
    assert (s.thickness, s.strength) == (0, 0)


def test_joint_shadowing_by_multiple_priors():
    """单条前序规则盖不住、多条联合起来完全遮蔽，也算遮蔽。"""
    report = analyze_coverage(
        [
            R(0, 100, 0, 100, "A"),
            R(0, 100, 101, 200, "B"),
            R(0, 100, 0, 200, "C"),
        ]
    )
    assert [s.ordinal for s in report.shadowed_rules] == [2]
    s = report.shadowed_rules[0]
    assert s.shadowed_by == 0


def test_partial_overlap_not_shadowed():
    report = analyze_coverage([R(0, 100, 0, 100, "A"), R(50, 150, 0, 100, "B")])
    assert report.shadowed_rules == []


def test_shadow_witness_hit_by_prior_rule():
    rules = [R(0, 100, 0, 100, "A"), R(10, 50, 10, 50, "B")]
    report = analyze_coverage(rules)
    (s,) = report.shadowed_rules
    # 代表读数在被遮蔽规则矩形内，且首条命中的是前序规则
    assert 10 <= s.thickness <= 50 and 10 <= s.strength <= 50
    assert grade_of(rules, s.thickness, s.strength) == "A"


# ---------- 算法：与暴力枚举对照的模糊测试 ----------


def test_fuzz_against_brute_force():
    rng = random.Random(20261124)

    def rand_interval():
        a = rng.randint(0, 30)
        return Interval(a, a + rng.randint(0, 8))

    def inside(r, t, s):
        t_ok = r.t_lo <= t and (r.t_hi is None or t <= r.t_hi)
        s_ok = r.s_lo <= s and (r.s_hi is None or s <= r.s_hi)
        return t_ok and s_ok

    for _ in range(60):
        rules = [
            Rule(rand_interval(), rand_interval(), f"G{i}")
            for i in range(rng.randint(0, 6))
        ]
        report = analyze_coverage(rules)
        rects = report.uncovered

        # 1) 矩形两两不重叠（None 视为 +∞）
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                a, b = rects[i], rects[j]
                t_sep = (
                    (a.t_hi is not None and a.t_hi < b.t_lo)
                    or (b.t_hi is not None and b.t_hi < a.t_lo)
                )
                s_sep = (
                    (a.s_hi is not None and a.s_hi < b.s_lo)
                    or (b.s_hi is not None and b.s_hi < a.s_lo)
                )
                assert t_sep or s_sep

        # 2) 逐点对照：未分级 ⟺ 恰好落在一个未覆盖矩形内
        #    （坐标上限 38，[0,45)² 足以代表所有格子的行为）
        for t in range(45):
            for s in range(45):
                ungraded = grade_of(rules, t, s) is None
                hits = [r for r in rects if inside(r, t, s)]
                assert (len(hits) == 1) == ungraded

        # 3) 遮蔽判定与暴力一致：矩形内每个点都被某条前序规则覆盖
        shadowed_ordinals = {s.ordinal for s in report.shadowed_rules}
        for i, r in enumerate(rules):
            brute = all(
                any(
                    rules[j].thickness.lo <= t <= rules[j].thickness.hi
                    and rules[j].strength.lo <= s <= rules[j].strength.hi
                    for j in range(i)
                )
                for t in range(r.thickness.lo, r.thickness.hi + 1)
                for s in range(r.strength.lo, r.strength.hi + 1)
            )
            assert (i in shadowed_ordinals) == brute

        # 4) 代表读数合法：在被遮蔽规则矩形内，且被 shadowed_by 先命中
        for s in report.shadowed_rules:
            r = rules[s.ordinal]
            assert r.thickness.lo <= s.thickness <= r.thickness.hi
            assert r.strength.lo <= s.strength <= r.strength.hi
            assert s.shadowed_by < s.ordinal
            assert grade_of(rules, s.thickness, s.strength) == rules[s.shadowed_by].grade


# ---------- API 端到端 ----------


@pytest.fixture()
def client(tmp_path):
    app = create_app(str(tmp_path / "grading.db"))
    with TestClient(app) as c:
        yield c


def register(client, rules):
    return client.post("/tables", json={"rules": rules})


def test_coverage_unknown_version_404(client):
    assert client.get("/tables/1/coverage").status_code == 404
    register(client, [])
    r = client.get("/tables/99/coverage")
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "no_such_version"


def test_coverage_marks_requested_version(client):
    register(client, [rule_json(0, 100, 0, 5000, "优")])
    body = client.get("/tables/1/coverage").json()
    assert body["version"] == 1


def test_coverage_uncovered_regions(client):
    register(
        client,
        [rule_json(0, 100, 0, 5000, "优"), rule_json(101, 200, 0, 5000, "良")],
    )
    body = client.get("/tables/1/coverage").json()
    assert body["uncovered"] == [
        {"thickness": {"min": 0, "max": 200}, "strength": {"min": 5001, "max": None}},
        {"thickness": {"min": 201, "max": None}, "strength": {"min": 0, "max": None}},
    ]
    assert body["shadowed_rules"] == []


def test_coverage_shadowed_rules(client):
    register(
        client,
        [
            rule_json(0, 100, 0, 100, "A"),
            rule_json(0, 100, 101, 200, "B"),
            rule_json(0, 100, 0, 200, "C"),
        ],
    )
    body = client.get("/tables/1/coverage").json()
    assert body["shadowed_rules"] == [
        {"ordinal": 2, "shadowed_by": 0, "witness": {"thickness": 0, "strength": 0}}
    ]


def test_coverage_empty_table(client):
    register(client, [])
    body = client.get("/tables/1/coverage").json()
    assert body["uncovered"] == [
        {"thickness": {"min": 0, "max": None}, "strength": {"min": 0, "max": None}}
    ]
    assert body["shadowed_rules"] == []


def test_coverage_for_historical_version(client):
    register(client, [rule_json(0, 100, 0, 100, "A")])
    r = client.post(
        "/tables/swap",
        json={
            "seen_version": 1,
            "rules": [rule_json(0, 100, 0, 100, "A"), rule_json(0, 100, 0, 100, "B")],
            "allowed_thickness": {"min": 0, "max": 100},
            "allowed_strength": {"min": 0, "max": 100},
        },
    )
    assert r.status_code == 200
    v1 = client.get("/tables/1/coverage").json()
    assert v1["version"] == 1 and v1["shadowed_rules"] == []
    v2 = client.get("/tables/2/coverage").json()
    assert v2["version"] == 2
    assert [s["ordinal"] for s in v2["shadowed_rules"]] == [1]


def test_coverage_reads_committed_version_after_failed_swap(client):
    """换版核对失败回滚后，覆盖核查读到的仍是已生效的旧版，版本数据原状。"""
    register(client, [rule_json(0, 100, 0, 100, "A"), rule_json(0, 100, 0, 100, "B")])
    r = client.post(
        "/tables/swap",
        json={
            "seen_version": 1,
            "rules": [rule_json(0, 100, 0, 100, "A"), rule_json(500, 600, 0, 100, "C")],
            "allowed_thickness": {"min": 0, "max": 100},
            "allowed_strength": {"min": 0, "max": 100},
        },
    )
    assert r.status_code == 422
    body = client.get("/tables/1/coverage").json()
    assert body["version"] == 1
    assert [s["ordinal"] for s in body["shadowed_rules"]] == [1]
    assert client.get("/tables/current").json()["version"] == 1
    assert client.get("/tables/2/coverage").status_code == 404
    # 分级查询也仍按已生效表供数
    g = client.get("/grade", params={"thickness": 550, "strength": 50}).json()
    assert g["version"] == 1 and g["graded"] is False
