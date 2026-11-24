"""规则覆盖分析：未分级区域与被遮蔽规则。

与换版核对（verify.py）共用同一套符号化平面划分：把该版所有规则区间端点
投影到厚度轴、强度轴上作为切分点，两个轴的基本区间做笛卡尔积，把非负整数
平面划成有限个格子。任一规则矩形的边都落在切分点上，因此每个格子对任一
规则只有"整体被覆盖 / 整体不相交"两种关系，格子内首条命中规则恒定。

- 未覆盖区域：所有未命中格子按"相邻厚度行上相同的强度段"纵向合并成互不
  重叠的矩形；基本区间最后一段延伸到 +∞，因此没有上限的区域自然算全，
  全程只与格子打交道，不逐点枚举。
- 遮蔽规则：规则 i 的矩形内每个格子都被某条序号更小的规则完整覆盖，则
  规则 i 永远轮不到首条命中；其矩形左下角必落在某个被前序规则覆盖的格子
  内，即为一组被前序规则先命中的代表读数。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

# 同包复用 verify 的格子划分原语，保证两处"平面划分"语义完全一致
from .verify import Interval, Rule, _covers, _elementary_cells

# 基本区间：(起点, 终点]，终点为 None 表示延伸到 +∞（同 verify._Cell）
_Cell = Tuple[int, Optional[int]]


@dataclass(frozen=True)
class Region:
    """一块未覆盖矩形区域；t_hi / s_hi 为 None 表示该方向延伸到 +∞。"""

    t_lo: int
    t_hi: Optional[int]
    s_lo: int
    s_hi: Optional[int]


@dataclass(frozen=True)
class ShadowedRule:
    """一条被前序规则完全遮蔽的规则。

    (thickness, strength) 为代表读数（矩形左下角），该点首条命中的规则
    序号为 shadowed_by（必小于 ordinal）。
    """

    ordinal: int
    thickness: int
    strength: int
    shadowed_by: int


@dataclass(frozen=True)
class CoverageReport:
    """覆盖分析结果：互不重叠的未覆盖矩形 + 被前序规则完全遮蔽的规则。"""

    uncovered: List[Region]
    shadowed_rules: List[ShadowedRule]


def _cell_span(interval: Interval, cells: Sequence[_Cell]) -> Tuple[int, int]:
    """规则区间完整覆盖的基本区间下标范围 [start, end]。

    区间端点都是切分点，覆盖的基本区间必为连续段且非空。
    """
    start: Optional[int] = None
    end = -1
    for idx, cell in enumerate(cells):
        if _covers(interval, cell):
            if start is None:
                start = idx
            end = idx
    if start is None:
        raise RuntimeError("规则区间未对齐基本区间划分")
    return start, end


def _merge_uncovered(
    first: Sequence[Sequence[Optional[int]]],
    t_cells: Sequence[_Cell],
    s_cells: Sequence[_Cell],
) -> List[Region]:
    """把未覆盖格子合并成互不重叠的矩形。

    按厚度行扫描：每行求出未覆盖格子的极大连续强度段；某段在下一行不再
    整体出现时即关闭对应矩形，否则纵向延伸。每个未覆盖格子恰属于一个矩形。
    """
    regions: List[Region] = []
    active: Dict[Tuple[int, int], int] = {}  # 强度段 (s0, s1) -> 起始厚度行下标

    def close(seg: Tuple[int, int], t_start: int, t_end: int) -> None:
        s0, s1 = seg
        regions.append(
            Region(
                t_lo=t_cells[t_start][0],
                t_hi=t_cells[t_end][1],
                s_lo=s_cells[s0][0],
                s_hi=s_cells[s1][1],
            )
        )

    for ti, row in enumerate(first):
        segments: List[Tuple[int, int]] = []
        sj = 0
        while sj < len(s_cells):
            if row[sj] is not None:
                sj += 1
                continue
            s0 = sj
            while sj < len(s_cells) and row[sj] is None:
                sj += 1
            segments.append((s0, sj - 1))
        current = set(segments)
        for seg in list(active):
            if seg not in current:
                close(seg, active.pop(seg), ti - 1)
        for seg in segments:
            if seg not in active:
                active[seg] = ti
    for seg, t_start in active.items():
        close(seg, t_start, len(t_cells) - 1)

    regions.sort(key=lambda r: (r.t_lo, r.s_lo))
    return regions


def analyze_coverage(rules: Sequence[Rule]) -> CoverageReport:
    """分析一版分级表：返回全部未覆盖区域（不重叠矩形）与被遮蔽规则。

    格子数量只取决于规则区间端点个数（每轴至多 2×规则数 个切分点），
    与区间宽度无关，坐标再大也不退化成逐点枚举。
    """
    t_points: List[int] = []
    s_points: List[int] = []
    for r in rules:
        t_points.append(r.thickness.lo)
        t_points.append(r.thickness.hi + 1)
        s_points.append(r.strength.lo)
        s_points.append(r.strength.hi + 1)
    t_cells = _elementary_cells(t_points)
    s_cells = _elementary_cells(s_points)

    # first[ti][sj] = 格子 (ti, sj) 内首条命中规则的序号；None 表示未覆盖
    first: List[List[Optional[int]]] = [
        [None] * len(s_cells) for _ in range(len(t_cells))
    ]
    spans: List[Tuple[int, int, int, int]] = []
    for ordinal, r in enumerate(rules):
        t0, t1 = _cell_span(r.thickness, t_cells)
        s0, s1 = _cell_span(r.strength, s_cells)
        spans.append((t0, t1, s0, s1))
        for ti in range(t0, t1 + 1):
            row = first[ti]
            for sj in range(s0, s1 + 1):
                if row[sj] is None:
                    row[sj] = ordinal

    shadowed: List[ShadowedRule] = []
    for ordinal, (t0, t1, s0, s1) in enumerate(spans):
        # 格子首条命中序号最小者即覆盖者：规则 i 被遮蔽
        # ⟺ 其矩形内每个格子的首条命中序号都小于 i
        hit = first[t0][s0]
        if hit is None or hit >= ordinal:
            continue
        if all(
            first[ti][sj] is not None and first[ti][sj] < ordinal
            for ti in range(t0, t1 + 1)
            for sj in range(s0, s1 + 1)
        ):
            r = rules[ordinal]
            shadowed.append(
                ShadowedRule(
                    ordinal=ordinal,
                    thickness=r.thickness.lo,
                    strength=r.strength.lo,
                    shadowed_by=hit,
                )
            )

    return CoverageReport(
        uncovered=_merge_uncovered(first, t_cells, s_cells),
        shadowed_rules=shadowed,
    )
