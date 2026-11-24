"""换版核对：判断新表是否在许可改判区间之外与现行表判级一致。

读数定义域为 (厚度, 抗折强度) ∈ 非负整数 × 非负整数，上无界；
核对必须覆盖整个定义域（包括尚未收到的读数），且不允许逐点枚举。

算法（符号化平面划分）：
1. 把新旧两版所有规则区间的端点 lo、hi+1，以及许可区间端点，
   分别投影到厚度轴、强度轴上作为切分点；
2. 切分点把每个轴切成有限个"基本区间"（最后一段延伸到 +∞）；
3. 两个轴的基本区间做笛卡尔积，把定义域划成有限个格子；
4. 任一规则矩形、许可矩形的边都落在切分点上，因此每个格子对任一规则
   只有"整体被覆盖 / 整体不相交"两种关系，格子内新旧两版的判级各自恒定；
5. 逐格比较：格子只要有一点在许可矩形之外（厚度越界或强度越界），
   新旧判级就必须相同，否则返回该格的代表读数作为反例。

格子数量只取决于区间端点个数（每版至多 40 条规则，每轴至多
2×(40+40+1) 个切分点），与区间宽度无关，因此许可区间再宽也不会
退化成逐点枚举。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

Grade = Optional[str]  # None 表示"未分级"


@dataclass(frozen=True)
class Interval:
    """非负整数闭区间 [lo, hi]。"""

    lo: int
    hi: int

    def __post_init__(self) -> None:
        if self.lo < 0:
            raise ValueError("区间下界必须为非负整数")
        if self.hi < self.lo:
            raise ValueError("区间上界不能小于下界")


@dataclass(frozen=True)
class Rule:
    """一条分级规则：厚度闭区间、强度闭区间、等级。"""

    thickness: Interval
    strength: Interval
    grade: str


@dataclass(frozen=True)
class Violation:
    """一处许可区间外的改判：读数及新旧等级（None 表示未分级）。"""

    thickness: int
    strength: int
    old_grade: Grade
    new_grade: Grade


def grade_of(rules: Sequence[Rule], thickness: int, strength: int) -> Grade:
    """单点判级：取首条命中的等级，未命中为 None（未分级）。"""
    for r in rules:
        if (
            r.thickness.lo <= thickness <= r.thickness.hi
            and r.strength.lo <= strength <= r.strength.hi
        ):
            return r.grade
    return None


# 基本区间：(起点, 终点]，终点为 None 表示延伸到 +∞
_Cell = Tuple[int, Optional[int]]


def _elementary_cells(split_points: Sequence[int]) -> list[_Cell]:
    """按切分点把 [0, +∞) 切成基本区间，每段都至少含一个整数点。"""
    cuts = sorted({p for p in split_points if p > 0})
    cells: list[_Cell] = []
    lo = 0
    for p in cuts:
        cells.append((lo, p - 1))
        lo = p
    cells.append((lo, None))
    return cells


def _covers(interval: Interval, cell: _Cell) -> bool:
    """规则区间是否完整覆盖某个基本区间（端点已对齐，等价于与该格相交）。"""
    lo, hi = cell
    return interval.lo <= lo and hi is not None and interval.hi >= hi


def _cell_grade(rules: Sequence[Rule], t_cell: _Cell, s_cell: _Cell) -> Grade:
    """格子内恒定的判级：首条完整覆盖该格的规则的等级。"""
    for r in rules:
        if _covers(r.thickness, t_cell) and _covers(r.strength, s_cell):
            return r.grade
    return None


def _within(cell: _Cell, allowed: Interval) -> bool:
    """格子是否整体落在许可区间内（端点已对齐，只会全在或全不在）。"""
    lo, hi = cell
    return allowed.lo <= lo and hi is not None and hi <= allowed.hi


def find_violation(
    old_rules: Sequence[Rule],
    new_rules: Sequence[Rule],
    allowed_thickness: Interval,
    allowed_strength: Interval,
) -> Optional[Violation]:
    """核对新表：许可矩形（厚度许可 × 强度许可）之外，两版判级必须处处一致。

    若存在一组读数，其厚度或强度超出各自许可区间且两版判级不同，
    返回该组读数及新旧等级；否则返回 None。
    """
    t_points: list[int] = [allowed_thickness.lo, allowed_thickness.hi + 1]
    s_points: list[int] = [allowed_strength.lo, allowed_strength.hi + 1]
    for r in (*old_rules, *new_rules):
        t_points.append(r.thickness.lo)
        t_points.append(r.thickness.hi + 1)
        s_points.append(r.strength.lo)
        s_points.append(r.strength.hi + 1)

    t_cells = _elementary_cells(t_points)
    s_cells = _elementary_cells(s_points)

    for t_cell in t_cells:
        t_free = _within(t_cell, allowed_thickness)
        for s_cell in s_cells:
            # 厚度、强度都在各自许可区间内时才允许改判
            if t_free and _within(s_cell, allowed_strength):
                continue
            old_grade = _cell_grade(old_rules, t_cell, s_cell)
            new_grade = _cell_grade(new_rules, t_cell, s_cell)
            if old_grade != new_grade:
                return Violation(
                    thickness=t_cell[0],
                    strength=s_cell[0],
                    old_grade=old_grade,
                    new_grade=new_grade,
                )
    return None
