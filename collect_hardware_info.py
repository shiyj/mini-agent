import os
import subprocess
import platform


def run_cmd(cmd):
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=15
        )
        return result.stdout.strip()
    except Exception as e:
        return "ERROR: " + str(e)


# 1. Operating System name (from /etc/os-release or platform)
os_release_path = '/etc/os-release'
os_name = 'unknown'
if os.path.exists(os_release_path):
    with open(os_release_path, 'r') as f:
        content = f.read()
    for line in content.splitlines():
        if '=' in line:
            key, _, value = line.partition('=')
            if key.strip() == 'PRETTY_NAME':
                os_name = value.strip().strip('"')
if not os_name or os_name == 'unknown':
    os_name = platform.platform()

# 2. CPU model (from lscpu or /proc/cpuinfo)
cpu_model = run_cmd("lscpu | grep -i 'model name' | head -1")
if not cpu_model:
    with open('/proc/cpuinfo', 'r') as f:
        for line in f.readlines():
            if line.startswith('model name'):
                cpu_model = line.split(':', 1)[1].strip()
                break

# 3. GPU model (try nvidia-smi, then lspci)
gpu_model = run_cmd("nvidia-smi -L")
if not gpu_model or "ERROR" in gpu_model:
    gpu_model = run_cmd("lspci | grep -i 'vga\\|3d\\|display'")
if not gpu_model:
    gpu_model = "无法获取（未检测到 GPU 或相关命令不可用）"

# 4. Memory size from /proc/meminfo
mem_info = {}
with open('/proc/meminfo', 'r') as f:
    for line in f.readlines():
        parts = line.split(':')
        if len(parts) == 2:
            key = parts[0].strip()
            val = parts[1].strip().split()[0]
            mem_info[key] = int(val)

mem_kb = mem_info.get('MemTotal', 0)
mem_mb = mem_kb / 1024
mem_gb = mem_mb / 1