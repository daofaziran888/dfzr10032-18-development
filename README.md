# 多源数据采集与RPA自动化平台

面向网页抓取、API接入、增量采集、RPA流程编排、数据校验与采集监控的数据工程平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 数据源配置 | `datasource` | 数据源 | 源端编号、连接类型、接入地址 |
| 网页采集规则 | `crawler` | 采集规则 | 规则编号、目标网址、页面选择器 |
| API接入管理 | `apiclient` | API客户端 | 客户端编号、接口名称、请求方法 |
| 增量采集 | `incremental` | 增量游标 | 游标编号、来源标识、增量字段 |
| RPA任务编排 | `robot_task` | RPA任务 | 任务编号、任务名称、页面定位器 |
| 数据接入通道 | `ingestion` | 接入通道 | 通道编号、通道名称、输入格式 |
| 格式解析器 | `parser` | 解析配置 | 解析编号、输入格式、输出格式 |
| 采集结果校验 | `validator` | 校验规则 | 规则编号、校验字段、校验类型 |
| 采集去重 | `dedup` | 去重策略 | 策略编号、输入来源、去重键 |
| 采集调度 | `schedule` | 调度计划 | 计划编号、关联任务、调度周期 |
| 采集监控 | `monitor` | 监控面板 | 面板编号、采集源总数、今日采集量 |
| 采集日志 | `log_viewer` | 日志条目 | 日志编号、关联任务、日志级别 |
| 凭证管理 | `auth` | 认证凭证 | 凭证编号、凭证类型、关联数据源 |
| 采集转换管道 | `transform` | 转换节点 | 节点编号、输入来源、输出目标 |
| 数据流转编排 | `flow` | 流转管道 | 管道编号、管道名称、起始节点 |
| 数据血缘追踪 | `lineage` | 血缘关系 | 关系编号、上游节点、下游节点 |
| 采集报告 | `report` | 采集报告 | 报告编号、采集源、统计周期 |
| 共享源采集分摊 | `share_allocate` | 分摊任务/分摊表 | 任务编号、统计周期、分摊口径、交集记录标识 |

## 共享源采集分摊

在采集监控的来源登记之上，按**来源样本**和**归属时间段**计算可共享指标，
生成共享源采集分摊表，列明各团队权限内的有效分钟数与采集量。

领域事实与派生结果严格分层：

- 事实层（不可再分）：来源登记 `share_source`（物理来源标识唯一，两队都可查看的
  来源只计一个物理来源）、归属时段 `share_attribution`（允许同一时段归属多个团队，
  重合是权限边界同时覆盖，不是前后交接）、唯一原始样本 `share_source_sample`
  （样本编号全局唯一，是整体采集量的唯一事实来源）。
- 派生层：分摊表一行 = 团队 × 来源 × 十分钟观测窗口，保留**交集记录标识**与
  原始样本编号清单；团队明细与全局汇总口径不同时，重复归属靠标识识别，
  不会被转成两份实际采集。

三种分摊口径（重叠十分钟如何计由实际业务口径决定，口径只投影采集量，不改事实）：

| 口径 | 参数值 | 重叠窗口处理 | 分部门合计与全局去重 |
| --- | --- | --- | --- |
| 考核口径 | `full` | 各团队权限内全额各计，各自考核报表都体现 | 允许大于去重值，差值即重复归属 |
| 分摊口径 | `split` | 按各团队有效分钟占比拆分，余数按团队编号顺序补 | 恰好相等 |
| 主责口径 | `primary` | 只计给主责团队，非主责计 0 | 恰好相等 |

无论哪种口径，整体采集量都能按唯一原始样本编号重算。核心接口：

- `GET /api/share_allocate/allocation` 共享源采集分摊表
- `GET /api/share_allocate/department_details` 分部门来源明细
- `GET /api/share_allocate/reconciliation` 整体对账行（共享覆盖分钟、去重前后差值、按样本重算）
- `GET /api/report/shared_allocation` 采集报告：用分摊表生成分部门明细 + 整体对账行
- `POST /api/share_allocate/{id}/actions` 重新分摊（`values.caliber` 指定口径）

后端领域逻辑测试（仅依赖标准库）：

```bash
cd backend
python3 -I -m unittest discover -s tests
```

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。
