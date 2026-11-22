"""跨模块共享数据管理器：读写 data/ 目录下的业务数据产物。

职责：
- 每个服务产出 domain_snapshot 后序列化到 data/{module}/snapshot.json
- 下游服务从 data/ 目录读取上游的标准化数据产物
- 审计记录写入 data/audit/
- 用量记录写入 data/usage/
- 导出任务结果写入 data/exports/
- API 注册快照写入 data/api/
- 订阅审批记录写入 data/subscriptions/
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# 数据目录相对于仓库根
DATA_ROOT = Path(os.environ.get("DATA_ROOT", str(
    Path(__file__).resolve().parents[3] / "data"
)))

# 东八区
TZ = timezone(timedelta(hours=8))

# 测试用时钟覆盖：set_clock 后 now() 返回固定时间，便于构造到期前后场景。
_clock_override: datetime | None = None


def set_clock(value: str | datetime | None) -> None:
    """固定当前时间，供测试模拟过期前后；传 None 恢复系统时钟。"""
    global _clock_override
    if value is None:
        _clock_override = None
    elif isinstance(value, datetime):
        _clock_override = value if value.tzinfo else value.replace(tzinfo=TZ)
    else:
        parsed = datetime.fromisoformat(value)
        _clock_override = parsed if parsed.tzinfo else parsed.replace(tzinfo=TZ)


def reset_clock() -> None:
    set_clock(None)


def now() -> datetime:
    """当前业务时间（东八区），可被 set_clock 固定。"""
    if _clock_override is not None:
        return _clock_override
    return datetime.now(TZ)


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data: Any) -> None:
    _ensure_dir(path.parent)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _now_iso() -> str:
    return now().isoformat()


def _today() -> date:
    return now().date()


def parse_expire_date(value: Any) -> date | None:
    """把登记的过期时间（YYYY-MM-DD 或带时间的 ISO 串）解析为日期。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


# ========== API 注册快照 ==========


def write_api_snapshot(api_id: int, api_data: dict[str, Any]) -> None:
    """将单个 API 注册数据写入共享快照，供路由、缓存、限流等模块消费。"""
    snapshot = _read_json(DATA_ROOT / "api" / "registry.json", {})
    key = str(api_id)
    api_data["_synced_at"] = _now_iso()
    snapshot[key] = api_data
    _write_json(DATA_ROOT / "api" / "registry.json", snapshot)


def read_api_snapshot(api_id: int | None = None) -> dict[str, Any] | dict[str, dict[str, Any]]:
    """读取 API 注册快照。返回单个或全部。"""
    snapshot = _read_json(DATA_ROOT / "api" / "registry.json", {})
    if api_id is not None:
        return snapshot.get(str(api_id), {})
    return snapshot


def remove_api_snapshot(api_id: int) -> None:
    """标记 API 为下线。"""
    snapshot = _read_json(DATA_ROOT / "api" / "registry.json", {})
    key = str(api_id)
    if key in snapshot:
        snapshot[key]["status"] = "已下线"
        snapshot[key]["_deleted_at"] = _now_iso()
        _write_json(DATA_ROOT / "api" / "registry.json", snapshot)


# ========== 订阅审批 ==========


def write_subscription(sub_id: int, sub_data: dict[str, Any]) -> None:
    """写入订阅申请记录到共享目录。"""
    record = {
        "subscription_id": sub_id,
        **sub_data,
        "_created_at": sub_data.get("_created_at", _now_iso()),
        "_updated_at": _now_iso(),
    }
    _write_json(DATA_ROOT / "subscriptions" / f"{sub_id:06d}.json", record)


def read_subscription(sub_id: int) -> dict[str, Any]:
    return _read_json(DATA_ROOT / "subscriptions" / f"{sub_id:06d}.json", {})


def list_subscriptions() -> list[dict[str, Any]]:
    """列出所有订阅记录。"""
    _ensure_dir(DATA_ROOT / "subscriptions")
    records = []
    for f in sorted(DATA_ROOT.glob("subscriptions/*.json")):
        record = _read_json(f)
        if record:
            records.append(record)
    return records


