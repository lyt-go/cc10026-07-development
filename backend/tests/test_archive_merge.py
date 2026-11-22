"""归档下架清册 × 数据归并 联动测试。

覆盖需求场景：
1. 过期时间真正驱动可执行的归档下架清册（文件状态 + 取数资格 + 关联归并）；
2. 一笔归并先取到主侧，另一侧到期前后分别登记新单并执行，核对各自看到的
   来源与可继续操作；
3. 在途归并遇来源到期转「待恢复」，接手人补来源可恢复，归档状态不会被误判
   为全部汇总成功；
4. 过期后无论是否已执行下架，新归并都不能再选用该文件；
5. 读取完成 / 需恢复状态关联到实际导出产物与归并任务及最终产物。
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# 与 test_cross_module 保持同一约定：DATA_ROOT 在 data_manager 首次导入时即被
# 冻结，整个测试会话共用一个临时根目录，各用例自行清空重建，避免两个测试模块
# 各自设置不同 DATA_ROOT 导致后导入者的环境变量不生效。
_TEMP_DATA = os.path.join(tempfile.gettempdir(), "cc10026_shared_test_data")
os.environ['DATA_ROOT'] = _TEMP_DATA

import pytest  # noqa: E402


@pytest.fixture()
def context():
    """每个用例重建 store、清空数据目录并复位业务时钟。"""
    from app.seed import SEED_ROWS
    from app.store import store as s
    from app.services import data_manager as dm

    s._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
    if os.path.exists(dm.DATA_ROOT):
        shutil.rmtree(dm.DATA_ROOT, ignore_errors=True)
    dm.reset_clock()
    yield dm
    dm.reset_clock()


def _make_export(svc, entry_id: int, expire_at: str):
    """执行种子导出任务并把过期时间写入导出产物。"""
    from app.store import store
    entry = store.find("export_task", entry_id)
    entry["过期时间"] = expire_at
    result = svc.execute_export(entry_id)
    assert result["ok"], result
    return entry_id


class TestArchiveRosterAndMerge:
    """归档下架清册与数据归并全链路。"""

    def test_roster_tracks_status_eligibility_and_due_items(self, context):
        """清册按过期时间辨认在用/待下架，并可执行下架。"""
        dm = context
        from app.services.export_task import ExportTaskService

        export_svc = ExportTaskService()
        dm.set_clock("2026-11-20T09:00:00+08:00")
        # A 未到期；B 当天到期（按“过期日之后才算到期”仍在用）；C 已过期
        _make_export(export_svc, 1, "2026-12-31")
        _make_export(export_svc, 2, "2026-11-20")
        _make_export(export_svc, 3, "2026-11-10")

        roster = export_svc.build_roster()
        by_id = {e["export_id"]: e for e in roster["entries"]}
        assert by_id[1]["file_status"] == dm.FILE_STATUS_ACTIVE and by_id[1]["eligible"] is True
        assert by_id[2]["file_status"] == dm.FILE_STATUS_ACTIVE
        assert by_id[3]["file_status"] == dm.FILE_STATUS_DUE and by_id[3]["eligible"] is False
        assert roster["due_export_ids"] == [3]

        # 到期但未下架：取数资格已失效，但文件状态仍可区分于“已下架”
        status3 = dm.evaluate_export_eligibility(3)
        assert status3["eligible"] is False
        assert status3["file_status"] == dm.FILE_STATUS_DUE

        # 执行下架后：文件状态与取数资格同时落到终态
        outcome = export_svc.execute_roster()
        assert outcome["executed_count"] == 1
        after = dm.evaluate_export_eligibility(3)
        assert after["file_status"] == dm.FILE_STATUS_ARCHIVED
        assert after["eligible"] is False
        product = dm.read_export(3)
        assert product["file_status"] == dm.FILE_STATUS_ARCHIVED
        assert product["eligible"] is False

    def test_expiry_race_blocked_merge_can_recover(self, context):
        """主侧已读、从侧到期冲突：旧任务转待恢复，补来源后可交付。"""
        dm = context
        from app.store import store
        from app.services.export_task import ExportTaskService
        from app.services.data_merge import DataMergeService

        export_svc = ExportTaskService()
        merge_svc = DataMergeService()

        dm.set_clock("2026-11-20T09:00:00+08:00")
        # A=主侧（长期有效），B=从侧（次日到期），C=替补来源（长期有效）
        _make_export(export_svc, 1, "2026-12-31")
        _make_export(export_svc, 2, "2026-11-21")
        _make_export(export_svc, 3, "2026-12-31")

        rule = store.rows("data_merge")[0]
        rule_id = rule["id"]

        # ---- 旧归并：两侧都在有效期内登记成功 ----
        registered = merge_svc.register_merge_task(rule_id, 1, 2)
        assert registered["ok"], registered["message"]
        old_task_id = registered["task"]["task_id"]

        # 先取到主侧数据
        started = merge_svc.start_merge(old_task_id)
        assert started["ok"]
        assert started["task"]["status"] == dm.MERGE_STATUS_READING
        assert started["task"]["primary"]["read_state"] == dm.SIDE_READ

        # ---- 时钟推进到从侧到期之后 ----
        dm.set_clock("2026-11-22T09:00:00+08:00")
        # 值班人先核对：此时尚未读从侧，任务不可交付
        check = merge_svc.check_delivery(old_task_id)
        assert check["deliverable"] is False
        assert check["status"] == dm.MERGE_STATUS_READING

        # 正要读另一侧时与到期冲突：任务受阻，明确要补从侧来源
        blocked = merge_svc.read_secondary(old_task_id)
        assert blocked["ok"] is False
        task = dm.read_merge_task(old_task_id)
        assert task["status"] == dm.MERGE_STATUS_BLOCKED
        assert task["deliverable"] is False
        assert task["secondary"]["read_state"] == dm.SIDE_BLOCKED
        assert "从侧" in task["secondary"]["blocked_reason"]
        # 已读取的主侧状态与产物关联保留，不能丢
        assert task["primary"]["read_state"] == dm.SIDE_READ
        assert task["primary"]["export_id"] == 1

        delivery = merge_svc.check_delivery(old_task_id)
        assert delivery["deliverable"] is False
        assert delivery["missing_sources"][0]["side"] == "secondary"

        # ---- 到期后登记新归并：不能再选用这份到期文件（无论下架与否）----
        new_order = merge_svc.register_merge_task(rule_id, 3, 2)
        assert new_order["ok"] is False
        assert "取数资格" in new_order["message"]
        # 当前可选用来源里已经看不到 B（export 2）
        available = {s["export_id"] for s in merge_svc.list_available_sources()}
        assert 2 not in available and 1 in available and 3 in available

        # ---- 执行归档下架：在途归并被关联，受阻侧保持需恢复 ----
        archive_outcome = export_svc.execute_roster()
        archived_ids = [r["export_id"] for r in archive_outcome["results"]]
        assert 2 in archived_ids
        roster_by_id = {e["export_id"]: e for e in dm.list_roster_entries()}
        assert merge_svc.get_merge_task(old_task_id)["task_no"] in \
            roster_by_id[2]["affected_merge_tasks"]

        # 归档状态不等于汇总成功：任务仍是待恢复
        task_after_archive = dm.read_merge_task(old_task_id)
        assert task_after_archive["status"] == dm.MERGE_STATUS_BLOCKED
        assert task_after_archive["deliverable"] is False

        # 下架后新归并同样被拒
        again = merge_svc.register_merge_task(rule_id, 1, 2)
        assert again["ok"] is False

        # ---- 接手人补一份有效来源，任务恢复并产出实际归并产物 ----
        recovered = merge_svc.recover_source(old_task_id, "secondary", 3)
        assert recovered["ok"], recovered["message"]
        finished = dm.read_merge_task(old_task_id)
        assert finished["status"] == dm.MERGE_STATUS_DONE
        assert finished["deliverable"] is True
        assert finished["artifact_path"]
        artifact = dm.read_merge_artifact(old_task_id)
        assert artifact["secondary_export_id"] == 3
        assert artifact["merged_count"] >= 1
        # 两侧读取状态都关联到实际产物所用来源
        assert finished["secondary"]["read_state"] == dm.SIDE_READ
        assert finished["secondary"]["export_id"] == 3

    def test_new_merge_before_and_after_expiry_see_different_sources(self, context):
        """到期前后分别登记新单并执行：可见来源与可继续操作不同。"""
        dm = context
        from app.store import store
        from app.services.export_task import ExportTaskService
        from app.services.data_merge import DataMergeService

        export_svc = ExportTaskService()
        merge_svc = DataMergeService()
        rule_id = store.rows("data_merge")[0]["id"]

        # 到期前
        dm.set_clock("2026-11-20T09:00:00+08:00")
        _make_export(export_svc, 1, "2026-12-31")
        _make_export(export_svc, 2, "2026-11-21")

        before = merge_svc.register_merge_task(rule_id, 1, 2)
        assert before["ok"]
        task_before = before["task"]["task_id"]
        # 到期前登记的单可一路执行完成
        assert merge_svc.start_merge(task_before)["ok"]
        done = merge_svc.read_secondary(task_before)
        assert done["ok"]
        assert dm.read_merge_task(task_before)["status"] == dm.MERGE_STATUS_DONE

        # 到期后登记的新单：B 已不可选，只能用仍有效的来源组合
        dm.set_clock("2026-11-22T09:00:00+08:00")
        _make_export(export_svc, 3, "2026-12-31")
        rejected = merge_svc.register_merge_task(rule_id, 1, 2)
        assert rejected["ok"] is False
        accepted = merge_svc.register_merge_task(rule_id, 1, 3)
        assert accepted["ok"]
        task_after = accepted["task"]["task_id"]
        assert merge_svc.start_merge(task_after)["ok"]
        assert merge_svc.read_secondary(task_after)["ok"]
        assert dm.read_merge_task(task_after)["status"] == dm.MERGE_STATUS_DONE

    def test_archive_marks_in_flight_merge_and_roster_links_merge(self, context):
        """下架同时处理失效文件状态、取数资格并关联涉及的归并。"""
        dm = context
        from app.store import store
        from app.services.export_task import ExportTaskService
        from app.services.data_merge import DataMergeService

        export_svc = ExportTaskService()
        merge_svc = DataMergeService()
        rule_id = store.rows("data_merge")[0]["id"]

        dm.set_clock("2026-11-20T09:00:00+08:00")
        _make_export(export_svc, 1, "2026-12-31")
        _make_export(export_svc, 2, "2026-11-19")

        order = merge_svc.register_merge_task(rule_id, 1, 2)
        assert order["ok"] is False, "登记时从侧已到期，应直接拒绝"

        # 把 B 改成到期前登记，再让时钟跨过到期日，制造在途冲突
        entry2 = store.find("export_task", 2)
        entry2["过期时间"] = "2026-12-31"
        dm._write_json(dm.DATA_ROOT / "exports" / "000002.json",
                       {**dm.read_export(2), "expire_at": "2026-12-31"})
        order = merge_svc.register_merge_task(rule_id, 1, 2)
        assert order["ok"]
        task_id = order["task"]["task_id"]
        merge_svc.start_merge(task_id)

        # B 到期并执行下架
        entry2["过期时间"] = "2026-11-19"
        dm._write_json(dm.DATA_ROOT / "exports" / "000002.json",
                       {**dm.read_export(2), "expire_at": "2026-11-19"})
        result = export_svc.archive_one(2)
        assert result["ok"]
        assert result["file_status"] == dm.FILE_STATUS_ARCHIVED

        task = dm.read_merge_task(task_id)
        assert task["status"] == dm.MERGE_STATUS_BLOCKED
        assert task["blocked_sides"] == ["secondary"]
        # 清册条目关联到该归并任务
        entry = next(e for e in dm.list_roster_entries() if e["export_id"] == 2)
        assert task["task_no"] in entry["affected_merge_tasks"]

        # 已完成的历史归并不受下架影响（不被回改状态）
        finished_task_no = task["task_no"]
        assert dm.read_merge_task_by_no(finished_task_no)["status"] == \
            dm.MERGE_STATUS_BLOCKED

    def test_blocked_primary_can_also_be_recovered(self, context):
        """主侧在读取前即失效时同样进入待恢复，并可补主侧来源。"""
        dm = context
        from app.store import store
        from app.services.export_task import ExportTaskService
        from app.services.data_merge import DataMergeService

        export_svc = ExportTaskService()
        merge_svc = DataMergeService()
        rule_id = store.rows("data_merge")[0]["id"]

        dm.set_clock("2026-11-20T09:00:00+08:00")
        _make_export(export_svc, 1, "2026-12-31")
        _make_export(export_svc, 2, "2026-12-31")
        _make_export(export_svc, 3, "2026-12-31")

        order = merge_svc.register_merge_task(rule_id, 1, 2)
        task_id = order["task"]["task_id"]

        # 主侧文件 1 到期后再 start
        entry1 = store.find("export_task", 1)
        entry1["过期时间"] = "2026-11-01"
        dm._write_json(dm.DATA_ROOT / "exports" / "000001.json",
                       {**dm.read_export(1), "expire_at": "2026-11-01"})
        started = merge_svc.start_merge(task_id)
        assert started["ok"] is False
        task = dm.read_merge_task(task_id)
        assert task["status"] == dm.MERGE_STATUS_BLOCKED
        assert task["blocked_sides"] == ["primary"]

        # 用文件 3 补主侧，另一侧（文件2）仍待读取，任务回到读取中而非误报完成
        recovered = merge_svc.recover_source(task_id, "primary", 3)
        assert recovered["ok"]
        task = dm.read_merge_task(task_id)
        assert task["status"] == dm.MERGE_STATUS_READING
        assert task["primary"]["read_state"] == dm.SIDE_READ
        assert task["secondary"]["read_state"] == dm.SIDE_PENDING

        # 再读从侧后正常交付
        assert merge_svc.read_secondary(task_id)["ok"]
        assert dm.read_merge_task(task_id)["status"] == dm.MERGE_STATUS_DONE
