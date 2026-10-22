"""Prepare and start the project-local PostgreSQL instance.

The server must not run with administrator privileges (PostgreSQL refuses it).
On a machine where the current shell is elevated, pg_ctl is therefore started
through the Windows "basic user" trust level. Data, .env and the database are
never recreated when they already exist.
"""
import ctypes
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
runtime = root / '.runtime'
bin_dir = runtime / 'pgsql' / 'bin'
data = runtime / 'pgdata'
log_file = runtime / 'postgres.log'
env_file = root / '.env'
port = 55432
# Crash recovery after an interrupted shutdown fsyncs the whole data directory and
# replays WAL; on a slow disk this can take minutes. Do not call that "not ready".
ready_timeout = 300


def tcp_ready(timeout=0.5):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=timeout):
            return True
    except OSError:
        return False


def read_pid_file():
    """Return (pid, recorded_data_dir) or (None, None)."""
    try:
        lines = (data / 'postmaster.pid').read_text(encoding='utf-8', errors='replace').splitlines()
        return int(lines[0]), (lines[1] if len(lines) > 1 else '')
    except (OSError, ValueError, IndexError):
        return None, None


def process_image(pid):
    try:
        out = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        if out and 'No tasks' not in out and '没有运行' not in out:
            return out.split('","')[0].strip('"').lower()
    except (OSError, subprocess.SubprocessError):
        pass
    # Some restricted sessions cannot start tasklist.exe; the Win32 API still
    # answers without spawning a process.
    try:
        import ctypes
        from ctypes import wintypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return None
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buffer))
            if ctypes.windll.kernel32.QueryFullProcessImageNameW(
                    handle, 0, buffer, ctypes.byref(size)):
                return Path(buffer.value).name.lower()
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 - unknown ownership is handled by the caller
        return None
    return None


def is_our_postmaster(pid, recorded_dir):
    if not pid or not recorded_dir:
        return False
    try:
        if Path(recorded_dir).resolve() != data.resolve():
            return False
    except OSError:
        return False
    return process_image(pid) == 'postgres.exe'


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def run_pg_ctl(action):
    """Run pg_ctl with a non-administrator token when necessary.

    Returns after the helper has been launched; the caller polls readiness.
    """
    script = runtime / 'pg-ctl.cmd'
    if action == 'start':
        command = f'"{bin_dir / "pg_ctl.exe"}" -D "{data}" -l "{log_file}" start'
    else:
        command = f'"{bin_dir / "pg_ctl.exe"}" -D "{data}" stop -m fast'
    # Keep the helper on disk: `runas` returns before the target cmd.exe has read it.
    script.write_text('@echo off\r\ncd /d "{}"\r\n{}\r\nexit /b %errorlevel%\r\n'.format(root, command),
                      encoding='mbcs')
    if is_admin():
        # PostgreSQL refuses an elevated token. The basic-user trust level keeps
        # the same user and file ownership while dropping administrator rights.
        completed = subprocess.run(
            ['runas.exe', '/trustlevel:0x20000', f'cmd.exe /c "{script}"'],
            capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or 'runas failed').strip())
    else:
        completed = subprocess.run(['cmd.exe', '/c', str(script)],
                                   capture_output=True, text=True, timeout=60)
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or 'pg_ctl failed').strip())


def log_tail(lines=15):
    try:
        content = log_file.read_text(encoding='utf-8', errors='replace').splitlines()
        return '\n'.join(content[-lines:])
    except OSError:
        return '(postgres.log 不可读)'


def ensure_env():
    if env_file.exists():
        return
    password = secrets.token_urlsafe(32)
    env_file.write_text(
        f'DATABASE_URL=postgresql+psycopg://fishingnet:{password}@127.0.0.1:55432/fishingnet\n'
        f'SETUP_TOKEN={secrets.token_urlsafe(32)}\n'
        f'MASTER_KEY={secrets.token_urlsafe(32)}\n', encoding='utf-8')
    print('已生成新的本地 .env；初始化口令保存在 .env 中，不会打印。')


def init_data_dir(password):
    if data.exists():
        return
    pwfile = runtime / 'pg-password.tmp'
    try:
        pwfile.write_text(password, encoding='utf-8')
        subprocess.run([str(bin_dir / 'initdb.exe'), '-D', str(data), '-U', 'fishingnet',
                        '--encoding=UTF8', '--locale=C', '--auth=scram-sha-256',
                        f'--pwfile={pwfile}'], check=True)
    finally:
        pwfile.unlink(missing_ok=True)
    with (data / 'postgresql.conf').open('a', encoding='utf-8') as handle:
        handle.write("\nlisten_addresses = '127.0.0.1'\nport = 55432\n")