def approve_subscription(sub_id: int, approved: bool, approver: str = "system") -> dict[str, Any]:
    """审批订阅，记录审批流程。"""
    record = read_subscription(sub_id)
    if not record:
        return {}
    record["approval_status"] = "已通过" if approved else "已驳回"
    record["approver"] = approver
    record["_approved_at"] = _now_iso()
    write_subscription(sub_id, record)
    return record


# ========== 用量计费 ==========


def write_usage(usage_id: int, usage_data: dict[str, Any]) -> None:
    """写入用量记录到共享目录。"""
    usage_data["_created_at"] = usage_data.get("_created_at", _now_iso())
    usage_data["_updated_at"] = _now_iso()
    _write_json(DATA_ROOT / "usage" / f"{usage_id:06d}.json", usage_data)


def read_usage(usage_id: int) -> dict[str, Any]:
    return _read_json(DATA_ROOT / "usage" / f"{usage_id:06d}.json", {})


def list_usage(billing_cycle: str | None = None) -> list[dict[str, Any]]:
    """列出用量记录，支持按计费周期过滤。"""
    _ensure_dir(DATA_ROOT / "usage")
    records = []
    for f in sorted(DATA_ROOT.glob("usage/*.json")):
        record = _read_json(f)
        if record:
            if billing_cycle is None or record.get("billing_cycle") == billing_cycle:
                records.append(record)
    return records


def settle_billing(billing_cycle: str, use_auto_settlement: bool = True) -> dict[str, Any]:
    """结算指定计费周期，生成结算汇总。"""
    records = list_usage(billing_cycle)
    total_amount = sum(float(r.get("amount", 0)) for r in records)
    total_calls = sum(int(r.get("call_count", 0)) for r in records)
    settlement = {
        "billing_cycle": billing_cycle,
        "total_amount": total_amount,
        "total_calls": total_calls,
        "record_count": len(records),
        "settled_at": _now_iso(),
        "auto_settlement": use_auto_settlement,
    }
    _write_json(DATA_ROOT / "usage" / f"_settlement_{billing_cycle}.json", settlement)

    for r in records:
        r["settlement_status"] = "已结算"
        r["_settled_at"] = _now_iso()
        write_usage(r.get("subscription_id", 0), r)
    return settlement


# ========== 调用审计 ==========


def write_audit(audit_id: int, audit_data: dict[str, Any]) -> None:
    """写入审计记录。"""
    audit_data["_logged_at"] = _now_iso()
    _write_json(DATA_ROOT / "audit" / f"{audit_id:06d}.json", audit_data)


def read_audit(audit_id: int) -> dict[str, Any]:
    return _read_json(DATA_ROOT / "audit" / f"{audit_id:06d}.json", {})


def list_audit(api_name: str | None = None, caller: str | None = None,
               start_time: str | None = None, end_time: str | None = None) -> list[dict[str, Any]]:
    """按条件查询审计记录。"""
    _ensure_dir(DATA_ROOT / "audit")
    records = []
    for f in sorted(DATA_ROOT.glob("audit/*.json")):
        record = _read_json(f)
        if not record:
            continue
        if api_name and record.get("api_name") != api_name:
            continue
        if caller and record.get("caller") != caller:
            continue
        if start_time and record.get("_logged_at", "") < start_time:
            continue
        if end_time and record.get("_logged_at", "") > end_time:
            continue
        records.append(record)
    return records


def archive_audit_records(before_date: str) -> dict[str, Any]:
    """归档指定日期之前的审计记录。"""
    records = list_audit()
    archived = [r for r in records if r.get("_logged_at", "")[:10] < before_date]
    archive_data = {
        "archive_date": _now_iso(),
        "before_date": before_date,
        "archived_count": len(archived),
        "records": archived,
    }
    _write_json(DATA_ROOT / "audit" / f"_archive_{before_date}.json", archive_data)
    # 标记已归档
    for r in archived:
        rid = r.get("audit_id", 0)
        r["status"] = "已归档"
        write_audit(rid, r)
    return archive_data


