"""Start the two loopback pages and restore saved scan plans (disabled initially)."""
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def healthy(port):
    try:
        import json
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=2) as response:
            data = json.load(response)
            return data.get('service') == 'anomaly-workbench' and data.get('version') in ('2.7', '2.8')
    except (OSError, ValueError):
        return False


def main():
    from timezone_support import ensure_timezone_data
    ensure_timezone_data()
    if not all(healthy(p) for p in (8767, 8768)):
        import socket
        for port in (8767, 8768):
            with socket.socket() as probe:
                probe.settimeout(1)
                if probe.connect_ex(('127.0.0.1', port)) == 0:
                    raise RuntimeError(f'端口{port}被旧版或其他服务占用；请核实进程并关闭后重启，避免连接错误服务。')
        logs = ROOT / 'logs'
        logs.mkdir(exist_ok=True)
        with (logs / 'dashboard.out.log').open('ab') as output, (logs / 'dashboard.err.log').open('ab') as error:
            process = subprocess.Popen([sys.executable, '-B', '-X', 'utf8', str(ROOT / 'server.py')], cwd=ROOT,
                                       stdin=subprocess.DEVNULL, stdout=output, stderr=error,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        print(f'Started workbench: PID {process.pid}', flush=True)
        for _ in range(25):
            if all(healthy(p) for p in (8767, 8768)):
                break
            time.sleep(.3)
        else:
            raise RuntimeError(f'本地后台未就绪，请查看{logs}')
    print('Ready: http://127.0.0.1:8767/', flush=True)
    print('Ready: http://127.0.0.1:8768/prototype.html', flush=True)
    print('自动扫描首次默认关闭；后续启动按已保存扫描计划恢复。不会调用AI或实盘交易。', flush=True)


if __name__ == '__main__':
    main()
