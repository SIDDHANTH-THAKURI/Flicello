"""How many CPUs this process may really use.

os.cpu_count() reports the whole machine. In a container that is the host: a cloud renderer given 16 vCPUs saw 24,
and every drawing process sized its threads from that, so they fought over the cores. The container's limit (its
cgroup CPU quota) and the CPUs the process may run on are what count.
"""

from __future__ import annotations

import os
from functools import lru_cache


def _quota() -> int | None:
    try:  # cgroup v2: "<quota> <period>" or "max <period>"
        with open("/sys/fs/cgroup/cpu.max", encoding="ascii") as fh:
            quota, period = fh.read().split()[:2]
        if quota != "max":
            return max(1, round(int(quota) / int(period)))
        return None
    except (OSError, ValueError):
        pass
    try:  # cgroup v1
        with open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us", encoding="ascii") as fh:
            quota = int(fh.read())
        with open("/sys/fs/cgroup/cpu/cpu.cfs_period_us", encoding="ascii") as fh:
            period = int(fh.read())
        return max(1, round(quota / period)) if quota > 0 else None
    except (OSError, ValueError):
        return None


@lru_cache(maxsize=1)
def usable_cpus() -> int:
    if os.environ.get("VIDO_CPUS", "").isdigit():  # what this machine is paid for (a cloud renderer may burst past
        return max(1, int(os.environ["VIDO_CPUS"]))  # its request, and that is billed too)
    try:
        allowed = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):  # Windows, macOS
        allowed = os.cpu_count() or 4
    quota = _quota()
    return max(1, min(allowed, quota) if quota else allowed)
