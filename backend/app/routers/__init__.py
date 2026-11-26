"""业务路由汇总：所有模块共用一套资源路由，办理流额外挂语义别名路由。"""
from __future__ import annotations

from app.routers import dist_flow, resource

# 注意顺序：通用资源路由里的 /api/{module}/{id}/actions 必须先于
# dist_flow 的 /api/dist_receipt/{id}/{action_name} 兜底段注册。
ROUTERS = [resource, dist_flow]
