"""Stop only the project-local PostgreSQL instance (never a system service)."""
import importlib.util
import subprocess
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
runtime = root / '.runtime'
data = runtime / 'pgdata'
port = 55432

# init-local.py owns the pg_ctl invocation, ownership checks and readiness probe.
spec = importlib.util.spec_from_file_location('init_local', Path(__file__).with_name('init-local.py'))
init_local = importlib.util.module_from_spec(spec)
spec.loader.exec_module(init_local)


def wait_stopped(timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        pid, _ = init_local.read_pid_file()
        image = init_local.process_image(pid) if pid else None
        if not init_local.tcp_ready() and image != 'postgres.exe':
            return True
        time.sleep(0.25)
    return False


def main():
    pid, recorded_dir = init_local.read_pid_file()
    if not pid:
        if init_local.tcp_ready():
            raise SystemExit(
                f'端口 {port} 正在监听，但没有本项目 .runtime\\pgdata\\postmaster.pid。'
                '无法确认归属，未停止任何进程。')
        print('本地 PostgreSQL 未运行。')
        return
    if not init_local.is_our_postmaster(pid, recorded_dir):
        # The PID was reused by another program; the stale file is ours to remove.
        (data / 'postmaster.pid').unlink(missing_ok=True)
        print(f'postmaster.pid 已过期（PID {pid} 不属于本项目），仅清理了该文件。')
        return

    try:
        init_local.run_pg_ctl('stop')
    except RuntimeError as exc:
        print(f'pg_ctl 停止命令失败：{exc}；尝试仅终止已确认归属的进程树。')

    if wait_stopped():
        print('本地 PostgreSQL 已停止。')
        return

    # pg_ctl may be blocked by an inherited restricted token. Only a postmaster whose
    # pid file and image were verified above may be force-terminated.
    subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                   capture_output=True, text=True)
    if wait_stopped(timeout=10):
        print(f'本地 PostgreSQL 已强制停止（PID {pid}）。数据目录保留，下次启动会做崩溃恢复。')
        return
    raise SystemExit(f'无法停止本项目的 PostgreSQL (PID {pid})。未删除数据库；请检查进程后重试。')


if __name__ == '__main__':
    main()
