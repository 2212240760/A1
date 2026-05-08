# 网格数据评估 Web 系统（MVP）设计文档

日期：2026-05-08  
范围：MVP（首个可用版本）  

## 1. 背景与目标

我们需要一个面向“网格数据”的 Web 系统，支持以 Excel 作为主要输入，通过评估与对比快速产出可交付结果，并能沉淀历史报告。

MVP 目标：
- Excel 上传导入为“数据集版本”
- 基于规则集进行指标评估，产出结果与汇总
- 版本 A/B 对比，产出差异结果
- 以“对比驱动”的总览看板作为主入口
- 支持 Excel 与 PDF 两种导出
- 支持简单登录与管理员/普通用户角色
- 默认对比对象按用户记忆（记住上次选择的 A/B）

非目标（MVP 不做）：
- 复杂数据权限（按数据集细粒度授权）
- 行级明细全量入库与复杂交互式二次分析
- 企业 SSO 对接

## 2. 关键产品决策（已确认）

- 入口：看板优先（A）→ 对比驱动（A3）
- 默认对比：D3（按用户记忆上次 A/B）
- 数据输入：Excel
- 登录与角色：简单登录 + 管理员/普通用户
- 结果策略：结果仅文件化（评估/对比行级明细不强制入库）
- 导出：Excel + PDF 都做
  - Excel：原始明细 + 汇总（多 sheet）
  - PDF：HTML → PDF（模板渲染后生成 PDF）

## 3. 信息架构（IA）与页面

顶部导航（登录后）：
- 总览（对比）
- 数据集
- 评估任务
- 对比
- 报告

页面清单（MVP）：
- 登录：账号/密码登录
- 总览（对比）：默认展示 A/B 差异（D3）+ KPI + 差异明细摘要 + 快捷操作（上传/评估/对比/导出）
- 数据集列表：数据集管理、上传 Excel 创建新版本
- 数据集详情：版本列表、字段/行数概览、最近任务与报告
- 评估任务：任务列表、状态、错误信息、结果摘要与导出入口
- 对比：选择版本 A/B，生成对比结果并可保存为报告
- 报告中心：历史报告列表、下载（Excel/PDF）

主流程：
1. 首次进入总览：无数据时引导上传 → 进入数据集页上传 Excel
2. 上传完成：生成新版本 → 发起评估任务
3. 评估完成：产出指标结果（文件化）→ 总览展示 KPI/摘要
4. 发起对比：选择 A/B 版本 → 生成差异结果（文件化）
5. 总览默认对比：登录后按用户偏好恢复上次 A/B（D3）
6. 导出：Excel（明细+汇总）与 PDF（HTML→PDF）均可下载

## 4. 架构与技术路线（建议）

### 4.1 形态
- 单体服务：后端 API + 服务端渲染页面（SSR）/轻交互
- 任务处理：评估/对比使用后台任务队列（同进程 worker 或独立进程）

### 4.2 技术栈（建议，最终以仓库依赖为准）
- Web：FastAPI
- 页面：Jinja2 + HTMX（或等价轻前端方案）
- 元数据存储：SQLite（MVP 部署简单）
- 文件存储：本地磁盘（/data 或 ./data 目录）
- Excel 解析：Python 数据处理栈（xlsx 解析依赖 openpyxl 等）
- PDF：HTML 模板渲染后转 PDF（例如基于 headless 浏览器或 HTML→PDF 工具链）

## 5. 数据模型（元数据）

说明：评估/对比行级明细以文件化方式保存；数据库主要保存元数据、任务状态、摘要信息、导出文件索引。

实体（MVP）：
- User：id、username、password_hash、role（admin/user）、created_at、last_login_at
- Dataset：id、name、description、owner_user_id、created_at
- DatasetVersion：id、dataset_id、version_label、source_file_path、sheet_name、schema_json、row_count、created_at、created_by
- IngestJob：id、dataset_version_id、status、error_message、log_path、started_at、finished_at
- RuleSet：id、name、version、definition_json、created_by、created_at、is_active
- EvalJob：id、dataset_version_id、ruleset_id、status、summary_json、result_files_json、started_at、finished_at、created_by
- CompareJob：id、dataset_id、version_a_id、version_b_id、status、summary_json、result_files_json、started_at、finished_at、created_by
- Report：id、dataset_id、type（eval/compare）、ref_job_id、title、export_files_json、created_at、created_by
- UserPreference：id、user_id、dataset_id、last_compare_version_a_id、last_compare_version_b_id、updated_at

文件化结果建议目录结构：
- data/
  - uploads/{dataset_id}/{version_id}/source.xlsx
  - jobs/eval/{eval_job_id}/
    - summary.json
    - details.xlsx
    - details.csv（可选）
    - report.html（可选）
    - report.pdf
  - jobs/compare/{compare_job_id}/
    - summary.json
    - diff.xlsx
    - diff.csv（可选）
    - report.html（可选）
    - report.pdf

## 6. 任务与计算

### 6.1 导入（IngestJob）
输入：上传的 Excel 文件、可选 sheet、字段映射（如需要）  
输出：
- DatasetVersion 元数据（schema_json、row_count 等）
- 原始文件落盘
- 导入日志（失败原因可追溯）

### 6.2 评估（EvalJob）
输入：DatasetVersion + RuleSet  
输出：
- summary_json：KPI、TopN 异常、关键维度聚合
- result_files_json：导出文件列表（Excel/PDF/HTML）

### 6.3 对比（CompareJob）
输入：DatasetVersion A + DatasetVersion B  
输出：
- summary_json：新增/减少/变化分布、TopN 变化项
- result_files_json：对比明细 Excel、PDF 等

### 6.4 D3（按用户记忆）
- 登录后读取 UserPreference 作为默认 A/B
- 用户在总览/对比页切换 A/B 后写回 UserPreference

## 7. 导出与报告

### 7.1 Excel（明细 + 汇总，多 sheet）
建议至少包含：
- Sheet 1：汇总（KPI、分布、TopN）
- Sheet 2：明细（评估异常明细或对比差异明细）

### 7.2 PDF（HTML → PDF）
- 用统一的报告 HTML 模板渲染（包含标题、版本信息、KPI、图表、表格摘要）
- 同时保存 report.html（便于排查与快速预览），再生成 report.pdf

## 8. 权限与安全

角色：
- admin：管理 RuleSet、用户管理（MVP 可简化为仅创建/禁用用户）
- user：上传/发起任务/查看结果/导出

安全基线：
- 密码仅保存哈希
- 上传文件白名单/大小限制
- 任务日志/错误信息对用户展示时避免泄露服务器路径与敏感信息

## 9. 可观测与错误处理（MVP）
- 每个任务（导入/评估/对比）有 status：pending/running/succeeded/failed
- failed 需保留 error_message（用户可读）与 log_path（运维可读）
- 页面提供“重试”入口（可选）

## 10. 测试与验收（MVP）

验收场景：
- 登录：管理员/普通用户均可登录
- 导入：上传 Excel → 生成数据集版本 → 可查看版本信息
- 评估：选规则集运行 → 任务成功 → 可下载 Excel/PDF
- 对比：选 A/B 运行 → 任务成功 → 总览按 D3 记忆默认 A/B
- 报告中心：可查询历史报告并下载