# ========== 导出任务 ==========


def write_export(export_id: int, export_data: dict[str, Any]) -> None:
    """写入导出任务结果。"""
    export_data["_created_at"] = export_data.get("_created_at", _now_iso())
    export_data["_updated_at"] = _now_iso()
    _write_json(DATA_ROOT / "exports" / f"{export_id:06d}.json", export_data)
    # 同时生成一份导出 CSV
    _write_export_csv(export_id, export_data)


def _write_export_csv(export_id: int, export_data: dict[str, Any]) -> None:
    """根据导出数据生成 CSV 文件头。"""
    columns = export_data.get("columns", [])
    rows = export_data.get("rows", [])
    csv_path = DATA_ROOT / "exports" / f"{export_id:06d}.csv"
    lines = [",".join(columns)]
    for row in rows:
        lines.append(",".join(str(v) for v in row))
    csv_path.write_text("\n".join(lines), encoding="utf-8")


def read_export(export_id: int) -> dict[str, Any]:
    return _read_json(DATA_ROOT / "exports" / f"{export_id:06d}.json", {})


def list_exports() -> list[dict[str, Any]]:
    _ensure_dir(DATA_ROOT / "exports")
    records = []
    for f in sorted(DATA_ROOT.glob("exports/[0-9]*.json")):
        record = _read_json(f)
        if record:
            records.append(record)
    return records


# ========== 模块数据快照 ==========


def write_module_snapshot(module: str, snapshot: dict[str, Any]) -> None:
    """各服务将自己的 domain_snapshot 写入共享目录，供跨模块消费。"""
    snapshot["_snapshot_at"] = _now_iso()
    _write_json(DATA_ROOT / module / "snapshot.json", snapshot)


def read_module_snapshot(module: str) -> dict[str, Any]:
    return _read_json(DATA_ROOT / module / "snapshot.json", {})


def list_module_snapshots() -> list[dict[str, Any]]:
    """读取所有模块的快照汇总。"""
    snapshots = []
    for module_dir in DATA_ROOT.iterdir():
        if module_dir.is_dir() and not module_dir.name.startswith("_"):
            snap = _read_json(module_dir / "snapshot.json")
            if snap:
                snapshots.append(snap)
    return snapshots


# ========== 导出文件归档下架清册 ==========
#
# 过期时间此前只登记不消费。本子系统让过期登记真正驱动「下架」：
# - 每份导出产物在清册里有一条记录，同时承载文件状态与取数资格；
# - 清册是可执行的下架清单（due 项可一键执行下架），并关联受影响的归并；
# - 归并按清册辨认当前输入资格，已读取 / 待恢复的读取状态关联到产物与归并任务。

ARCHIVE_DIR = DATA_ROOT / "archives"
ROSTER_PATH = ARCHIVE_DIR / "decommission_roster.json"
MERGE_TASK_DIR = DATA_ROOT / "merge_tasks"
MERGE_ARTIFACT_DIR = DATA_ROOT / "merge_artifacts"

# 导出文件状态
FILE_STATUS_ACTIVE = "在用"          # 未到期、可供归并选用
FILE_STATUS_DUE = "待下架"           # 已到期、已列入下架清册，等待执行
FILE_STATUS_ARCHIVED = "已下架"      # 已执行下架，文件状态与取数资格同时失效

# 归并任务状态
MERGE_STATUS_PENDING = "待执行"
MERGE_STATUS_READING = "读取中"      # 已读到一侧，正准备读另一侧
MERGE_STATUS_BLOCKED = "待恢复"      # 中途来源到期受阻，需补来源后可恢复
MERGE_STATUS_DONE = "已完成"

# 单侧来源读取状态
SIDE_PENDING = "待读取"
SIDE_READ = "读取完成"
SIDE_BLOCKED = "需恢复"

