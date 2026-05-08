# 网格数据评估 Web MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现一个可登录的 Web 系统，支持 Excel 上传→版本化数据集→评估与对比任务→看板（对比驱动）→导出 Excel+PDF（HTML→PDF），并按用户记忆默认对比版本。

**Architecture:** 单体服务（FastAPI）+ 轻页面（Jinja2 + HTMX）+ SQLite 存元数据 + 文件系统存上传与结果。评估/对比以后台任务运行，产出文件化结果（Excel/PDF/JSON 摘要），页面展示摘要并提供下载。

**Tech Stack:** Python 3、FastAPI、Jinja2、HTMX、SQLite、Excel 解析库（例如 openpyxl）、PDF（HTML→PDF 工具链，优先 Playwright/Chromium 或等价方案）

---

## 0. 约定与目录结构（落地前先统一）

目标落地结构（新建为主）：
- Create: `app/__init__.py`
- Create: `app/main.py`
- Create: `app/settings.py`
- Create: `app/db.py`
- Create: `app/models.py`
- Create: `app/security.py`
- Create: `app/repositories/`（CRUD/查询）
- Create: `app/services/`（导入/评估/对比/导出）
- Create: `app/jobs/`（后台任务调度/worker）
- Create: `app/web/`（路由 + 视图）
- Create: `app/templates/`（Jinja2 页面）
- Create: `app/static/`（少量静态资源）
- Create: `data/.gitkeep`（本地数据目录占位；真实运行中写入上传与结果）
- Create: `tests/`（pytest）

数据目录约定（运行时生成，不提交）：
- `data/uploads/{dataset_id}/{version_id}/source.xlsx`
- `data/jobs/eval/{eval_job_id}/summary.json`
- `data/jobs/eval/{eval_job_id}/details.xlsx`
- `data/jobs/eval/{eval_job_id}/report.html`
- `data/jobs/eval/{eval_job_id}/report.pdf`
- `data/jobs/compare/{compare_job_id}/summary.json`
- `data/jobs/compare/{compare_job_id}/diff.xlsx`
- `data/jobs/compare/{compare_job_id}/report.html`
- `data/jobs/compare/{compare_job_id}/report.pdf`

运行命令约定：
- Web：`uvicorn app.main:app --reload`
- 测试：`pytest -q`

---

## Task 1: 初始化 Python 工程与依赖（可运行服务骨架）

**Files:**
- Create: `pyproject.toml`
- Create: `app/main.py`
- Create: `app/__init__.py`
- Create: `tests/test_health.py`

- [ ] **Step 1: 写一个最小健康检查测试（先失败）**

```python
# tests/test_health.py
from fastapi.testclient import TestClient

from app.main import app


def test_health():
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest -q`  
Expected: FAIL（ModuleNotFoundError: app 或依赖未安装/路由不存在）

- [ ] **Step 3: 写最小 FastAPI 应用使测试通过**

```python
# app/main.py
from fastapi import FastAPI

app = FastAPI()


@app.get("/healthz")
def healthz():
    return {"ok": True}
```

- [ ] **Step 4: 补齐最小依赖与安装方式**

`pyproject.toml`（示例，最终按你环境可用性调整）：

```toml
[project]
name = "grid-eval-web"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
  "fastapi",
  "uvicorn[standard]",
  "jinja2",
  "python-multipart",
  "pydantic-settings",
  "sqlalchemy",
  "passlib[bcrypt]",
  "pytest",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest -q`  
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml app/main.py app/__init__.py tests/test_health.py
git commit -m "chore: bootstrap fastapi app with health check"
```

---

## Task 2: SQLite 数据库接入与元数据模型（SQLAlchemy）

**Files:**
- Create: `app/settings.py`
- Create: `app/db.py`
- Create: `app/models.py`
- Create: `tests/test_models_smoke.py`

- [ ] **Step 1: 写一个模型烟囱测试（先失败）**

```python
# tests/test_models_smoke.py
from app.db import create_all, get_engine
from app.models import User


def test_create_tables(tmp_path):
    db_path = tmp_path / "test.db"
    engine = get_engine(f"sqlite:///{db_path}")
    create_all(engine)

    with engine.connect() as conn:
        rows = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchall()
        assert rows

    assert User.__tablename__ == "users"
```

- [ ] **Step 2: 实现 settings 与 db 工具**

```python
# app/settings.py
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/app.db"
    data_dir: str = "./data"


settings = Settings()
```

```python
# app/db.py
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def get_engine(url: str):
    return create_engine(url, future=True)


def get_session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def create_all(engine):
    from app import models  # noqa: F401

    Base.metadata.create_all(engine)
```

- [ ] **Step 3: 实现 models（先建核心表，后续补全字段）**

```python
# app/models.py
import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class UserRole(str, enum.Enum):
    admin = "admin"
    user = "user"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.user)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
