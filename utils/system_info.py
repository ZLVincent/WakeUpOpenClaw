"""
系统信息收集模块

提供结构化的系统状态、网络 IP、网络连通性信息。
供语音技能（格式化为 TTS 文本）和 Web API（直接 JSON）共用。
"""

import asyncio
import re
import socket
from typing import Optional

from utils.logger import get_logger

logger = get_logger("system_info")


def get_system_info() -> dict:
    """
    获取系统状态信息（CPU/温度/内存/磁盘/运行时间）。

    Returns
    -------
    dict
        cpu_percent: CPU 使用率百分比（float，0-100）
        cpu_temp: CPU 温度（摄氏度，None 表示无法读取）
        mem_used_bytes / mem_total_bytes / mem_percent
        disk_used_bytes / disk_total_bytes / disk_percent
        uptime_seconds: 系统运行秒数
    """
    import psutil

    result = {
        "cpu_percent": 0.0,
        "cpu_temp": None,
        "mem_used_bytes": 0,
        "mem_total_bytes": 0,
        "mem_percent": 0,
        "disk_used_bytes": 0,
        "disk_total_bytes": 0,
        "disk_percent": 0,
        "uptime_seconds": 0,
    }

    try:
        result["cpu_percent"] = psutil.cpu_percent(interval=1)
    except Exception as e:
        logger.debug("读取 CPU 使用率失败: %s", e)

    # CPU 温度（树莓派 /sys/class/thermal/thermal_zone0/temp，单位毫摄氏度）
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            result["cpu_temp"] = int(f.read().strip()) / 1000
    except Exception:
        pass

    try:
        mem = psutil.virtual_memory()
        result["mem_used_bytes"] = mem.used
        result["mem_total_bytes"] = mem.total
        result["mem_percent"] = mem.percent
    except Exception as e:
        logger.debug("读取内存失败: %s", e)

    try:
        disk = psutil.disk_usage("/")
        result["disk_used_bytes"] = disk.used
        result["disk_total_bytes"] = disk.total
        result["disk_percent"] = disk.percent
    except Exception as e:
        logger.debug("读取磁盘失败: %s", e)

    try:
        with open("/proc/uptime", "r") as f:
            result["uptime_seconds"] = float(f.read().split()[0])
    except Exception as e:
        logger.debug("读取运行时间失败: %s", e)

    return result


def get_ip_info() -> dict:
    """
    获取本机 IP 地址信息。

    过滤 loopback、tun/veth/docker 等虚拟网卡，以及 127.x/198.18.x/172.17.x/169.254.x 等虚拟 IP。

    Returns
    -------
    dict
        hostname: 主机名
        interfaces: list[{name, ip}] 有效的网卡列表
    """
    import psutil

    skip_ifaces = ("lo", "utun", "tun", "veth", "docker", "br-", "virbr")
    skip_ip_prefixes = ("127.", "198.18.", "172.17.", "169.254.")

    result = {
        "hostname": "",
        "interfaces": [],
    }

    try:
        result["hostname"] = socket.gethostname()
    except Exception as e:
        logger.debug("读取主机名失败: %s", e)

    try:
        for iface, addrs in psutil.net_if_addrs().items():
            if any(iface.startswith(p) for p in skip_ifaces):
                continue
            for addr in addrs:
                if addr.family == socket.AF_INET:
                    ip = addr.address
                    if any(ip.startswith(p) for p in skip_ip_prefixes):
                        continue
                    result["interfaces"].append({
                        "name": iface,
                        "ip": ip,
                    })
    except Exception as e:
        logger.warning("获取 IP 地址失败: %s", e)

    return result


async def check_network(targets: Optional[list[tuple[str, str]]] = None) -> list[dict]:
    """
    检查网络连通性，并行 ping 多个目标。

    Parameters
    ----------
    targets : list[tuple[str, str]]
        [(显示名, 主机), ...] 默认 [("百度", "baidu.com"), ("谷歌", "google.com")]

    Returns
    -------
    list[dict]
        每个目标的结果: {name, host, reachable, latency_ms, error}
    """
    if targets is None:
        targets = [("百度", "baidu.com"), ("谷歌", "google.com")]

    async def _ping_one(name: str, host: str) -> dict:
        result = {
            "name": name,
            "host": host,
            "reachable": False,
            "latency_ms": None,
            "error": None,
        }
        try:
            proc = await asyncio.create_subprocess_exec(
                "ping", "-c", "1", "-W", "3", host,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            if proc.returncode == 0:
                output = stdout.decode("utf-8", errors="replace")
                m = re.search(r"time[=<](\d+\.?\d*)", output)
                if m:
                    result["latency_ms"] = float(m.group(1))
                result["reachable"] = True
            else:
                result["error"] = "不通"
        except asyncio.TimeoutError:
            result["error"] = "超时"
        except Exception as e:
            result["error"] = f"检测失败: {e}"
        return result

    # 并行 ping 所有目标
    tasks = [_ping_one(name, host) for name, host in targets]
    return await asyncio.gather(*tasks)
