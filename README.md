# 陶瓷样品分级服务

陶瓷样品入质检数仓前，按**厚度**（非负整数，微米）与**抗折强度**（非负整数，千帕）定级。
FastAPI 提供分级 API，SQLite 存储版本化分级表，支持 Docker Compose 一键启动。

## 业务规则

- 分级表按版本管理，**首版直接登记**（`POST /tables`），之后只能走换版流程。
- 每版**最多 40 条**规则；每条规则 = 厚度闭区间 + 强度闭区间 + 一个等级。
- 判级时**取首条命中**规则的等级；没有命中记为**未分级**（`grade: null`）。
- 分级查询返回**所用版号**和等级。

## 换版协议

`POST /tables/swap` 需提交：

| 字段 | 含义 |
| --- | --- |
| `seen_version` | 客户端所见的现行版号 |
| `rules` | 新分级表（≤40 条） |
| `allowed_thickness` | 允许改判的厚度闭区间 |
| `allowed_strength` | 允许改判的强度闭区间 |

**核对规则**：若存在一组读数，其厚度或强度超出各自许可区间，且新旧两版判级不同，
则退回新表（HTTP 422），并返回这组读数及新旧等级。也就是说，只有
`allowed_thickness × allowed_strength` 这个矩形之内才允许改判。

**符号化核对，不逐点枚举**：读数定义域是非负整数平面、上无界，核对必须覆盖
尚未收到的读数。实现（`app/verify.py`）把两版所有规则区间端点与许可区间端点
投影到两个坐标轴上作为切分点，将平面划成有限个"基本区间格子"。每个格子与任一
规则矩形只有"整体覆盖 / 整体不相交"两种关系，格内两版判级各自恒定，逐格比较
即等价于全定义域逐点核对。格子数量只取决于端点个数（每轴 ≤ 2×81 个切分点），
与区间宽度无关——许可区间再宽也不会退化成枚举。

**并发与失败语义**：

- 换版在单个 `BEGIN IMMEDIATE` 事务内完成"校验版号 → 核对 → 发布"，
  **核对期间分级查询仍按现行（已提交）表供数**；
- **发布时所见版号仍有效才能换表**（乐观并发控制，失效返回 409）；
- 核对不通过或任何失败都会回滚，**继续沿用已生效的表**。

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/tables` | 登记首版分级表（仅一次，重复返回 409） |
| `GET` | `/tables/current` | 查看现行分级表（含版号） |
| `GET` | `/tables/{version}` | 查看历史版本 |
| `POST` | `/tables/swap` | 换版（核对通过且版号有效才发布） |
| `GET` | `/grade?thickness=&strength=` | 分级查询，返回所用版号和等级 |

交互式文档：服务启动后访问 `/docs`。

### 示例

```bash
# 登记首版
curl -X POST localhost:8000/tables -H 'Content-Type: application/json' -d '{
  "rules": [
    {"thickness": {"min": 0, "max": 100}, "strength": {"min": 0, "max": 5000}, "grade": "优"},
    {"thickness": {"min": 101, "max": 200}, "strength": {"min": 0, "max": 5000}, "grade": "良"}
  ]
}'
# → {"version": 1}

# 分级查询
curl 'localhost:8000/grade?thickness=80&strength=1200'
# → {"version":1,"thickness":80,"strength":1200,"grade":"优","graded":true}

# 换版：只允许厚度 0–100、强度 0–5000 内改判
curl -X POST localhost:8000/tables/swap -H 'Content-Type: application/json' -d '{
  "seen_version": 1,
  "rules": [
    {"thickness": {"min": 0, "max": 100}, "strength": {"min": 0, "max": 5000}, "grade": "特优"},
    {"thickness": {"min": 101, "max": 200}, "strength": {"min": 0, "max": 5000}, "grade": "良"}
  ],
  "allowed_thickness": {"min": 0, "max": 100},
  "allowed_strength": {"min": 0, "max": 5000}
}'
# → {"version": 2}
```

### 错误响应

错误体为 `{"detail": {...}}`，`detail.error` 取值：

| `error` | 状态码 | 含义 |
| --- | --- | --- |
| `no_table` | 404 | 尚未登记分级表 |
| `already_registered` | 409 | 首版已登记 |
| `version_conflict` | 409 | 所见版号已失效，附 `current_version` |
| `verification_failed` | 422 | 许可区间外存在改判，附 `reading`、`old_grade`、`new_grade`（`null` 表示未分级） |

请求体/参数不合法（负数、区间倒置、超过 40 条等）返回 FastAPI 默认的 422 校验错误。

## 启动

### Docker Compose（推荐）

```bash
docker compose up --build
```

服务监听 `8000` 端口，SQLite 数据落在命名卷 `grading-data`（容器内 `/data/grading.db`）。

### 本地运行

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

数据库路径由环境变量 `GRADING_DB_PATH` 指定，默认 `./grading.db`。

## 测试

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

测试覆盖：符号化核对算法（含与逐点暴力枚举对照的随机模糊测试、10^15 量级
坐标的无枚举核对）、首版登记、分级查询（首条命中 / 未分级 / 版号返回）、
换版全流程（成功、版号冲突、核对失败回滚、宽许可区间）。

## 项目结构

```
app/
  main.py    # FastAPI 入口与路由
  models.py  # 请求/响应模型（Pydantic 校验）
  db.py      # SQLite 持久化与换版事务
  verify.py  # 换版符号化核对（核心算法）
tests/
  test_verify.py  # 核对算法单测 + 暴力枚举对照
  test_api.py     # API 端到端测试
Dockerfile
docker-compose.yml
```
