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


async def check_network(
    targets: Optional[list[tuple]] = None,
    proxy: str = "",
) -> list[dict]:
    """
    检查网络连通性。

    Parameters
    ----------
    targets : list[tuple]
        每个元素为 (显示名, 主机, 方法)。
        方法: "ping"（ICMP，不支持代理）或 "curl"（HTTP，支持代理）。
        默认: 百度用 ping（国内直连），谷歌用 curl 走代理。
    proxy : str
        代理地址，仅 curl 方法使用，如 "http://127.0.0.1:7890"

    Returns
    -------
    list[dict]
        每个目标: {name, host, reachable, latency_ms, error}
    """
    if targets is None:
        targets = [
            ("百度", "baidu.com", "ping"),
            ("谷歌", "www.google.com", "curl"),
        ]

    async def _check_ping(name: str, host: str) -> dict:
        result = {"name": name, "host": host, "reachable": False, "latency_ms": None, "error": None}
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

    async def _check_curl(name: str, host: str) -> dict:
        result = {"name": name, "host": host, "reachable": False, "latency_ms": None, "error": None}
        cmd = ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}:%{time_total}",
               "--connect-timeout", "5", f"https://{host}"]
        if proxy:
            cmd += ["-x", proxy]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode == 0:
                output = stdout.decode("utf-8", errors="replace").strip()
                parts = output.split(":")
                http_code = int(parts[0]) if parts[0].isdigit() else 0
                time_total = float(parts[1]) if len(parts) > 1 else 0.0
                if http_code in range(200, 400):
                    result["reachable"] = True
                    result["latency_ms"] = round(time_total * 1000, 1)
                else:
                    result["error"] = f"HTTP {http_code}"
            else:
                result["error"] = "不通"
        except asyncio.TimeoutError:
            result["error"] = "超时"
        except Exception as e:
            result["error"] = f"检测失败: {e}"
        return result

    async def _check_one(name: str, host: str, method: str) -> dict:
        if method == "curl":
            return await _check_curl(name, host)
        return await _check_ping(name, host)

    tasks = [_check_one(name, host, method) for name, host, method in targets]
    return await asyncio.gather(*tasks)
