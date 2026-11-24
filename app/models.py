"""请求 / 响应模型。"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

# SQLite INTEGER 为 64 位有符号整数，坐标上限据此约束
MAX_COORD = 2**63 - 1

MAX_RULES = 40  # 每版最多 40 条规则


class IntervalIn(BaseModel):
    """非负整数闭区间。"""

    min: int = Field(ge=0, le=MAX_COORD, description="下界（含）")
    max: int = Field(ge=0, le=MAX_COORD, description="上界（含）")

    @model_validator(mode="after")
    def _ordered(self) -> "IntervalIn":
        if self.min > self.max:
            raise ValueError("区间下界不能大于上界")
        return self


class RuleIn(BaseModel):
    """一条分级规则：两个闭区间 + 一个等级。"""

    thickness: IntervalIn = Field(description="厚度闭区间（微米）")
    strength: IntervalIn = Field(description="抗折强度闭区间（千帕）")
    grade: str = Field(min_length=1, max_length=64, description="命中时判定的等级")


class TableIn(BaseModel):
    """首版登记请求。"""

    rules: List[RuleIn] = Field(default_factory=list, max_length=MAX_RULES)


class SwapIn(BaseModel):
    """换版请求：所见版号 + 新表 + 允许改判的厚度、强度闭区间。"""

    seen_version: int = Field(ge=1, description="客户端所见的现行版号")
    rules: List[RuleIn] = Field(max_length=MAX_RULES, description="新分级表")
    allowed_thickness: IntervalIn = Field(description="允许改判的厚度闭区间（微米）")
    allowed_strength: IntervalIn = Field(description="允许改判的强度闭区间（千帕）")


class VersionOut(BaseModel):
    version: int


class TableOut(BaseModel):
    version: int
    rules: List[RuleIn]


class GradeOut(BaseModel):
    """分级查询结果：所用版号 + 等级（grade 为 null 表示未分级）。"""

    version: int
    thickness: int
    strength: int
    grade: Optional[str]
    graded: bool
