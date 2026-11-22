"""归档下架 / 归并任务 HTTP 接口端到端测试。"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

_TEMP_DATA = os.path.join(tempfile.gettempdir(), "cc10026_shared_test_data")
os.environ['DATA_ROOT'] = _TEMP_DATA

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client():
    from app.seed import SEED_ROWS
    from app.store import store as s
    from app.services import data_manager as dm

    s._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
    if os.path.exists(dm.DATA_ROOT):
        shutil.rmtree(dm.DATA_ROOT, ignore_errors=True)
    dm.reset_clock()
    from app.main import app
    with TestClient(app) as c:
        yield c, dm
    dm.reset_clock()


def _prepare_exports(c, dm):
    dm.set_clock("2026-11-20T09:00:00+08:00")
    from app.store import store
    for export_id, expire in ((1, "2026-12-31"), (2, "2026-11-19"), (3, "2026-12-31")):
        store.find("export_task", export_id)["过期时间"] = expire
        resp = c.post(f"/api/export_task/{export_id}/actions",
                      json={"action": "发起导出"})
        assert resp.json()["ok"], resp.text


def test_roster_and_archive_endpoints(client):
    c, dm = client
    _prepare_exports(c, dm)

    roster = c.get("/api/archive/roster").json()
    assert roster["due_count"] == 1
    assert roster["due_export_ids"] == [2]

    # 未到期文件拒绝提前下架
    refused = c.post("/api/archive/exports/1")
    assert refused.status_code == 400

    # 到期文件可单份下架
    ok = c.post("/api/archive/exports/2")
    assert ok.status_code == 200
    assert ok.json()["file_status"] == "已下架"

    # 重复下架幂等
    again = c.post("/api/archive/exports/2")
    assert again.status_code == 200

    # 导出任务行状态同步为已下架
    rows = c.get("/api/export_task").json()["items"]
    row2 = next(r for r in rows if r["id"] == 2)
    assert row2["status"] == "已下架"


def test_merge_task_end_to_end_and_expiry_guard(client):
    c, dm = client
    _prepare_exports(c, dm)
    rule_id = 1

    # 到期来源不能登记新归并
    rejected = c.post("/api/merge/tasks", json={
        "rule_id": rule_id, "primary_export_id": 1, "secondary_export_id": 2})
    assert rejected.status_code == 400

    # 有效来源登记成功
    created = c.post("/api/merge/tasks", json={
        "rule_id": rule_id, "primary_export_id": 1, "secondary_export_id": 3})
    assert created.status_code == 200
    task_id = created.json()["task"]["task_id"]

    # 可用来源清单只含在有效期内文件
    sources = {item["export_id"] for item in c.get("/api/merge/sources").json()["items"]}
    assert sources == {1, 3}

    # 主侧 -> 从侧 -> 交付
    assert c.post(f"/api/merge/tasks/{task_id}/start").status_code == 200
    finished = c.post(f"/api/merge/tasks/{task_id}/secondary")
    assert finished.status_code == 200
    assert finished.json()["task"]["status"] == "已完成"

    delivery = c.get(f"/api/merge/tasks/{task_id}/delivery").json()
    assert delivery["deliverable"] is True
    assert delivery["missing_sources"] == []

    # 任务列表与产物可查
    tasks = c.get("/api/merge/tasks").json()
    assert tasks["total"] == 1
    assert c.get(f"/api/merge/tasks/{task_id}").status_code == 200


def test_blocked_task_recover_via_api(client):
    c, dm = client
    _prepare_exports(c, dm)
    rule_id = 1

    # 再准备一份长期有效的替补来源（导出任务 id 由 store 自增分配）
    from app.store import store
    created_export = c.post("/api/export_task", json={"values": {
        "任务编号": "EXPO-RECOVER", "关联API": "恢复来源API", "查询参数": "all",
        "过期时间": "2026-12-31"}}).json()["entry"]
    replacement_id = created_export["id"]
    run = c.post(f"/api/export_task/{replacement_id}/actions",
                 json={"action": "发起导出"})
    assert run.json()["ok"], run.text

    # 用 1+3 登记，随后让 3 到期，制造读取前受阻
    created = c.post("/api/merge/tasks", json={
        "rule_id": rule_id, "primary_export_id": 1, "secondary_export_id": 3})
    task_id = created.json()["task"]["task_id"]
    assert c.post(f"/api/merge/tasks/{task_id}/start").status_code == 200

    entry3 = store.find("export_task", 3)
    entry3["过期时间"] = "2026-11-01"
    dm._write_json(dm.DATA_ROOT / "exports" / "000003.json",
                   {**dm.read_export(3), "expire_at": "2026-11-01"})

    blocked = c.post(f"/api/merge/tasks/{task_id}/secondary")
    assert blocked.status_code == 400

    delivery = c.get(f"/api/merge/tasks/{task_id}/delivery").json()
    assert delivery["deliverable"] is False
    assert delivery["missing_sources"][0]["side"] == "secondary"

    # 替补来源不能与主侧重复
    dup = c.post(f"/api/merge/tasks/{task_id}/recover",
                 json={"side": "secondary", "replacement_export_id": 1})
    assert dup.status_code == 400

    # 用有效替补来源恢复并完成交付
    recovered = c.post(f"/api/merge/tasks/{task_id}/recover",
                       json={"side": "secondary",
                             "replacement_export_id": replacement_id})
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["task"]["status"] == "已完成"
    assert c.get(f"/api/merge/tasks/{task_id}/delivery").json()["deliverable"] is True
