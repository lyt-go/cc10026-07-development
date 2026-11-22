# 数据服务开放与订阅管理平台

面向数据API注册、查询路由、订阅审批、用量计量与SLA保障的企业数据服务开放平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
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
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| API注册 | `api_registry` | 数据API | API编号、API名称、接口路径 |
| 查询路由 | `query_route` | 路由规则 | 规则编号、关联API、路由条件 |
| 订阅管理 | `subscription` | 订阅申请 | 申请编号、订阅API、申请方 |
| 频率限制 | `rate_limiter` | 限流策略 | 策略编号、目标API、调用方 |
| 用量计量 | `usage_billing` | 用量记录 | 记录编号、调用方、API名称 |
| 缓存分层 | `cache_layer` | 缓存策略 | 策略编号、关联API、缓存层级 |
| 数据过滤 | `data_filter` | 过滤规则 | 规则编号、关联API、过滤字段 |
| 字段脱敏 | `field_mask` | 脱敏规则 | 规则编号、目标字段、脱敏算法 |
| SLA监控 | `sla_monitor` | SLA指标 | 指标编号、关联API、可用率目标 |
| 结果格式化 | `result_format` | 格式模板 | 模板编号、关联API、输出格式 |
| 查询优化 | `query_optimize` | 优化规则 | 规则编号、关联API、索引建议 |
| 异常归类 | `error_classify` | 异常类型 | 类型编号、类型名称、错误码段 |
| 数据归并 | `data_merge` | 归并规则 | 规则编号、主API、从API |
| 导出任务 | `export_task` | 导出任务 | 任务编号、关联API、查询参数 |
| 调用审计 | `call_audit` | 调用记录 | 记录编号、调用方、目标API |
| 版本兼容 | `version_compat` | 版本映射 | 映射编号、关联API、旧版本 |
| 批量查询 | `batch_query` | 批量任务 | 任务编号、关联API、查询清单 |
| SDK配置 | `sdk_config` | SDK配置 | 配置编号、关联API、SDK语言 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 过期导出的归档下架与归并取数

导出任务登记的「过期时间」不再只是字段，而会驱动一套可执行的归档下架清册，
并贯穿数据归并的取数资格。实现集中在 `app/services/data_manager.py`、
`export_task.py`、`data_merge.py` 与 `routers/archive_merge.py`。

- **归档下架清册**：`data/archives/decommission_roster.json` 为每份导出产物
  维护一条记录，同时承载文件状态（在用/待下架/已下架）与取数资格。到期文件先变
  「待下架」，值班人执行后转「已下架」并回写产物自身状态。
- **归并取数资格**：数据归并统一按清册 `evaluate_export_eligibility` 辨认输入。
  过期后无论是否已执行下架，新归并都不能再选用该文件；未到期文件也不能提前下架。
- **在途归并可恢复**：归并任务持久化到 `data/merge_tasks/`，两侧来源的读取状态
  （待读取/读取完成/需恢复）关联实际导出产物。一侧已读、另一侧到期时任务转
  「待恢复」并写明需补哪项来源；接手人补有效来源后恢复交付，归档状态不会被误判
  为汇总成功。归并实际产物落 `data/merge_artifacts/` 并由任务关联。
- **业务时钟**：`data_manager.set_clock()` 可固定当前时间，便于复现到期前后场景。

### 专用接口（固定路径，先于通用 `/api/{module}` 注册）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/archive/roster` | 生成并查看可执行的归档下架清册 |
| POST | `/api/archive/execute` | 执行整份下架清册 |
| POST | `/api/archive/exports/{id}` | 下架单份到期导出文件 |
| GET | `/api/merge/sources` | 当前具备取数资格的归并输入 |
| GET | `/api/merge/tasks` | 归并任务清单（含读取/受阻状态） |
| POST | `/api/merge/tasks` | 登记归并任务（拒绝过期来源） |
| POST | `/api/merge/tasks/{id}/start` | 读取主侧来源 |
| POST | `/api/merge/tasks/{id}/secondary` | 读取从侧并尝试交付 |
| GET | `/api/merge/tasks/{id}/delivery` | 核对能否交付、需补哪项来源 |
| POST | `/api/merge/tasks/{id}/recover` | 补来源恢复受阻任务 |

前端在「归档下架与归并取数」导航（`/archive_merge`，`ArchiveMergeView.vue`）
提供清册执行、归并登记、受阻恢复的操作台；导出任务工作台也新增「执行下架」动作。

领域：数据工程
子方向：数据分析与数据应用
