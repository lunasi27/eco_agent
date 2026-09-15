"""/init 命令：扫描 EDA 工作目录，生成 eco_agent/config.yaml。

用法：
  /init                    扫描当前工作目录
  /init /path/to/work_dir  扫描指定目录

扫描推断：
  project_name  从 N.APR.{design} / N.PTECO.{design} 等目录名反推
  work_dir      扫描的目录路径
  project_cshrc 搜索 */c_shell/*.cshrc 模式

模板占位（resolve 时自动展开，不需要扫描）：
  base.run_eco_route = "{work_dir}/2.APR.{project_name}"
  base.run_ext       = "{work_dir}/3.EXT.{project_name}"
  base.run_pt_fix    = "{work_dir}/4.PTECO.{project_name}"
  ...

需手动填写（扫描不到的跨盘路径 / 集群参数）：
  base.run_sta_rpt  base.run_pv  dsub_queue
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

# ── 目录名匹配模式 ──
# EDA 工程目录命名约定：N.PHASE.DESIGN_NAME
# 例如：2.APR.SOC_XXX_SUB、3.EXT.SOC_XXX_SUB、4.PTECO.SOC_XXX_SUB
_DIR_PATTERNS: dict[str, re.Pattern] = {
    "APR":   re.compile(r"\d+\.APR\.(.+)"),
    "EXT":   re.compile(r"\d+\.EXT\.(.+)"),
    "PTECO": re.compile(r"\d+\.PTECO\.(.+)"),
    "xtop":  re.compile(r"\d+\.xtop\.(.+)"),
}


def scan_eda_directory(work_dir: Path) -> dict:
    """扫描 EDA 工作目录，推断配置值。

    Returns:
        dict with keys: project_name, work_dir, project_cshrc, found_dirs
        project_name 为 None 时表示无法识别（不是 EDA 工程目录）
    """
    info: dict = {
        "project_name": None,
        "work_dir": str(work_dir),
        "project_cshrc": None,
        "found_dirs": [],
    }

    if not work_dir.is_dir():
        return info

    # 1. 从目录名推断 project_name
    for entry in sorted(work_dir.iterdir()):
        if not entry.is_dir():
            continue
        for phase, pat in _DIR_PATTERNS.items():
            m = pat.match(entry.name)
            if m:
                pname = m.group(1)
                if info["project_name"] is None:
                    info["project_name"] = pname
                elif info["project_name"] != pname:
                    # 不同目录出现不同 design 名，取第一个，标记冲突
                    info["project_name"] = info["project_name"]
                info["found_dirs"].append((phase, entry))
                break

    # 2. 搜索 project.cshrc（约定路径：*/c_shell/*.cshrc）
    cshrc = _find_cshrc(work_dir)
    if cshrc:
        info["project_cshrc"] = str(cshrc)
    info["found_dirs"].sort(key=lambda x: x[0])

    return info


def _find_cshrc(work_dir: Path) -> Path | None:
    """在 work_dir 下搜索 project.cshrc（约定路径模式）。

    搜索优先级：
      1. work_dir/common/0.c_shell/*.cshrc   （最常见）
      2. work_dir/*/c_shell/*.cshrc          （变体）
    """
    for pattern in ("common/0.c_shell/*.cshrc", "*/c_shell/*.cshrc"):
        matches = sorted(work_dir.glob(pattern))
        if matches:
            return matches[0]
    return None


def _build_config(info: dict) -> str:
    """根据扫描结果生成 config.yaml 内容。"""
    pname = info["project_name"] or "TODO_PROJECT_NAME"
    work_dir = info["work_dir"]
    cshrc = info["project_cshrc"] or "# TODO: 填入 IT 环境脚本路径（如 /data/.../project.cshrc）"

    # 扫描到的目录用于确认哪些 base 路径实际存在
    found_phases = {phase for phase, _ in info["found_dirs"]}

    config: dict = {
        "project_name": pname,
        "work_dir": work_dir,
        "dsub_queue": [
            "# TODO: 按实际集群修改",
            "dsub -A root.xxxx.class -q lang -R 'cpu=8;mem=40000'",
        ],
        "project_cshrc": cshrc,
        "base": {
            "run_eco_route":    "{work_dir}/2.APR.{project_name}",
            "run_ext":          "{work_dir}/3.EXT.{project_name}",
            "run_sta_bin":      "{base.run_eco_route}/tools/sta",
            "run_sta_rpt":      "# TODO: 填入 STA 报告目录（跨盘路径，扫描不到）",
            "run_pv":           "# TODO: 填入 PV 目录（跨盘路径，扫描不到）",
            "run_signoff":      "{base.run_eco_route}",
            "run_pt_fix":       "{work_dir}/4.PTECO.{project_name}",
            "run_xtop_fix":     "{work_dir}/4.xtop.{project_name}",
            "dataout":          "{work_dir}/0.outgoing/{project_name}",
        },
        "logs": {
            "run_eco_route":    "{base.run_eco_route}/log",
            "run_ext":          "{base.run_ext}/run/LOGS",
            "run_sta":          "{base.run_sta_rpt}/{project_name}",
            "run_pv":           "{base.run_pv}/log",
            "run_signoff":      "{base.run_signoff}/log/signoff_check_*",
            "run_pt_fix":       "{base.run_pt_fix}/log",
            "run_xtop_fix":     "{base.run_xtop_fix}/log",
        },
        "wait_flags": {
            "run_eco_route":     "{base.dataout}/{project_name}.def.ok",
            "run_ext":           "{base.run_ext}/run/LOGS/{project_name}.spef.smc/LOG/ok.flag",
            "run_sta":           "{base.run_sta_rpt}/{project_name}/{project_name}.sta.ok",
            "run_pv":            "{base.run_pv}/log/{project_name}.pv.ok",
            "run_signoff":       "{base.run_signoff}/log/signoff_check.ok",
            "run_pt_fix_setup":  "{base.run_pt_fix}/output/fix_setup/fix_setup.tcl",
            "run_pt_fix_hold":   "{base.run_pt_fix}/output/fix_hold/fix_hold.tcl",
            "run_pt_fix_leakage":"{base.run_pt_fix}/output/fix_leakage/fix_leakage.tcl",
            "run_pt_fix_drv":    "{base.run_pt_fix}/output/fix_drv/fix_drv.tcl",
            "run_xtop_fix_hold": "{base.run_xtop_fix}/output/xtop_fix_hold.tcl",
        },
        "step_command": {
            "run_eco_route":        "pds_innovus 6.ecoRt",
            "run_ext":              "pds_starrc DEF_SMC",
            "run_sta":              "run_sta.csh {project_name} all CTS ptpx -input {base.dataout} -output {base.run_sta_rpt}",
            "run_pv":               "source 100P_PV.csh {design_name}",
            "run_signoff":          "pds_innovus 6-2.signoff_check",
            "run_pt_fix_setup":     "pds_pteco setup",
            "run_pt_fix_hold":      "pds_pteco hold",
            "run_pt_fix_leakage":   "pds_pteco leakage",
            "run_pt_fix_drv":       "pds_pteco ldrc",
            "run_xtop_fix_hold":    "source run_fix_hold.csh {design_name}",
        },
        "timeout_s": {
            "run_eco_route":      3600,
            "run_ext":            3600,
            "run_sta":            7200,
            "run_pv":             3600,
            "run_signoff":        3600,
            "run_pt_fix_setup":   3600,
            "run_pt_fix_hold":    1800,
            "run_pt_fix_leakage": 1800,
            "run_pt_fix_drv":     1800,
            "run_xtop_fix_hold":  1800,
        },
        "design_name": "{project_name}",
        "execution_dir": "{base.run_eco_route}",
        "preco_db":      "{base.run_eco_route}/DB/{design_name}_fixhold.enc",
        "preco_db_next": "{base.run_eco_route}/DB/{design_name}_fixhold_fixsetup.enc",
        "eco_scripts":   "{base.run_pt_fix}/output/setup.tcl",
    }

    return yaml.safe_dump(config, sort_keys=False, allow_unicode=True, width=120)


def run_init(work_dir_str: str = "") -> str:
    """执行 /init：扫描目录 → 创建 eco_agent/ → 写 config.yaml。

    Args:
        work_dir_str: EDA 工作目录路径，空则用当前工作目录

    Returns:
        给用户看的报告文本
    """
    import os

    work_dir = Path(work_dir_str) if work_dir_str else Path.cwd()
    work_dir = work_dir.resolve()

    if not work_dir.is_dir():
        return f"❌ 目录不存在: {work_dir}"

    # 1. 扫描
    info = scan_eda_directory(work_dir)

    if info["project_name"] is None:
        lines = [f"❌ 未能从 {work_dir} 识别到 EDA 工程目录。",
                 "",
                 "期望看到以下命名模式的目录：",
                 "  N.APR.<design>     （如 2.APR.SOC_XXX_SUB）",
                 "  N.EXT.<design>     （如 3.EXT.SOC_XXX_SUB）",
                 "  N.PTECO.<design>   （如 4.PTECO.SOC_XXX_SUB）",
                 "",
                 "请确认路径是否正确，或手动指定：/init /path/to/eda/work"]
        return "\n".join(lines)

    # 2. 创建 eco_agent/ 目录
    eco_dir = work_dir / "eco_agent"
    eco_dir.mkdir(exist_ok=True)

    # 3. 如果 config.yaml 已存在，备份
    config_path = eco_dir / "config.yaml"
    if config_path.exists():
        backup = eco_dir / "config.yaml.bak"
        backup.write_text(config_path.read_text())
        print(f"  已备份旧配置到: {backup}")

    # 4. 生成 config.yaml
    yaml_content = _build_config(info)
    config_path.write_text(yaml_content)

    # 5. 报告
    lines = [
        f"✅ 配置已生成: {config_path}",
        "",
        f"  project_name : {info['project_name']}",
        f"  work_dir     : {info['work_dir']}",
        f"  project_cshrc: {info['project_cshrc'] or '(未找到，需手动填写)'}",
        "",
        "  扫描到的目录：",
    ]
    if info["found_dirs"]:
        for phase, path in info["found_dirs"]:
            lines.append(f"    {phase:6s} → {path}")
    else:
        lines.append("    (未找到标准 EDA 目录，但 project_name 已从其他来源推断)")

    lines.extend([
        "",
        "  ⚠ 需手动填写：",
        "    base.run_sta_rpt   STA 报告目录（跨盘路径）",
        "    base.run_pv        PV 目录（跨盘路径）",
        "    dsub_queue         集群调度参数",
        "",
        "  启动 Real 模式：",
        f"    ECO_CONFIG={config_path} ECO_BACKEND=real python -m src.mcp_server.stdio_runner",
    ])

    return "\n".join(lines)
