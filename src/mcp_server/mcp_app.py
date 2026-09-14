from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from src.mcp_server.protocol import ECOMCPServer
from src.mcp_server.mock import MockECOMCPServer

if TYPE_CHECKING:
    from src.mcp_server.real import RealECOMCPServer


def create_mcp_app(
    eco_server: ECOMCPServer | None = None,
    server_name: str = "eco-agent",
) -> FastMCP:
    """创建 ECO Agent MCP Server 实例。

    10 个 Tool 方法内部只做委托：
        Tool 方法 -> eco_server.run_xxx() -> Protocol 实现（Mock 或 Real）

    Args:
        eco_server: 内部持有的 ECOMCPServer 实例。
                    None 时默认使用 MockECOMCPServer(scenario="happy_path")。
        server_name: MCP Server 名称，客户端识别用。

    Returns:
        配置好 10 个 Tools 的 FastMCP 实例。
    """
    instance = eco_server or MockECOMCPServer(scenario="happy_path")

    mcp = FastMCP(server_name)

    @mcp.tool()
    def run_eco_route(design_name: str, run_dir: str) -> dict:
        """执行 ECO Route 初始化，调用 Innovus 工具完成布线。

        ECO 流水线 Phase1 第一步，成功后可继续执行寄生参数提取。

        Args:
            design_name: Design 名称，如 "MyDesign"
            run_dir:     当前迭代运行目录

        Returns:
            {"route_done": bool} 布线是否成功完成

        Raises:
            Exception: Innovus 工具调用失败时抛出
        """
        return instance.run_eco_route(design_name=design_name, run_dir=run_dir)

    @mcp.tool()
    def run_ext(design_name: str, run_dir: str) -> dict:
        """执行寄生参数提取，调用 StarRCX 生成寄生网表。

        ECO 流水线 Phase1 串行第二步，依赖 run_eco_route 成功。

        Args:
            design_name: Design 名称
            run_dir:     当前迭代运行目录

        Returns:
            {"ext_done": bool} 提取是否成功完成

        Raises:
            Exception: StarRCX license 失效或执行失败时抛出
        """
        return instance.run_ext(design_name=design_name, run_dir=run_dir)

    @mcp.tool()
    def run_sta(design_name: str, run_dir: str) -> dict:
        """执行静态时序分析 (STA)，调用 PrimeTime 生成违例报告。

        ECO 流水线 Phase2 并行任务之一，返回 Setup 和 Hold 违例数。

        Args:
            design_name: Design 名称
            run_dir:     当前迭代运行目录

        Returns:
            {"setup_vio": int, "hold_vio": int} Setup 和 Hold 违例数

        Raises:
            Exception: STA 工具超时或 license 失效时抛出
        """
        return instance.run_sta(design_name=design_name, run_dir=run_dir)

    @mcp.tool()
    def run_pv(design_name: str, run_dir: str) -> dict:
        """执行物理验证 (PV)，调用 Calibre 做 DRC/LVS 检查。

        ECO 流水线 Phase2 并行任务之一。

        Args:
            design_name: Design 名称
            run_dir:     当前迭代运行目录

        Returns:
            {"pv_pass": bool} DRC/LVS 是否全部通过

        Raises:
            Exception: PV LVS 检查失败（netlist mismatch）时抛出
        """
        return instance.run_pv(design_name=design_name, run_dir=run_dir)

    @mcp.tool()
    def run_signoff(design_name: str, run_dir: str) -> dict:
        """执行 Signoff 签核检查。

        ECO 流水线 Phase2 并行任务之一，最终门禁检查。

        Args:
            design_name: Design 名称
            run_dir:     当前迭代运行目录

        Returns:
            {"signoff_pass": bool, "violations": list} Signoff 结果和违例详情

        Raises:
            Exception: Signoff DRC 检查失败时抛出
        """
        return instance.run_signoff(design_name=design_name, run_dir=run_dir)

    @mcp.tool()
    def run_pt_fix_setup(design_name: str, run_dir: str, fix_strategy: str) -> dict:
        """执行 PT ECO Setup 违例修复。

        ECO 流水线 Phase3 分支任务之一，PT 引擎修复 setup 违例。

        Args:
            design_name:    Design 名称
            run_dir:        当前迭代运行目录
            fix_strategy:   修复策略，如 "setup_via_repair"

        Returns:
            {"fix_done": bool, "setup_vio": int} 修复状态和剩余违例数

        Raises:
            Exception: PT ECO Fix 工具异常时抛出
        """
        return instance.run_pt_fix_setup(
            design_name=design_name, run_dir=run_dir, fix_strategy=fix_strategy,
        )

    @mcp.tool()
    def run_pt_fix_hold(design_name: str, run_dir: str, fix_strategy: str) -> dict:
        """执行 PT ECO Hold 违例修复。

        ECO 流水线 Phase3 分支任务之一，PT 引擎修复 hold 违例。

        Args:
            design_name:    Design 名称
            run_dir:        当前迭代运行目录
            fix_strategy:   修复策略，如 "hold_delay_cell"

        Returns:
            {"fix_done": bool, "hold_vio": int} 修复状态和剩余违例数

        Raises:
            Exception: PT ECO Fix 工具异常时抛出
        """
        return instance.run_pt_fix_hold(
            design_name=design_name, run_dir=run_dir, fix_strategy=fix_strategy,
        )

    @mcp.tool()
    def run_pt_fix_leakage(design_name: str, run_dir: str, fix_strategy: str) -> dict:
        """执行 PT ECO Leakage 泄漏修复。

        ECO 流水线 Phase3 分支任务之一，PT 引擎修复 leakage 违例。

        Args:
            design_name:    Design 名称
            run_dir:        当前迭代运行目录
            fix_strategy:   修复策略

        Returns:
            {"fix_done": bool} 修复是否成功

        Raises:
            Exception: PT ECO Fix 工具异常时抛出
        """
        return instance.run_pt_fix_leakage(
            design_name=design_name, run_dir=run_dir, fix_strategy=fix_strategy,
        )

    @mcp.tool()
    def run_pt_fix_drv(design_name: str, run_dir: str, fix_strategy: str) -> dict:
        """执行 PT ECO DRV (Design Rule Violation) 修复。

        ECO 流水线 Phase3 分支任务之一，PT 引擎修复 DRV 违例。

        Args:
            design_name:    Design 名称
            run_dir:        当前迭代运行目录
            fix_strategy:   修复策略

        Returns:
            {"fix_done": bool} 修复是否成功

        Raises:
            Exception: PT ECO Fix 工具异常时抛出
        """
        return instance.run_pt_fix_drv(
            design_name=design_name, run_dir=run_dir, fix_strategy=fix_strategy,
        )

    @mcp.tool()
    def run_xtop_fix_hold(design_name: str, run_dir: str, fix_strategy: str) -> dict:
        """执行 XTOP ECO Hold 违例修复。

        ECO 流水线 Phase3 分支任务之一，XTOP 引擎修复 hold 违例（PT 引擎的备选/补充）。

        Args:
            design_name:    Design 名称
            run_dir:        当前迭代运行目录
            fix_strategy:   修复策略

        Returns:
            {"fix_done": bool, "hold_vio": int} 修复状态和剩余违例数

        Raises:
            Exception: XTOP ECO Fix 工具异常时抛出
        """
        return instance.run_xtop_fix_hold(
            design_name=design_name, run_dir=run_dir, fix_strategy=fix_strategy,
        )

    return mcp