```

- [ ] **Step 4: 运行测试**

Run: `pytest -q`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/settings.py app/db.py app/models.py tests/test_models_smoke.py
git commit -m "feat: add sqlite db wiring and initial user model"
```

---

## Task 3: 密码哈希与登录会话（管理员/普通用户）

**Files:**
- Create: `app/security.py`
- Create: `app/web/auth.py`
- Create: `app/templates/login.html`
- Modify: `app/main.py`
- Test: `tests/test_auth_login.py`

- [ ] **Step 1: 写登录测试（先失败）**

```python
# tests/test_auth_login.py
from fastapi.testclient import TestClient

from app.main import app


def test_login_page():
    client = TestClient(app)
    resp = client.get("/login")
    assert resp.status_code == 200


def test_login_rejects_invalid_credentials():
    client = TestClient(app)
    resp = client.post("/login", data={"username": "u", "password": "bad"})
    assert resp.status_code in (200, 401)
```

- [ ] **Step 2: 实现 password hashing + session cookie**

`app/security.py`（示例）：

```python
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)
```

登录会话（MVP 简化）：
- 使用签名 cookie 存 user_id（FastAPI session 中间件或等价实现）
- 首次启动创建一个默认 admin（来自环境变量或首次引导页）

- [ ] **Step 3: 补齐 auth 路由与模板**

实现：
- `GET /login` 返回登录页
- `POST /login` 校验用户名密码，写入 session，成功跳转 `/`
- `POST /logout` 清除 session
- `require_user()` 依赖用于保护页面

- [ ] **Step 4: 运行测试**

Run: `pytest -q`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/security.py app/web/auth.py app/templates/login.html app/main.py tests/test_auth_login.py
git commit -m "feat: add login with admin/user roles"
```

---

## Task 4: 数据集与版本（Excel 上传→DatasetVersion）

**Files:**
- Create: `app/services/ingest.py`
- Create: `app/web/datasets.py`
- Create: `app/templates/datasets.html`
- Create: `app/templates/dataset_detail.html`
- Modify: `app/models.py`
- Test: `tests/test_upload_creates_version.py`

- [ ] **Step 1: 扩展模型（Dataset / DatasetVersion / IngestJob）**

在 `app/models.py` 新增：
- Dataset（owner_user_id、name、description、created_at）
- DatasetVersion（dataset_id、version_label、source_file_path、sheet_name、schema_json、row_count、created_at、created_by）
- IngestJob（dataset_version_id、status、error_message、log_path、started_at、finished_at）

- [ ] **Step 2: 写上传测试（先失败）**

```python
# tests/test_upload_creates_version.py
import io

from fastapi.testclient import TestClient

from app.main import app


def test_upload_requires_login():
    client = TestClient(app)
    resp = client.post("/datasets/upload", files={"file": ("a.xlsx", b"x", "application/vnd.ms-excel")})
    assert resp.status_code in (302, 401, 403)


