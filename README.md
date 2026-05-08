# 网格数据评估系统（grid-eval-web）

一个基于 FastAPI + Jinja2 的本地 Web 应用：上传 Excel 数据集、运行规则评估、对比版本差异，并导出结果报告。

## 安装

```bash
python -m pip install -U pip
pip install -e .
```

## 启动

```bash
uvicorn app.main:app --reload
```

启动后访问：<http://127.0.0.1:8000/>

## 默认管理员账号

首次启动会自动创建默认管理员（如不存在）：
- 用户名：admin
- 密码：admin

可通过环境变量覆盖：`DEFAULT_ADMIN_USERNAME` / `DEFAULT_ADMIN_PASSWORD`。

## 核心入口

- `/`：仪表盘（对比入口、最近一次成功对比摘要/下载）
- `/datasets`：数据集与版本管理（上传 Excel）
- `/rulesets`：规则集管理（管理员）
- `/eval-jobs`：评估任务（对某个数据集版本运行规则评估）
- `/compare`：对比任务（选择同一数据集的两个版本对比）
- `/reports`：报告与导出下载

## 使用流程（上传 → 评估 → 对比 → 导出）

1. 进入 `/datasets` 上传 Excel，创建数据集与版本（Upload 后会生成一次导入任务）。
2. 进入 `/eval-jobs`，选择一个数据集版本创建评估任务，等待完成并查看结果/下载产物。
3. 进入 `/compare`，选择同一数据集的两个版本创建对比任务，查看差异摘要并下载结果文件。
4. 进入 `/reports` 查看历史报告，并下载导出文件。