_ROSTER_DEFAULTS = {
    "file_status": FILE_STATUS_ACTIVE,
    "eligible": True,
    "expired": False,
    "archive_at": None,
    "affected_merge_tasks": [],
    "first_seen_at": None,
    "last_checked_at": None,
}


# ---------- 清册读写 ----------


def _read_roster() -> dict[str, Any]:
    roster = _read_json(ROSTER_PATH, {"generated_at": None, "entries": {}})
    roster.setdefault("generated_at", None)
    roster.setdefault("entries", {})
    return roster


def _write_roster(roster: dict[str, Any]) -> None:
    _write_json(ROSTER_PATH, roster)


def _roster_entry(export_id: int, product: dict[str, Any] | None = None) -> dict[str, Any]:
    """取一条清册记录，缺失字段补默认值；product 缺失时回落到导出产物本身。"""
    roster = _read_roster()
    key = str(export_id)
    entry = dict(_ROSTER_DEFAULTS)
    entry.update(roster.get("entries", {}).get(key, {}))
    product = product if product is not None else read_export(export_id)
    entry["export_id"] = export_id
    entry["task_no"] = product.get("task_no")
    entry["api_name"] = product.get("api_name")
    entry["expire_at"] = product.get("expire_at")
    return entry


def evaluate_export_eligibility(export_id: int,
                                at: datetime | None = None) -> dict[str, Any]:
    """按归档下架清册辨认一份导出产物当前的取数资格（不改动任何状态）。

    判定优先级：已下架 > 到期（待下架）> 在用。
    新归并选源、归并读取两侧时都以本函数为准。
    """
    moment = at or now()
    product = read_export(export_id)
    if not product:
        return {"export_id": export_id, "found": False, "eligible": False,
                "file_status": FILE_STATUS_ARCHIVED,
                "expired": True, "reason": "导出产物不存在"}
    entry = _roster_entry(export_id, product)
    expire = parse_expire_date(product.get("expire_at"))
    expired = expire is not None and moment.date() > expire

    if entry["file_status"] == FILE_STATUS_ARCHIVED:
        eligible, status, reason = False, FILE_STATUS_ARCHIVED, "文件已归档下架"
    elif expired:
        eligible, status, reason = False, FILE_STATUS_DUE, "导出文件已过过期时间"
    else:
        eligible, status, reason = True, FILE_STATUS_ACTIVE, "在有效期内"
    return {
        "export_id": export_id,
        "found": True,
        "task_no": product.get("task_no"),
        "api_name": product.get("api_name"),
        "expire_at": product.get("expire_at"),
        "expired": expired,
        "file_status": status,
        "eligible": eligible,
        "reason": reason,
        "affected_merge_tasks": list(entry.get("affected_merge_tasks", [])),
    }


def sync_roster(at: datetime | None = None) -> dict[str, Any]:
    """同步下架清册：登记新产物、按到期时间刷新文件状态与取数资格。

    - 新产物先以「在用 / 可取数」入册；
    - 到期但尚未执行下架的，置「待下架 / 不可取数」，等待值班人执行；
    - 已下架项保持终态，不会被重新激活。
    """
    moment = at or now()
    roster = _read_roster()
    entries = roster.setdefault("entries", {})
    due: list[int] = []

    for product in list_exports():
        export_id = int(product["export_id"])
        key = str(export_id)
        entry = dict(_ROSTER_DEFAULTS)
        entry.update(entries.get(key, {}))
        if entry.get("first_seen_at") is None:
            entry["first_seen_at"] = _now_iso()
        entry["export_id"] = export_id
        entry["task_no"] = product.get("task_no")
        entry["api_name"] = product.get("api_name")
        entry["expire_at"] = product.get("expire_at")
        entry["last_checked_at"] = _now_iso()

        if entry["file_status"] != FILE_STATUS_ARCHIVED:
            expire = parse_expire_date(product.get("expire_at"))
            if expire is not None and moment.date() > expire:
                entry["file_status"] = FILE_STATUS_DUE
                entry["eligible"] = False
                entry["expired"] = True
                due.append(export_id)
            else:
                entry["file_status"] = FILE_STATUS_ACTIVE
                entry["eligible"] = True
                entry["expired"] = False
        entries[key] = entry

    roster["generated_at"] = _now_iso()
    _write_roster(roster)
    return roster


