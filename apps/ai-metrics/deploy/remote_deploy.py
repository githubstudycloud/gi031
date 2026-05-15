"""Paramiko 远程部署脚本（密码 SSH）。

用法（在 dev 机本仓库根目录）：
    cd apps/ai-metrics
    SSH_PASSWORD=xxx .venv/Scripts/python.exe deploy/remote_deploy.py \
        --host 192.168.0.132 --user ubuntu \
        --mode native --db sqlite

参数：
    --host       目标 IP
    --user       SSH 用户名（用 sudo 的）
    --mode       'native' (systemd) 或 'docker' (compose)
    --db         'sqlite' / 'mysql' / 'postgres' （native 模式用）
    --no-seed    不灌假数据（升级现有部署）

密码从环境变量 SSH_PASSWORD 取（避免入仓库 / 历史 / log）。
"""
from __future__ import annotations
import argparse
import os
import sys
import tarfile
import tempfile
import time
from pathlib import Path

import paramiko


DEFAULT_REMOTE_DIR = "/home/ubuntu/ai-metrics"


def make_tarball(src_dir: Path) -> Path:
    """打包 apps/ai-metrics（不含 .venv / __pycache__ / *.db / 截图）"""
    exclude_dirs = {".venv", "__pycache__", "node_modules", ".pytest_cache"}
    exclude_suffixes = {".db", ".png", ".pyc"}

    def _filter(tarinfo: tarfile.TarInfo):
        parts = Path(tarinfo.name).parts
        if any(p in exclude_dirs for p in parts): return None
        if Path(tarinfo.name).suffix in exclude_suffixes: return None
        if "_screenshot_" in tarinfo.name: return None
        return tarinfo

    tmp = Path(tempfile.gettempdir()) / "ai-metrics.tar.gz"
    with tarfile.open(tmp, "w:gz") as t:
        t.add(src_dir, arcname="ai-metrics", filter=_filter)
    return tmp


def run(ssh: paramiko.SSHClient, cmd: str, *, sudo: bool = False, password: str | None = None) -> tuple[int, str, str]:
    """跑远端命令，返回 (exit_code, stdout, stderr)。sudo 时把密码写到 stdin。"""
    if sudo:
        cmd = "sudo -S -p '' bash -c " + repr(cmd)
    print(f"$ {cmd[:160]}{'…' if len(cmd) > 160 else ''}")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=600, get_pty=False)
    if sudo and password:
        stdin.write(password + "\n"); stdin.flush()
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    if out: print(out[-2000:])
    if err: print("STDERR:", err[-2000:], file=sys.stderr)
    return code, out, err


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--user", default="ubuntu")
    ap.add_argument("--mode", choices=["native", "docker"], default="native")
    ap.add_argument("--db",   choices=["sqlite", "mysql", "postgres"], default="sqlite")
    ap.add_argument("--no-seed", action="store_true")
    ap.add_argument("--remote-dir", default=DEFAULT_REMOTE_DIR)
    args = ap.parse_args()

    password = os.environ.get("SSH_PASSWORD")
    if not password:
        print("❌ SSH_PASSWORD env not set", file=sys.stderr)
        sys.exit(2)

    src = Path(__file__).parent.parent.resolve()    # apps/ai-metrics/
    print(f"== 1) 打包 {src}")
    tar = make_tarball(src)
    print(f"   → {tar} ({tar.stat().st_size:,} bytes)")

    print(f"== 2) SSH 连接 {args.user}@{args.host}")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(args.host, username=args.user, password=password,
                timeout=15, banner_timeout=15, auth_timeout=15)

    sftp = ssh.open_sftp()
    remote_tar = "/tmp/ai-metrics.tar.gz"
    print(f"== 3) SCP → {remote_tar}")
    sftp.put(str(tar), remote_tar)
    sftp.close()

    run(ssh, f"rm -rf {args.remote_dir} && mkdir -p {args.remote_dir}")
    run(ssh, f"tar -xzf {remote_tar} -C /tmp && mv /tmp/ai-metrics/* {args.remote_dir}/ && ls {args.remote_dir}")
    run(ssh, f"chmod +x {args.remote_dir}/deploy/install_native.sh")

    if args.mode == "native":
        db_url = {
            "sqlite":   f"sqlite:///{args.remote_dir}/ai_metrics.db",
            "mysql":    "mysql+pymysql://ai_metrics:aimpwd@127.0.0.1:3306/ai_metrics?charset=utf8mb4",
            "postgres": "postgresql+psycopg://ai_metrics:aimpwd@127.0.0.1:5432/ai_metrics",
        }[args.db]
        # install_native.sh 是 sudo 起作用的；改个变体：直接在 ubuntu home 用 venv 跑
        print("== 4) 本目录 venv + uvicorn 启动（避免 sudo 改系统）")
        cmds = [
            f"cd {args.remote_dir} && python3 --version || sudo -S apt-get install -y python3 python3-venv",
            f"cd {args.remote_dir} && python3 -m venv .venv",
            f"cd {args.remote_dir} && .venv/bin/pip install --upgrade pip --quiet",
            f"cd {args.remote_dir} && .venv/bin/pip install -e . --quiet",
            f"echo 'DATABASE_URL={db_url}' > {args.remote_dir}/.env",
            f"echo 'CORS_ORIGINS=*' >> {args.remote_dir}/.env",
        ]
        for c in cmds:
            code, _, _ = run(ssh, c)
            if code != 0:
                print(f"❌ 失败: exit {code}"); sys.exit(code)

        if not args.no_seed:
            run(ssh, f"cd {args.remote_dir} && set -a; source .env; set +a; .venv/bin/python -m app.seed.fake_data --days 30 --reset")

        print("== 5) 杀掉旧实例并以 nohup 启动")
        run(ssh, "pkill -f 'app.main:app' || true")
        run(ssh, f"cd {args.remote_dir} && nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001 > {args.remote_dir}/uvicorn.log 2>&1 &", )
        time.sleep(2)
        run(ssh, "ss -tlnp 2>/dev/null | grep 8001 || true")

        print("== 6) 健康检查")
        run(ssh, "curl -s --max-time 5 http://127.0.0.1:8001/api/healthz")
        run(ssh, "curl -s --max-time 5 http://127.0.0.1:8001/api/reports/ai_metrics/config | head -c 300")

    elif args.mode == "docker":
        run(ssh, f"cd {args.remote_dir} && docker compose -f deploy/docker-compose.yml up -d --build", sudo=True, password=password)
        time.sleep(10)
        run(ssh, f"cd {args.remote_dir} && docker compose -f deploy/docker-compose.yml exec -T api python -m app.seed.fake_data --days 30 --reset", sudo=True, password=password)
        run(ssh, "curl -s --max-time 5 http://127.0.0.1:8001/api/healthz")

    ssh.close()
    print("\n✅ 部署完成 — 访问 http://%s:8001/ui/" % args.host)


if __name__ == "__main__":
    main()
