"""业务路由汇总：专用执行单路由在前，通用模块资源路由兜底。"""
from __future__ import annotations

from app.routers import archive_merge, resource

# archive_merge 含 /api/archive、/api/merge 固定路径，
# 必须排在通配 /api/{module} 资源路由之前注册。
ROUTERS = [archive_merge, resource]