def build_decommission_roster(at: datetime | None = None) -> dict[str, Any]:
    """生成可执行的归档下架清册：同步状态后返回清册与待下架明细。"""
    moment = at or now()
    roster = sync_roster(moment)
    entries = roster.get("entries", {})
    due_entries = [e for e in entries.values() if e.get("file_status") == FILE_STATUS_DUE]
    archived = [e for e in entries.values() if e.get("file_status") == FILE_STATUS_ARCHIVED]
    return {
        "generated_at": roster.get("generated_at"),
        "as_of": moment.isoformat(),
        "total": len(entries),
        "due_count": len(due_entries),
        "archived_count": len(archived),
        "due_export_ids": [e["export_id"] for e in due_entries],
        "entries": sorted(entries.values(), key=lambda e: e["export_id"]),
    }


def list_roster_entries() -> list[dict[str, Any]]:
    """返回当前清册全部条目（不强制刷新）。"""
    return sorted(_read_roster().get("entries", {}).values(),
                  key=lambda e: e["export_id"])


def _block_merge_tasks_for_export(export_id: int) -> list[str]:
    """文件下架时，把仍在读取该文件的在途归并任务置为「待恢复」。

    已读取完成的一侧保留产物关联（接手人可核对旧任务还能否交付），
    受阻一侧登记为「需恢复」并写明要补哪项来源。
    """
    blocked: list[str] = []
    for task in list_merge_tasks():
        if task["status"] in (MERGE_STATUS_DONE, MERGE_STATUS_BLOCKED):
            continue
        changed = False
        for side in ("primary", "secondary"):
            src = task.get(side) or {}
            if src.get("export_id") == export_id and src.get("read_state") != SIDE_READ:
                src["read_state"] = SIDE_BLOCKED
                src["blocked_reason"] = "来源文件已到期下架，需补该项来源"
                task[side] = src
                changed = True
        if changed:
            task["status"] = MERGE_STATUS_BLOCKED
            task["deliverable"] = False
            task["blocked_sides"] = [
                side for side in ("primary", "secondary")
                if (task.get(side) or {}).get("read_state") == SIDE_BLOCKED
            ]
            task["last_event_at"] = _now_iso()
            write_merge_task(task)
            blocked.append(task["task_no"])
    return blocked


def archive_export(export_id: int, at: datetime | None = None) -> dict[str, Any]:
    """执行单份到期导出文件的归档下架。

    同时处理两件事：
    - 文件状态 -> 已下架，取数资格 -> 失效；
    - 关联仍在使用该文件的归并任务（在途任务转「待恢复」）。
    """
    moment = at or now()
    product = read_export(export_id)
    if not product:
        return {"ok": False, "export_id": export_id, "message": "导出产物不存在"}

    roster = sync_roster(moment)
    key = str(export_id)
    entry = roster["entries"].get(key)
    if entry is None:
        return {"ok": False, "export_id": export_id, "message": "清册中无该导出文件"}
    if entry["file_status"] == FILE_STATUS_ARCHIVED:
        return {"ok": True, "export_id": export_id, "message": "文件已处于下架状态",
                "affected_merge_tasks": entry.get("affected_merge_tasks", [])}

    blocked_tasks = _block_merge_tasks_for_export(export_id)

    entry["file_status"] = FILE_STATUS_ARCHIVED
    entry["eligible"] = False
    entry["expired"] = True
    entry["archive_at"] = _now_iso()
    entry["affected_merge_tasks"] = sorted(
        set(entry.get("affected_merge_tasks", [])) | set(blocked_tasks))
    roster["generated_at"] = _now_iso()
    _write_roster(roster)

    # 同步产物文件自身状态，保证从 data/exports 读取也能辨认
    product["file_status"] = FILE_STATUS_ARCHIVED
    product["eligible"] = False
    product["archived_at"] = entry["archive_at"]
    write_export(export_id, product)

    return {"ok": True, "export_id": export_id, "task_no": product.get("task_no"),
            "file_status": FILE_STATUS_ARCHIVED, "eligible": False,
            "affected_merge_tasks": entry["affected_merge_tasks"]}


