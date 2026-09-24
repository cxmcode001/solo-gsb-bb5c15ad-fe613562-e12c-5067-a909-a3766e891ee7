# Ensemble Beat Grid API

合奏节拍网格服务：提交一小节的**目标时长**和**至少两个声部**（每个声部是依次排列的
发声/休止事件，时长为正有理数字符串），服务用**精确分数**校验各声部总时长是否等于
目标；通过后保存小节，并可按 ID 查询由所有事件边界并集切分出的**半开时间段网格**。

技术栈：Python 3.12 · FastAPI · SQLite（标准库 `sqlite3`）· Docker / Compose

## 启动（一条命令）

```bash
docker compose up --build
```

该命令完成依赖安装、SQLite 数据库初始化（应用启动时自动建表）并启动服务。

- API 地址： http://localhost:8000
- 交互式文档（Swagger UI）： http://localhost:8000/docs

## 配置

| 环境变量         | 默认值          | 说明                     |
| ---------------- | --------------- | ------------------------ |
| `DATABASE_PATH`  | `/data/app.db`（容器内）/ `./app.db`（本地） | SQLite 数据库文件路径 |
| （端口映射）      | `8000:8000`     | 在 `docker-compose.yml` 中修改 |

Compose 中数据库文件挂载在命名卷 `beat-grid-data` 上，容器重启数据不丢失。

## API

### `POST /measures` — 提交小节

```bash
curl -s -X POST http://localhost:8000/measures \
  -H 'Content-Type: application/json' \
  -d '{
    "target_duration": "4/4",
    "voices": [
      {"name": "melody", "events": [
        {"kind": "sound", "duration": "1/2"},
        {"kind": "rest",  "duration": "1/4"},
        {"kind": "sound", "duration": "1/4"}
      ]},
      {"name": "bass", "events": [
        {"kind": "rest",  "duration": "1/4"},
        {"kind": "sound", "duration": "3/4"}
      ]}
    ]
  }'
```

- 时长格式：正有理数字符串，如 `"2"`、`"1/4"`、`"3/8"`（拒绝 `0`、负数、小数、
  分母为 0）。所有时长以 `fractions.Fraction` 精确累加，无浮点误差。
- 任一一声部总时长 ≠ 目标时长时返回 **422**，逐声部给出 `expected` / `actual` /
  `difference`（= actual − expected，负值表示不足），且**不保存任何数据**：

```json
{
  "detail": {
    "message": "voice durations do not add up to the target duration; measure was not saved",
    "mismatches": [
      {"voice": "melody", "expected": "1", "actual": "3/4", "difference": "-1/4"}
    ]
  }
}
```

- 全部匹配时返回 **201** 及保存的小节（含每个事件的 ID `v{声部序号}e{事件序号}`、
  规范化的起止偏移）。

### `GET /measures/{id}` — 查询小节

返回小节、各声部及全部事件（种类、时长、起止偏移）。

### `GET /measures/{id}/grid` — 查询节拍网格

对所有事件的起止点取并集，按相邻点生成**半开时间段** `[start, end)`；每段列出各声部
正在发声的事件 ID，休止为 `null`。半开区间保证相邻事件边界处既不丢音也不重复计音：

```json
{
  "measure_id": 1,
  "target_duration": "1",
  "voices": ["melody", "bass"],
  "segments": [
    {"start": "0",   "end": "1/4", "events": {"melody": "v1e1", "bass": null}},
    {"start": "1/4", "end": "1/2", "events": {"melody": "v1e1", "bass": "v2e2"}},
    {"start": "1/2", "end": "3/4", "events": {"melody": null,   "bass": "v2e2"}},
    {"start": "3/4", "end": "1",   "events": {"melody": "v1e3", "bass": "v2e2"}}
  ]
}
```

## 本地开发与测试

```bash
pip install -r requirements-dev.txt
uvicorn app.main:app --reload          # 本地启动（默认 ./app.db）
pytest                                 # 全部测试（纯逻辑 + API）
python3 -m unittest discover -s tests -p 'test_grid.py'   # 仅核心逻辑（零依赖）
```

## 项目结构

```
app/
  grid.py      # 核心逻辑：有理数解析、精确累加、边界并集切分网格（纯标准库）
  db.py        # SQLite 连接、建表、读写（纯标准库）
  schemas.py   # Pydantic 请求/响应模型与校验
  main.py      # FastAPI 应用与路由（启动时初始化数据库）
tests/         # 核心逻辑测试（stdlib 可跑）+ API 测试（TestClient）
Dockerfile
docker-compose.yml
```
