"""数据归并领域服务：管理多API数据源的归并、冲突处理和去重策略。

跨模块数据流：
- 消费 api_registry 快照 -> 归并源API核验
- 消费 data_filter 快照 -> 各源数据归并前先过滤
- 被 export_task 消费 -> 归并后数据导出
- 归并结果写入共享快照

归档下架联动：
- 归并输入统一按导出文件的「归档下架清册」辨认当前取数资格；
- 归并任务持久化到 data/merge_tasks/，两侧来源的读取状态
  （读取完成 / 需恢复）关联到实际导出产物与归并任务；
- 一侧已读取、另一侧到期时任务转「待恢复」，接手人补来源后可恢复，
  不会因为归档状态被误判为全部汇总成功；
- 过期后无论是否已执行下架，新归并都不能再选用该文件。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class DataMergeService(BaseModuleService):
    MODULE = "data_merge"
    ENTITY = "归并规则"
    OPERATION_TERM = "去重"
    LIST_FIELDS = ["规则编号", "主API", "从API", "归并键", "冲突策略", "去重策略", "结果排序", "规则版本"]
    REQUIRED_FIELDS = ["规则编号", "主API", "从API"]
    STATUS_ORDER = ["草稿", "待校验", "已生效", "已停用"]
    ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "停用规则": "已停用"}
    NEGATIVE_ACTIONS = ["停用规则"]

    # ---------- 归并输入资格（按归档下架清册辨认） ----------

    def list_available_sources(self) -> list[dict[str, Any]]:
        """当前可供新归并选用的导出文件（取数资格为真）。"""
        from app.services import data_manager as dm
        return dm.list_eligible_exports()

    def check_source(self, export_id: int) -> dict[str, Any]:
        """核对单个来源文件当前是否还有取数资格。"""
        from app.services import data_manager as dm
        return dm.evaluate_export_eligibility(export_id)

    # ---------- 归并任务生命周期 ----------

    def register_merge_task(self, rule_id: int, primary_export_id: int,
                            secondary_export_id: int) -> dict[str, Any]:
        """登记一笔归并任务。新归并不得选用已到期/已下架文件。"""
        from app.services import data_manager as dm

        rule = store.find(self.MODULE, rule_id)
        if rule is None:
            return {"ok": False, "message": f"归并规则 {rule_id} 不存在"}

        if int(primary_export_id) == int(secondary_export_id):
            return {"ok": False, "message": "主侧与从侧不能选用同一份导出文件"}

        primary = dm.evaluate_export_eligibility(primary_export_id)
        secondary = dm.evaluate_export_eligibility(secondary_export_id)
        blocked: list[str] = []
        if not primary.get("eligible"):
            blocked.append(f"主侧来源[{primary_export_id}]{primary.get('reason', '不可用')}")
        if not secondary.get("eligible"):
            blocked.append(f"从侧来源[{secondary_export_id}]{secondary.get('reason', '不可用')}")
        if blocked:
            return {"ok": False, "message": "来源已失去取数资格，新归并不予登记：" + "；".join(blocked)}

        task_id = dm.next_merge_task_id()
        task = {
            "task_id": task_id,
            "task_no": f"MT-{task_id:06d}",
            "rule_id": rule_id,
            "rule_no": rule.get("规则编号"),
            "status": dm.MERGE_STATUS_PENDING,
            "deliverable": False,
            "blocked_sides": [],
            "primary": self._side_payload("primary", primary, dm.SIDE_PENDING),
            "secondary": self._side_payload("secondary", secondary, dm.SIDE_PENDING),
            "artifact_path": None,
            "merged_count": 0,
            "events": [{"at": dm._now_iso(), "event": "registered"}],
        }
        dm.write_merge_task(task)
        dm.register_merge_task_link(task_id, primary_export_id)
        dm.register_merge_task_link(task_id, secondary_export_id)
        return {"ok": True, "task": task, "message": "归并任务已登记，两侧来源均在取数有效期内"}

    @staticmethod
    def _side_payload(side: str, eligibility: dict[str, Any],
                      read_state: str) -> dict[str, Any]:
        return {
            "side": side,
            "export_id": eligibility.get("export_id"),
            "task_no": eligibility.get("task_no"),
            "api_name": eligibility.get("api_name"),
            "expire_at": eligibility.get("expire_at"),
            "file_status": eligibility.get("file_status"),
            "eligible": eligibility.get("eligible"),
            "read_state": read_state,
            "blocked_reason": None,
        }

    def start_merge(self, task_id: int) -> dict[str, Any]:
        """读取主侧来源。主侧失效则任务直接进入待恢复。"""
        from app.services import data_manager as dm

        task = dm.read_merge_task(task_id)
        if not task:
            return {"ok": False, "message": f"归并任务 {task_id} 不存在"}
        if task["status"] == dm.MERGE_STATUS_DONE:
            return {"ok": False, "message": "归并任务已完成，无需重复执行"}

        primary = task["primary"]
        status = dm.evaluate_export_eligibility(primary["export_id"])
        if not status.get("eligible"):
            primary["read_state"] = dm.SIDE_BLOCKED
            primary["eligible"] = False
            primary["file_status"] = status.get("file_status")
            primary["blocked_reason"] = f"{status.get('reason')}，需补主侧来源"
            task["primary"] = primary
            task["status"] = dm.MERGE_STATUS_BLOCKED
            task["deliverable"] = False
            task["blocked_sides"] = ["primary"]
            task["events"].append({"at": dm._now_iso(),
                                   "event": "primary_blocked",
                                   "reason": status.get("reason")})
            dm.write_merge_task(task)
            return {"ok": False, "task": task,
                    "message": f"主侧来源已失去取数资格：{status.get('reason')}，请补主侧来源"}

        task["primary"] = self._side_payload("primary", status, dm.SIDE_READ)
        task["status"] = dm.MERGE_STATUS_READING
        task["events"].append({"at": dm._now_iso(), "event": "primary_read",
                               "export_id": primary["export_id"]})
        dm.write_merge_task(task)
        return {"ok": True, "task": task, "message": "主侧来源读取完成，等待读取从侧"}

    def read_secondary(self, task_id: int) -> dict[str, Any]:
        """读取从侧来源并尝试交付。从侧到期则任务转待恢复，已读主侧予以保留。"""
        from app.services import data_manager as dm

        task = dm.read_merge_task(task_id)
        if not task:
            return {"ok": False, "message": f"归并任务 {task_id} 不存在"}
        if task["status"] == dm.MERGE_STATUS_DONE:
            return {"ok": False, "message": "归并任务已完成"}
        if task["primary"].get("read_state") != dm.SIDE_READ:
            return {"ok": False, "message": "主侧来源尚未读取完成，请先读取主侧"}

        secondary = task["secondary"]
        status = dm.evaluate_export_eligibility(secondary["export_id"])
        if not status.get("eligible"):
            secondary["read_state"] = dm.SIDE_BLOCKED
            secondary["eligible"] = False
            secondary["file_status"] = status.get("file_status")
            secondary["blocked_reason"] = f"{status.get('reason')}，需补从侧来源"
            task["secondary"] = secondary
            task["status"] = dm.MERGE_STATUS_BLOCKED
            task["deliverable"] = False
            task["blocked_sides"] = ["secondary"]
            task["events"].append({"at": dm._now_iso(),
                                   "event": "secondary_blocked",
                                   "reason": status.get("reason")})
            dm.write_merge_task(task)
            return {"ok": False, "task": task,
                    "message": f"从侧来源在读取前到期：{status.get('reason')}，需补从侧来源后恢复"}

        task["secondary"] = self._side_payload("secondary", status, dm.SIDE_READ)
        dm.write_merge_task(task)
        return self._finalize(task)

    def check_delivery(self, task_id: int) -> dict[str, Any]:
        """值班人核对旧任务还能否交付，并指出受阻时需要补哪项来源。"""
        from app.services import data_manager as dm

        task = dm.read_merge_task(task_id)
        if not task:
            return {"ok": False, "message": f"归并任务 {task_id} 不存在"}
        missing = [
            {"side": side,
             "export_id": task[side].get("export_id"),
             "reason": task[side].get("blocked_reason") or "来源未读取"}
            for side in ("primary", "secondary")
            if task[side].get("read_state") != dm.SIDE_READ
        ]
        return {
            "ok": True,
            "task_no": task["task_no"],
            "status": task["status"],
            "deliverable": bool(task.get("deliverable")),
            "primary_read_state": task["primary"].get("read_state"),
            "secondary_read_state": task["secondary"].get("read_state"),
            "missing_sources": missing,
            "artifact_path": task.get("artifact_path"),
            "message": "任务可交付" if task.get("deliverable")
            else f"任务受阻，需补来源：{'、'.join(m['side'] for m in missing)}",
        }

    def recover_source(self, task_id: int, side: str,
                       replacement_export_id: int) -> dict[str, Any]:
        """接手人为受阻一侧补一份仍在有效期内的来源，并恢复归并。"""
        from app.services import data_manager as dm

        task = dm.read_merge_task(task_id)
        if not task:
            return {"ok": False, "message": f"归并任务 {task_id} 不存在"}
        if side not in ("primary", "secondary"):
            return {"ok": False, "message": "side 只能是 primary 或 secondary"}
        if task["status"] == dm.MERGE_STATUS_DONE:
            return {"ok": False, "message": "任务已完成，无需恢复"}

        status = dm.evaluate_export_eligibility(replacement_export_id)
        if not status.get("eligible"):
            return {"ok": False,
                    "message": f"替补来源[{replacement_export_id}]不可用：{status.get('reason')}"}

        other_side = "secondary" if side == "primary" else "primary"
        other = task[other_side]
        if other.get("export_id") == int(replacement_export_id):
            return {"ok": False, "message": "替补来源不能与另一侧来源重复"}

        task[side] = self._side_payload(side, status, dm.SIDE_READ)
        task["blocked_sides"] = [s for s in task.get("blocked_sides", []) if s != side]
        task["events"].append({"at": dm._now_iso(), "event": f"{side}_recovered",
                               "replacement_export_id": replacement_export_id})
        dm.write_merge_task(task)
        dm.register_merge_task_link(task_id, replacement_export_id)

        # 两侧都已读取才能恢复交付；否则继续等待另一侧
        if task["primary"]["read_state"] == dm.SIDE_READ \
                and task["secondary"]["read_state"] == dm.SIDE_READ:
            return self._finalize(task)
        task["status"] = dm.MERGE_STATUS_READING
        dm.write_merge_task(task)
        return {"ok": True, "task": task, "message": f"{side}侧来源已补齐，等待另一侧读取"}

    def _finalize(self, task: dict[str, Any]) -> dict[str, Any]:
        """两侧均读取完成后执行实际归并，产物落盘并把任务关联到实际产物。"""
        from app.services import data_manager as dm

        primary_rows, primary_product = dm.load_export_rows(task["primary"]["export_id"])
        secondary_rows, _ = dm.load_export_rows(task["secondary"]["export_id"])
        merged = self.merge_and_audit(task["rule_id"], primary_rows, secondary_rows)

        artifact = {
            "task_id": task["task_id"],
            "task_no": task["task_no"],
            "rule_id": task["rule_id"],
            "rule_no": task.get("rule_no"),
            "primary_export_id": task["primary"]["export_id"],
            "secondary_export_id": task["secondary"]["export_id"],
            "primary_rows": len(primary_rows),
            "secondary_rows": len(secondary_rows),
            "merged_count": len(merged),
            "rows": merged,
        }
        dm.write_merge_artifact(task["task_id"], artifact)

        task["status"] = dm.MERGE_STATUS_DONE
        task["deliverable"] = True
        task["blocked_sides"] = []
        task["merged_count"] = len(merged)
        task["artifact_path"] = str(
            dm.MERGE_ARTIFACT_DIR / f"{task['task_id']:06d}.json")
        task["events"].append({"at": dm._now_iso(), "event": "merged",
                               "merged_count": len(merged)})
        dm.write_merge_task(task)
        return {"ok": True, "task": task, "artifact": artifact,
                "message": f"归并完成，产物已关联：{task['artifact_path']}"}

    def list_merge_tasks(self) -> list[dict[str, Any]]:
        from app.services import data_manager as dm
        return dm.list_merge_tasks()

    def get_merge_task(self, task_id: int) -> dict[str, Any]:
        from app.services import data_manager as dm
        return dm.read_merge_task(task_id)

    # ---------- 既有归并能力 ----------

    def merge_sources(self, rule_id: int, source_a_rows: list[dict[str, Any]],
                      source_b_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """按归并规则合并两个数据源。"""
        rule = store.find(self.MODULE, rule_id)
        if rule is None or rule.get("status") != "已生效":
            return source_a_rows + source_b_rows

        merge_key = rule.get("归并键", "id")
        conflict_policy = rule.get("冲突策略", "主源优先")
        dedup_policy = rule.get("去重策略", "全保留")

        merged = {}
        for row in source_a_rows:
            key = str(row.get(merge_key, ""))
            merged[f"a_{key}"] = {"_source": "primary", **row}

        for row in source_b_rows:
            key = str(row.get(merge_key, ""))
            pk = f"a_{key}"
            if pk in merged:
                if conflict_policy == "主源优先":
                    continue
                elif conflict_policy == "从源覆盖":
                    merged[pk] = {"_source": "secondary", **row}
                elif conflict_policy == "合并字段":
                    merged[pk].update({"from_secondary": row})
            else:
                merged[f"b_{key}"] = {"_source": "secondary", **row}

        result = list(merged.values())

        # 去重
        if dedup_policy == "按键去重":
            seen = set()
            deduped = []
            for row in result:
                key = str(row.get(merge_key, ""))
                if key not in seen:
                    seen.add(key)
                    deduped.append(row)
            result = deduped

        # 排序
        sort_field = rule.get("结果排序", "")
        if sort_field:
            result = sorted(result, key=lambda r: str(r.get(sort_field, "")))

        return result

    def merge_and_audit(self, rule_id: int, source_a_rows: list[dict[str, Any]],
                        source_b_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """归并两个数据源并写入审计记录。

        跨模块增强：归并完成后自动写入call_audit审计。
        """
        merged = self.merge_sources(rule_id, source_a_rows, source_b_rows)
        rule = store.find(self.MODULE, rule_id)

        # 联动增强：归并结果写入审计
        from app.services.data_manager import write_audit, DATA_ROOT
        audit_id = max((int(f.stem) for f in DATA_ROOT.glob("audit/[0-9]*.json")), default=0) + 1
        write_audit(audit_id, {
            "audit_id": audit_id,
            "api_name": rule.get("主API", "") if rule else "",
            "caller": "data_merge",
            "event": "merge_completed",
            "primary_count": len(source_a_rows),
            "secondary_count": len(source_b_rows),
            "merged_count": len(merged),
            "rule_no": rule.get("规则编号", "") if rule else "",
        })
        return merged

    def merge_from_exports(self, rule_id: int) -> list[dict[str, Any]]:
        """从导出产物直接读取两个数据源并进行归并。

        跨模块增强：被 export_task 完成后调用，实现导出->归并->审计链路。
        仅选用归档下架清册中仍具取数资格的导出产物，过期/已下架文件跳过。
        """
        from app.services.data_manager import list_exports, evaluate_export_eligibility
        exports = list_exports()
        exports.sort(key=lambda x: x.get("_created_at", ""), reverse=True)
        qualified = [
            product for product in exports
            if evaluate_export_eligibility(int(product["export_id"])).get("eligible")
        ]
        source_a = qualified[0] if len(qualified) > 0 else {}
        source_b = qualified[1] if len(qualified) > 1 else {}

        rows_a = []
        rows_b = []
        cols_a = source_a.get("columns", [])
        cols_b = source_b.get("columns", [])
        for row_vals in source_a.get("rows", []):
            row = {col: val for col, val in zip(cols_a, row_vals)} if cols_a else {}
            rows_a.append(row)
        for row_vals in source_b.get("rows", []):
            row = {col: val for col, val in zip(cols_b, row_vals)} if cols_b else {}
            rows_b.append(row)

        return self.merge_and_audit(rule_id, rows_a, rows_b)

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"

        if action == "标记生效":
            from app.services.data_manager import read_api_snapshot
            registry = read_api_snapshot()
            primary_api = entry.get("主API", "")
            secondary_api = entry.get("从API", "")
            api_names = {v.get("api_name") for v in (registry.values() if isinstance(registry, dict) else []) if isinstance(v, dict)}
            if primary_api not in api_names and secondary_api not in api_names:
                return None, "至少一个源API尚未注册"

        result = super().run_action(entry_id, action)
        from app.services.data_manager import write_module_snapshot
        write_module_snapshot(self.MODULE, self.domain_snapshot())
        return result

    def domain_snapshot(self) -> dict[str, Any]:
        rows = store.rows(self.MODULE)
        merge_list = []
        for row in rows:
            merge_list.append({
                "id": row.get("id"),
                "rule_no": row.get("规则编号"),
                "primary_api": row.get("主API"),
                "secondary_api": row.get("从API"),
                "merge_key": row.get("归并键"),
                "conflict_policy": row.get("冲突策略"),
                "dedup_policy": row.get("去重策略"),
                "sort": row.get("结果排序"),
                "version": row.get("规则版本"),
                "status": row.get("status"),
            })
        from app.services import data_manager as dm
        try:
            tasks = dm.list_merge_tasks()
        except Exception:  # pragma: no cover
            tasks = []
        return {
            "module": self.MODULE,
            "entity": self.ENTITY,
            "artifact": f"{self.ENTITY}数据集",
            "count": len(merge_list),
            "merges": merge_list,
            "active_count": sum(1 for r in merge_list if r.get("status") == "已生效"),
            "merge_task_total": len(tasks),
            "merge_task_blocked": sum(1 for t in tasks
                                      if t.get("status") == dm.MERGE_STATUS_BLOCKED),
            "merge_task_done": sum(1 for t in tasks
                                   if t.get("status") == dm.MERGE_STATUS_DONE),
        }