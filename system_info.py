import subprocess


def run(cmd):
    """Run a command and return stdout."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout
    except Exception as e:
        return f"ERROR running {cmd}: {e}"


def get_os_info():
    lines = []
    os_release = run(['cat', '/etc/os-release'])
    for line in os_release.splitlines():
        if '=' in line:
            key, _, value = line.partition('=')
            lines.append(f"{key.strip()}: {value.strip()}")
    return "\n".join(lines)


def get_cpu_info():
    info = {}
    with open('/proc/cpuinfo', 'r') as f:
        current = {}
        for line in f:
            if ':' in line:
                key, _, value = line.partition(':')
                current[key.strip()] = value.strip()
            elif not line.strip():
                if current:
                    info['cpu'] = current
                    current = {}
    return "\n".join(f"{k}: {v}" for k, v in info.get('cpu', {}).items())


def get_memory_info():
    meminfo = {}
    with open('/proc/meminfo', 'r') as f:
        for line in f:
            key, _, value = line.partition(':')
            meminfo[key.strip()] = value.strip()
    return "\n".join(f"{k}: {v}" for k, v in meminfo.items())


def get_disk_info():
    output = run(['df', '-h', '--output', 'source,size,used,avail,pcent,target'])
    lines = output.strip().splitlines()
    header = lines[0]
    rows = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) == 6:
            rows.append(f"  {parts[0]:<40} {parts[1]:>10} {parts[2]:>10} {parts[3]:>10} {parts[4]:>8} {parts[5]}")
    return "\n".join([header] + rows)


def get_network_info():
    output = run(['ip', '-brief', 'addr'])
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return "\n".join(lines)


def get_process_list():
    output = run(['ps', 'axo', 'pid,comm,%cpu,%mem,rss,size'])
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    header = lines[0]
    rows = []
    for line in lines[1:]:
        parts = line.split(None, 5)
        if len(parts) == 6:
            pid, comm, cpu, mem, rss, size = parts
            rows.append(f"  {pid:<8} {cpu:>6} {mem:>6} {rss:>7} {size:>10} {comm}")
    return "\n".join([header] + rows)


def main():
    print("=" * 60)
    print("SYSTEM INFORMATION REPORT")
    print("=" * 60)

    print("\n[OS]")
    os_info = get_os_info()
    for line in os_info.splitlines():
        print(line)

    print("\n[CPU]")
    cpu_info = get_cpu_info()
    for line in cpu_info.splitlines():
        print(line)

    print("\n[MEMORY]")
    mem_info = get_memory_info()
    for line in mem_info.splitlines():
        print(line)

    print("\n[DISK]")
    disk_info = get_disk_info()
    for line in disk_info.splitlines():
        print(line)

    print("\n[NETWORK]")
    net_info = get_network_info()
    for line in net_info.splitlines():
        print(line)

    print("\n[PROCESSES]")
    proc_info = get_process_list()
    for line in proc_info.splitlines():
        print(line)


if __name__ == '__main__':
    main()