def test_upload_creates_dataset_version_after_login(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    client = TestClient(app)

    # 这里假设存在一个测试辅助：create_test_user_and_login(client)
    # 实现放在后续 Task 的 tests/conftest.py 中

    fake_xlsx = io.BytesIO(b"PK\x03\x04")  # 最小 zip 头，占位；实际应换成真实 xlsx fixture
    resp = client.post(
        "/datasets/upload",
        files={"file": ("demo.xlsx", fake_xlsx.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"dataset_name": "Demo", "version_label": "v1"},
    )
    assert resp.status_code in (200, 302)
```

- [ ] **Step 3: 实现 ingest 服务**

`app/services/ingest.py` 职责：
- 保存上传文件到 `data/uploads/.../source.xlsx`
- 解析 Excel（sheet、列名、行数），生成 `schema_json` 与 `row_count`
- 记录 IngestJob 状态

- [ ] **Step 4: 实现数据集页面与上传入口**

路由：
- `GET /datasets` 列表 + 上传表单
- `POST /datasets/upload` 处理上传
- `GET /datasets/{dataset_id}` 版本列表

- [ ] **Step 5: 测试补齐与通过**

补 `tests/conftest.py`：
- 创建 sqlite 临时库
- 创建默认 admin/user
- 测试登录 helper
- xlsx fixture（生成一个真实 xlsx，避免仅 zip 头）

- [ ] **Step 6: Commit**

```bash
git add app/models.py app/services/ingest.py app/web/datasets.py app/templates/datasets.html app/templates/dataset_detail.html tests/
git commit -m "feat: upload excel to create dataset versions"
```

---

## Task 5: 规则集管理（管理员）与规则定义格式

**Files:**
- Create: `app/services/rulesets.py`
- Create: `app/web/rulesets.py`
- Create: `app/templates/rulesets.html`
- Modify: `app/models.py`
- Test: `tests/test_ruleset_admin_only.py`

- [ ] **Step 1: 定义 RuleSet.definition_json 的最小 schema**

建议（MVP）：
- `rules`: list
  - `name`
  - `severity`（info/warn/error）
  - `expr`（简单表达式：列比较/空值/范围；MVP 可先实现几种 builtin）
  - `message_template`

- [ ] **Step 2: 写 admin-only 测试（先失败）**

```python
# tests/test_ruleset_admin_only.py
from fastapi.testclient import TestClient
from app.main import app


def test_ruleset_page_requires_admin():
    client = TestClient(app)
    resp = client.get("/rulesets")
    assert resp.status_code in (302, 401, 403)
```

- [ ] **Step 3: 扩展模型 RuleSet**

字段：name、version、definition_json、created_by、created_at、is_active

- [ ] **Step 4: 实现 ruleset 页面**

路由：
- `GET /rulesets` 列表 + 新建/激活
- `POST /rulesets` 新建（admin）
- `POST /rulesets/{id}/activate` 激活

- [ ] **Step 5: 测试通过 + Commit**

```bash
git add app/models.py app/services/rulesets.py app/web/rulesets.py app/templates/rulesets.html tests/test_ruleset_admin_only.py
git commit -m "feat: admin ruleset management"
```

---

## Task 6: 评估任务（EvalJob）——文件化结果（Excel+PDF）

**Files:**
- Create: `app/services/eval.py`
- Create: `app/web/eval_jobs.py`
- Create: `app/templates/eval_jobs.html`
- Modify: `app/models.py`
- Test: `tests/test_eval_job_lifecycle.py`

- [ ] **Step 1: 扩展模型 EvalJob**

字段：dataset_version_id、ruleset_id、status、summary_json、result_files_json、started_at、finished_at、created_by

- [ ] **Step 2: 实现“同步版”评估（先）+ 可扩展到后台任务（后）**

MVP 建议策略：
- 先实现同步评估（短任务）保证端到端可用
- 再加后台 worker（长任务）与轮询状态

评估输出（文件化）：
- `summary.json`（KPI/TopN）
- `details.xlsx`（明细 + 汇总，多 sheet）
- `report.html`（由模板渲染）
- `report.pdf`（HTML→PDF）

- [ ] **Step 3: 写生命周期测试（先失败）**

```python
# tests/test_eval_job_lifecycle.py
from fastapi.testclient import TestClient
from app.main import app


def test_eval_job_end_to_end():
    client = TestClient(app)
    # TODO: 通过 conftest 完成登录、准备 dataset_version、ruleset
    resp = client.post("/eval-jobs", data={"dataset_version_id": 1, "ruleset_id": 1})
    assert resp.status_code in (200, 302)
```

- [ ] **Step 4: 实现 eval service**

职责：
- 读取已解析的数据（或直接从 Excel 再读一次，MVP 可接受）
- 执行 ruleset（MVP builtin 规则）
- 生成导出 Excel（多 sheet）
- 渲染 report.html
- 生成 report.pdf
- 写回 EvalJob.summary_json / result_files_json

- [ ] **Step 5: 实现任务页面与下载**

路由：
- `GET /eval-jobs` 列表
- `POST /eval-jobs` 创建任务
- `GET /eval-jobs/{id}` 详情 + 下载链接

- [ ] **Step 6: 测试通过 + Commit**

```bash
git add app/models.py app/services/eval.py app/web/eval_jobs.py app/templates/eval_jobs.html tests/test_eval_job_lifecycle.py
git commit -m "feat: eval jobs with excel and pdf outputs"
```

---

## Task 7: 对比任务（CompareJob）——文件化结果（Excel+PDF）+ 用户偏好 D3

**Files:**
- Create: `app/services/compare.py`
- Create: `app/web/compare_jobs.py`
- Create: `app/templates/compare.html`
- Modify: `app/models.py`
- Test: `tests/test_compare_and_preference.py`

- [ ] **Step 1: 扩展 CompareJob 与 UserPreference 模型**

CompareJob 字段：
- dataset_id、version_a_id、version_b_id、status、summary_json、result_files_json、started_at、finished_at、created_by

UserPreference 字段：
- user_id、dataset_id、last_compare_version_a_id、last_compare_version_b_id、updated_at

- [ ] **Step 2: 实现 compare service**

职责：
- 读取版本 A/B（从 Excel 或中间数据）
- 生成差异明细 `diff.xlsx`（多 sheet：汇总 + 明细）
- 渲染 `report.html` 并生成 `report.pdf`
- 写回 summary_json/result_files_json

- [ ] **Step 3: 实现对比页（含 D3 默认值）**

路由：
- `GET /compare`：选择 dataset 后，A/B 默认读取 UserPreference（若无则空）
- `POST /compare`：创建 compare job，并写回 UserPreference（D3）

- [ ] **Step 4: 写测试（先失败→通过）**

```python
# tests/test_compare_and_preference.py
from fastapi.testclient import TestClient
from app.main import app


def test_compare_updates_user_preference():
    client = TestClient(app)
    # TODO: 登录并准备 dataset+versions
    resp = client.post("/compare", data={"dataset_id": 1, "version_a_id": 2, "version_b_id": 1})
    assert resp.status_code in (200, 302)
```

- [ ] **Step 5: Commit**

```bash
git add app/models.py app/services/compare.py app/web/compare_jobs.py app/templates/compare.html tests/test_compare_and_preference.py
git commit -m "feat: compare jobs and per-user last selection preference"
```

---

## Task 8: 总览看板（对比驱动 A3）

**Files:**
- Create: `app/web/dashboard.py`
- Create: `app/templates/dashboard.html`
- Test: `tests/test_dashboard_renders.py`

- [ ] **Step 1: 实现首页聚合数据接口（服务端渲染）**

首页展示内容（MVP）：
- 当前 dataset（可选择）
- 默认 A/B（来自 UserPreference）
- 对比摘要（若已存在最近一次 CompareJob 成功结果，可直接展示；否则引导创建）
- 快捷入口：上传/发起评估/发起对比/查看报告

- [ ] **Step 2: 写渲染测试**

```python
# tests/test_dashboard_renders.py
from fastapi.testclient import TestClient
from app.main import app


def test_dashboard_requires_login():
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code in (302, 401, 403)
```

- [ ] **Step 3: 实现路由与模板**

路由：
- `GET /`：总览（对比驱动）

模板包含：
- 版本选择器（A/B）
- 差异摘要区
- TopN 变化/异常列表
- 下载入口（如果已有结果文件）

- [ ] **Step 4: Commit**

```bash
git add app/web/dashboard.py app/templates/dashboard.html tests/test_dashboard_renders.py
git commit -m "feat: dashboard compare-first landing page"
```

---

## Task 9: 报告中心（Report）

**Files:**
- Create: `app/services/reports.py`
- Create: `app/web/reports.py`
- Create: `app/templates/reports.html`
- Modify: `app/models.py`
- Test: `tests/test_reports_list.py`

- [ ] **Step 1: 实现 Report 模型与最小 CRUD**

Report 关联：
- type（eval/compare）
- ref_job_id（指向 EvalJob/CompareJob）
- export_files_json（包含 excel/pdf 路径）

- [ ] **Step 2: 页面与测试**

路由：
- `GET /reports`：列表
- `GET /reports/{id}/download?kind=excel|pdf`

- [ ] **Step 3: Commit**

```bash
git add app/models.py app/services/reports.py app/web/reports.py app/templates/reports.html tests/test_reports_list.py
git commit -m "feat: reports center with downloads"
```

---

## Task 10: 后台任务化（可选但推荐）——从同步到异步

**Files:**
- Create: `app/jobs/queue.py`
- Create: `app/jobs/worker.py`
- Modify: `app/services/eval.py`
- Modify: `app/services/compare.py`
- Modify: `app/web/eval_jobs.py`
- Modify: `app/web/compare_jobs.py`
- Test: `tests/test_async_job_polling.py`

- [ ] **Step 1: 引入轻量队列**

选择其一（MVP 推荐最简单可控方案）：
- 基于 SQLite 的任务表 + worker 轮询
- 或 Python 标准库 `concurrent.futures`（同进程）+ 任务状态落库

- [ ] **Step 2: 页面轮询状态（HTMX）**

实现：
- 任务详情页每 2-5 秒轮询 status
- 成功后展示下载按钮

- [ ] **Step 3: 测试与 Commit**

```bash
git add app/jobs/ app/services/ app/web/ tests/test_async_job_polling.py
git commit -m "feat: background jobs for eval and compare"
```

---

## Task 11: 文档与运行说明

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 补齐本地运行步骤**

包含：
- 安装依赖
- 初始化数据库/创建默认 admin
- 启动服务
- 上传 Excel、运行评估/对比、导出

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add runbook for grid eval web mvp"
```

---

## 覆盖性自检（对照 spec）

- 登录与角色：Task 3 + Task 5（admin-only）
- Excel 上传版本化：Task 4
- 规则集：Task 5
- 评估任务与文件化输出（Excel+PDF）：Task 6
- 对比任务与文件化输出（Excel+PDF）：Task 7
- D3 用户记忆：Task 7
- A3 总览入口：Task 8
- 报告中心：Task 9
- 可观测/错误：各任务 status 字段 + 页面展示（Task 4/6/7/9）