def db_ready(password, timeout=2):
    """SQL-level readiness probe.

    ``pg_isready.exe`` can fail to start under a restricted token / low desktop
    heap (``0xc0000142``) even when the server is healthy. The application itself
    connects with psycopg, so use the same client library for the probe.
    """
    import psycopg
    try:
        with psycopg.connect(host='127.0.0.1', port=port, user='fishingnet',
                             password=password, dbname='postgres',
                             connect_timeout=timeout, autocommit=True):
            return True
    except Exception:  # noqa: BLE001 - "starting up" / refused / timeout all mean "retry"
        return False


def wait_ready(password, timeout=ready_timeout):
    deadline = time.time() + timeout
    started = time.time()
    last_note = -1
    while time.time() < deadline:
        if db_ready(password):
            return True
        elapsed = int(time.time() - started)
        if elapsed // 10 > last_note:
            last_note = elapsed // 10
            print(f'等待本地 PostgreSQL 完成启动/恢复… {elapsed}s（可能因上次未正常关闭而做崩溃恢复，请不要重复启动或停止）', flush=True)
        time.sleep(0.5)
    return False


def ensure_database(password):
    import psycopg
    with psycopg.connect(host='127.0.0.1', port=port, user='fishingnet', password=password,
                         dbname='postgres', connect_timeout=5, autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname='fishingnet'").fetchone():
            conn.execute('CREATE DATABASE fishingnet')


def main():
    if not (bin_dir / 'pg_ctl.exe').exists():
        raise SystemExit('未找到 .runtime\\pgsql\\bin\\pg_ctl.exe。请按 README 安装项目内 PostgreSQL 17 二进制。')
    runtime.mkdir(exist_ok=True)
    ensure_env()

    from urllib.parse import urlparse
    from dotenv import dotenv_values
    values = dotenv_values(env_file)
    if 'DATABASE_URL' not in values:
        raise SystemExit('.env 缺少 DATABASE_URL；未覆盖现有配置，请检查后重试。')
    parsed = urlparse(values['DATABASE_URL'])
    password = parsed.password
    if not password:
        raise SystemExit('DATABASE_URL 中没有数据库密码；未覆盖现有 .env。')

    init_data_dir(password)
    pid, recorded_dir = read_pid_file()

    if tcp_ready():
        if is_our_postmaster(pid, recorded_dir):
            if not wait_ready(password):
                raise SystemExit(
                    f'本地 PostgreSQL 在 {ready_timeout} 秒内仍未完成启动/崩溃恢复。\n'
                    '数据库未被修改；不要删除 pgdata，也不要在恢复期间反复停止/启动。\n'
                    f'--- postgres.log 末尾 ---\n{log_tail()}')
            ensure_database(password)
            print(f'本地 PostgreSQL 已就绪：127.0.0.1:{port}')
            return
        raise SystemExit(
            f'端口 {port} 已被其他程序占用，且不是本项目 .runtime\\pgdata 的 PostgreSQL。'
            '未启动或停止任何其他程序；请先释放该端口。')

    if pid and is_our_postmaster(pid, recorded_dir):
        # The postmaster is ours but has not opened its socket yet (still starting).
        if wait_ready(password):
            ensure_database(password)
            print(f'本地 PostgreSQL 已就绪：127.0.0.1:{port}')
            return
        raise SystemExit(
            f'本项目的 PostgreSQL 进程 (PID {pid}) 存在，但 {ready_timeout} 秒内未接受连接。\n'
            '请先等待日志出现 "database system is ready to accept connections"；'
            '若确实卡住，再运行 infra\\stop.ps1。不要删除 pgdata。\n'
            f'--- postgres.log 末尾 ---\n{log_tail()}')
    if pid:
        # A stale postmaster.pid (PID reused by another process) must not block a start.
        (data / 'postmaster.pid').unlink(missing_ok=True)

    try:
        run_pg_ctl('start')
    except RuntimeError as exc:
        raise SystemExit(f'PostgreSQL 启动命令失败：{exc}\n--- postgres.log 末尾 ---\n{log_tail()}')

    if not wait_ready(password):
        raise SystemExit(
            f'PostgreSQL 在 {ready_timeout} 秒内未接受连接（可能仍在崩溃恢复）。\n'
            '数据库未删除；请稍后直接重试启动，不要在恢复期间反复停止/启动。\n'
            f'--- postgres.log 末尾 ---\n{log_tail()}\n'
            '若端口被占用或日志显示权限错误，不要删除 pgdata；请把上面的日志发给开发者。')

    ensure_database(password)
    print(f'本地 PostgreSQL 已就绪：127.0.0.1:{port}')


if __name__ == '__main__':
    main()