def execute_decommission_roster(at: datetime | None = None) -> dict[str, Any]:
    """执行下架清册：把全部「待下架」项逐个归档下架。"""
    moment = at or now()
    plan = build_decommission_roster(moment)
    results = [archive_export(export_id, moment)
               for export_id in plan["due_export_ids"]]
    return {
        "ok": True,
        "executed_at": _now_iso(),
        "executed_count": len(results),
        "results": results,
    }


# ========== 归并任务（取数 / 受阻 / 恢复 的可恢复执行单） ==========


def _merge_task_path(task_id: int) -> Path:
    return MERGE_TASK_DIR / f"{task_id:06d}.json"


def next_merge_task_id() -> int:
    _ensure_dir(MERGE_TASK_DIR)
    return max((int(f.stem) for f in MERGE_TASK_DIR.glob("[0-9]*.json")), default=0) + 1


def write_merge_task(task: dict[str, Any]) -> None:
    task["_updated_at"] = _now_iso()
    _write_json(_merge_task_path(int(task["task_id"])), task)


def read_merge_task(task_id: int) -> dict[str, Any]:
    return _read_json(_merge_task_path(task_id), {})


def read_merge_task_by_no(task_no: str) -> dict[str, Any]:
    for task in list_merge_tasks():
        if task.get("task_no") == task_no:
            return task
    return {}


def list_merge_tasks() -> list[dict[str, Any]]:
    _ensure_dir(MERGE_TASK_DIR)
    tasks = []
    for f in sorted(MERGE_TASK_DIR.glob("[0-9]*.json")):
        task = _read_json(f)
        if task:
            tasks.append(task)
    return tasks


def list_eligible_exports(at: datetime | None = None) -> list[dict[str, Any]]:
    """当前可供新归并选用的输入：清册判定取数资格为真的导出产物。"""
    moment = at or now()
    sync_roster(moment)
    eligible = []
    for product in list_exports():
        status = evaluate_export_eligibility(int(product["export_id"]), moment)
        if status.get("eligible"):
            eligible.append(status)
    return eligible


def register_merge_task_link(task_id: int, export_id: int) -> None:
    """把归并任务登记到对应清册条目的关联归并中（在途即可见）。"""
    roster = _read_roster()
    entries = roster.setdefault("entries", {})
    key = str(export_id)
    if key not in entries:
        sync_roster()
        roster = _read_roster()
        entries = roster.setdefault("entries", {})
    entry = entries.get(key)
    if entry is None:
        return
    task = read_merge_task(task_id)
    task_no = task.get("task_no", f"MT-{task_id:06d}")
    linked = set(entry.get("affected_merge_tasks", []))
    linked.add(task_no)
    entry["affected_merge_tasks"] = sorted(linked)
    _write_roster(roster)


def load_export_rows(export_id: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """把一份导出产物的列/行还原为字典记录，供归并读取。"""
    product = read_export(export_id)
    if not product:
        return [], {}
    columns = product.get("columns", [])
    rows = [
        {col: val for col, val in zip(columns, row_vals)}
        for row_vals in product.get("rows", [])
    ]
    return rows, product


def write_merge_artifact(task_id: int, artifact: dict[str, Any]) -> None:
    """落盘归并实际产物，任务通过 artifact_path 关联到该文件。"""
    artifact["_written_at"] = _now_iso()
    _write_json(MERGE_ARTIFACT_DIR / f"{task_id:06d}.json", artifact)


def read_merge_artifact(task_id: int) -> dict[str, Any]:
    return _read_json(MERGE_ARTIFACT_DIR / f"{task_id:06d}.json", {})