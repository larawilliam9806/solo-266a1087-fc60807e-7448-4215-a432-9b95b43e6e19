"""FastAPI 入口：陶瓷样品分级服务。"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from typing_extensions import Annotated

from . import db
from .models import (
    MAX_COORD,
    GradeOut,
    RuleIn,
    SwapIn,
    TableIn,
    TableOut,
    VersionOut,
)
from .verify import Interval, Rule


def _to_domain(rules: List[RuleIn]) -> List[Rule]:
    return [
        Rule(
            thickness=Interval(r.thickness.min, r.thickness.max),
            strength=Interval(r.strength.min, r.strength.max),
            grade=r.grade,
        )
        for r in rules
    ]


def _to_api(rules: List[Rule]) -> List[dict]:
    return [
        {
            "thickness": {"min": r.thickness.lo, "max": r.thickness.hi},
            "strength": {"min": r.strength.lo, "max": r.strength.hi},
            "grade": r.grade,
        }
        for r in rules
    ]


def create_app(db_path: Optional[str] = None) -> FastAPI:
    path = db_path or os.environ.get("GRADING_DB_PATH", "./grading.db")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        db.init_db(path)
        yield

    app = FastAPI(
        title="陶瓷样品分级服务",
        version="1.0.0",
        description=(
            "按厚度（非负整数，微米）与抗折强度（非负整数，千帕）对陶瓷样品定级。"
            "分级表版本化；换版需交所见版号与允许改判的厚度、强度闭区间，"
            "通过符号化核对后方可发布。"
        ),
        lifespan=lifespan,
    )

    @app.get("/", include_in_schema=False)
    def root() -> dict:
        return {"service": "ceramic-grading", "docs": "/docs"}

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        return {"status": "ok"}

    @app.post(
        "/tables",
        status_code=201,
        response_model=VersionOut,
        tags=["分级表"],
        summary="登记首版分级表（仅允许一次）",
    )
    def register_table(body: TableIn) -> VersionOut:
        try:
            version = db.register_initial(path, _to_domain(body.rules))
        except db.AlreadyRegistered:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "already_registered",
                    "message": "首版已登记，后续请使用 /tables/swap 换版",
                },
            )
        return VersionOut(version=version)

    @app.get(
        "/tables/current",
        response_model=TableOut,
        tags=["分级表"],
        summary="查看现行分级表（含版号）",
    )
    def current_table() -> TableOut:
        conn = db.connect(path)
        try:
            current = db.fetch_current(conn)
        finally:
            conn.close()
        if current is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "no_table", "message": "尚未登记分级表"},
            )
        version, rules = current
        return TableOut(version=version, rules=_to_api(rules))

    @app.get(
        "/tables/{version}",
        response_model=TableOut,
        tags=["分级表"],
        summary="查看指定版本的分级表",
    )
    def table_by_version(version: int) -> TableOut:
        conn = db.connect(path)
        try:
            rules = db.fetch_version(conn, version)
        finally:
            conn.close()
        if rules is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "no_such_version", "message": f"版本 {version} 不存在"},
            )
        return TableOut(version=version, rules=_to_api(rules))

    @app.post(
        "/tables/swap",
        response_model=VersionOut,
        tags=["分级表"],
        summary="换版：交所见版号 + 新表 + 允许改判区间",
    )
    def swap_table(body: SwapIn) -> VersionOut:
        result = db.apply_swap(
            path,
            seen_version=body.seen_version,
            rules=_to_domain(body.rules),
            allowed_thickness=Interval(
                body.allowed_thickness.min, body.allowed_thickness.max
            ),
            allowed_strength=Interval(
                body.allowed_strength.min, body.allowed_strength.max
            ),
        )
        if isinstance(result, db.NoTable):
            raise HTTPException(
                status_code=404,
                detail={"error": "no_table", "message": "尚未登记首版，请直接 POST /tables"},
            )
        if isinstance(result, db.SwapStale):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "version_conflict",
                    "message": "所见版号已失效，请重新获取现行版号后再提交",
                    "current_version": result.current_version,
                },
            )
        if isinstance(result, db.SwapRejected):
            v = result.violation
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "verification_failed",
                    "message": "新表在许可改判区间之外与现行表判级不一致，已退回",
                    "reading": {"thickness": v.thickness, "strength": v.strength},
                    "old_grade": v.old_grade,
                    "new_grade": v.new_grade,
                },
            )
        return VersionOut(version=result.version)

    @app.get(
        "/grade",
        response_model=GradeOut,
        tags=["分级"],
        summary="分级查询：返回所用版号和等级（未命中为未分级）",
    )
    def grade(
        thickness: Annotated[
            int, Query(ge=0, le=MAX_COORD, description="厚度（微米，非负整数）")
        ],
        strength: Annotated[
            int, Query(ge=0, le=MAX_COORD, description="抗折强度（千帕，非负整数）")
        ],
    ) -> GradeOut:
        result = db.grade_reading(path, thickness, strength)
        if result is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "no_table", "message": "尚未登记分级表"},
            )
        version, g = result
        return GradeOut(
            version=version,
            thickness=thickness,
            strength=strength,
            grade=g,
            graded=g is not None,
        )

    return app


app = create_app()
