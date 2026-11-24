"""符号化换版核对的单元测试。"""

import random

from app.verify import Interval, Rule, find_violation, grade_of


def R(t0, t1, s0, s1, g):
    return Rule(Interval(t0, t1), Interval(s0, s1), g)


def assert_witness_valid(v, old, new, allowed_t, allowed_s):
    """反例必须满足：在许可矩形之外，且新旧判级确实不同。"""
    inside = (
        allowed_t.lo <= v.thickness <= allowed_t.hi
        and allowed_s.lo <= v.strength <= allowed_s.hi
    )
    assert not inside
    assert grade_of(old, v.thickness, v.strength) == v.old_grade
    assert grade_of(new, v.thickness, v.strength) == v.new_grade
    assert v.old_grade != v.new_grade


def test_identical_tables_pass():
    rules = [R(0, 100, 0, 5000, "A"), R(101, 200, 0, 5000, "B")]
    assert find_violation(rules, rules, Interval(0, 0), Interval(0, 0)) is None


def test_change_inside_allowed_passes():
    old = [R(0, 100, 0, 5000, "A"), R(101, 200, 0, 5000, "B")]
    new = [R(0, 100, 0, 5000, "C"), R(101, 200, 0, 5000, "B")]
    assert find_violation(old, new, Interval(0, 100), Interval(0, 5000)) is None


def test_change_outside_thickness_fails():
    old = [R(0, 100, 0, 5000, "A"), R(101, 200, 0, 5000, "B")]
    new = [R(0, 100, 0, 5000, "A"), R(101, 200, 0, 5000, "C")]
    v = find_violation(old, new, Interval(0, 100), Interval(0, 5000))
    assert v is not None
    assert v.thickness >= 101
    assert (v.old_grade, v.new_grade) == ("B", "C")
    assert_witness_valid(v, old, new, Interval(0, 100), Interval(0, 5000))


def test_change_outside_strength_fails():
    old = [R(0, 50, 0, 5000, "A")]
    new = [R(0, 50, 0, 3999, "A"), R(0, 50, 4000, 5000, "B")]
    v = find_violation(old, new, Interval(0, 50), Interval(0, 1000))
    assert v is not None
    assert v.strength >= 4000
    assert (v.old_grade, v.new_grade) == ("A", "B")
    assert_witness_valid(v, old, new, Interval(0, 50), Interval(0, 1000))


def test_unbounded_region_checked_without_enumeration():
    """10^15 量级的坐标不可能逐点枚举，符号化核对必须照样发现。"""
    big = 10**15
    old = [R(0, big, 0, big, "A")]
    new = [R(0, 10**12 - 1, 0, big, "A"), R(10**12, big, 0, big, "B")]
    v = find_violation(old, new, Interval(0, 10**6), Interval(0, big))
    assert v is not None
    assert v.thickness == 10**12
    assert (v.old_grade, v.new_grade) == ("A", "B")


def test_first_match_ordering_matters():
    old = [R(0, 10, 0, 10, "A"), R(0, 10, 0, 10, "B")]  # 永远判 A
    new = [R(0, 10, 0, 10, "B"), R(0, 10, 0, 10, "A")]  # 永远判 B
    v = find_violation(old, new, Interval(5, 6), Interval(5, 6))
    assert v is not None
    assert (v.old_grade, v.new_grade) == ("A", "B")
    # 许可矩形覆盖全部差异时则通过
    assert find_violation(old, new, Interval(0, 10), Interval(0, 10)) is None


def test_graded_vs_ungraded_detected():
    old = []
    new = [R(100, 200, 0, 50, "A")]
    v = find_violation(old, new, Interval(0, 99), Interval(0, 50))
    assert v is not None
    assert v.old_grade is None and v.new_grade == "A"
    assert v.thickness == 100


def test_ungraded_tail_never_violates():
    """两版都覆盖不到的区域（含 +∞ 尾部）恒为未分级，不算改判。"""
    old = [R(0, 10, 0, 10, "A")]
    new = [R(0, 10, 0, 10, "A"), R(20, 30, 20, 30, "B")]
    # 新增规则完全落在许可矩形内
    assert find_violation(old, new, Interval(15, 35), Interval(15, 35)) is None
    # 许可区间盖不住它则必须报出
    v = find_violation(old, new, Interval(0, 10), Interval(0, 10))
    assert v is not None
    assert (v.old_grade, v.new_grade) == (None, "B")


def test_allowed_boundary_is_exact():
    old = [R(0, 100, 0, 100, "A")]
    new = [R(0, 49, 0, 100, "A"), R(50, 100, 0, 100, "B")]
    # 差异在厚度 [50,100]，许可给到 50 即可通过
    assert find_violation(old, new, Interval(50, 100), Interval(0, 100)) is None
    # 许可只到 49 就必须报出
    v = find_violation(old, new, Interval(0, 49), Interval(0, 100))
    assert v is not None and v.thickness == 50


def test_full_tables_stress():
    """两版各 40 条规则、坐标稀疏分布，核对结果应与预期一致。"""
    old = [R(i * 1000, i * 1000 + 999, 0, 10**9, f"G{i}") for i in range(40)]
    new_same = list(old)
    assert find_violation(old, new_same, Interval(0, 0), Interval(0, 0)) is None

    # 只改第 20 条且许可区间恰好覆盖它
    new_ok = list(old)
    new_ok[20] = R(20 * 1000, 20 * 1000 + 999, 0, 10**9, "GX")
    assert (
        find_violation(old, new_ok, Interval(20 * 1000, 20 * 1000 + 999), Interval(0, 10**9))
        is None
    )
    # 许可区间盖不住则报出
    v = find_violation(old, new_ok, Interval(0, 20 * 1000 - 1), Interval(0, 10**9))
    assert v is not None
    assert (v.old_grade, v.new_grade) == ("G20", "GX")


def test_fuzz_against_brute_force():
    """随机小坐标场景下，与逐点暴力枚举的结果对照（含反例合法性）。"""
    rng = random.Random(20261124)

    def rand_interval():
        a = rng.randint(0, 35)
        return Interval(a, a + rng.randint(0, 6))

    def rand_table():
        return [
            Rule(rand_interval(), rand_interval(), f"G{rng.randint(0, 3)}")
            for _ in range(rng.randint(0, 6))
        ]

    for _ in range(80):
        old, new = rand_table(), rand_table()
        allowed_t, allowed_s = rand_interval(), rand_interval()
        v = find_violation(old, new, allowed_t, allowed_s)

        # 坐标上限 41，[0,50)² 之外两版都未分级，暴力枚举该框即等价于全定义域
        brute = None
        for t in range(50):
            for s in range(50):
                if allowed_t.lo <= t <= allowed_t.hi and allowed_s.lo <= s <= allowed_s.hi:
                    continue
                go, gn = grade_of(old, t, s), grade_of(new, t, s)
                if go != gn:
                    brute = (t, s, go, gn)
                    break
            if brute:
                break

        assert (v is None) == (brute is None)
        if v is not None:
            assert_witness_valid(v, old, new, allowed_t, allowed_s)
