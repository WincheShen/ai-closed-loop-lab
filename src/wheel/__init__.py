"""决策转盘（Decision Wheel）— 事件 × 转盘架构。

分层（docs/decision_wheel_architecture.md）：
- 事件层：什么时候进来（clock / market / position / account / followup）
- 角色绑定：这个角色在这个事件上用哪个转盘、带什么参数（config/wheels.yaml）
- 转盘：进来以后按什么步骤判断（DataStep 取数 / Factor 判断 / 合并规则）
- 系统底线 + ExitGateway：任何配置都不可绕过

Phase 0-1：协议 + 出场转盘 + ExitGateway，全部以 shadow 模式并行于旧路径。
"""
