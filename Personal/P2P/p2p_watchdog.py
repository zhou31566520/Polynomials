# ============================================================
# P2P Watchdog —— 监控 Git 仓库新版本 → 自动 docker compose up -d --build
# 运行方式: 宿主机直接跑  或  作为容器服务（需要挂载 /var/run/docker.sock）
# ============================================================
import os
import sys
import time
import subprocess
import logging

# ---------- 配置 ----------
GIT_REPO       = os.environ.get("GIT_REPO",       "https://github.com/zhou31566520/Polynomials")
GIT_BRANCH     = os.environ.get("GIT_BRANCH",     "master")
POLL_INTERVAL  = int(os.environ.get("POLL_INTERVAL", "30"))
COMPOSE_DIR    = os.environ.get("COMPOSE_DIR",     os.path.dirname(os.path.abspath(__file__)))
LOCAL_CLONE    = os.environ.get("LOCAL_CLONE",     "/tmp/p2p-git-watch")
WATCH_FILES    = [  # 这些文件有改动才触发 rebuild
    "P2P/p2p_ws_server.py",
    "P2P/p2p_web.html",
    "P2P/Dockerfile.ws",
    "P2P/Dockerfile.web",
    "P2P/docker-compose.yml",
    "P2P/nginx.conf",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("watchdog")


def run(cmd: list, cwd: str = None, check: bool = True) -> subprocess.CompletedProcess:
    """执行命令并实时输出"""
    log.info(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.stdout:
        for line in result.stdout.strip().splitlines():
            log.info(f"  │ {line}")
    if result.stderr:
        for line in result.stderr.strip().splitlines():
            log.warning(f"  │ {line}")
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed (exit={result.returncode}): {' '.join(cmd)}")
    return result


def ensure_repo() -> None:
    """确保本地有 git 仓库 clone 用于 fetch"""
    if not os.path.isdir(os.path.join(LOCAL_CLONE, ".git")):
        log.info(f"首次 clone {GIT_REPO} (branch={GIT_BRANCH}) → {LOCAL_CLONE}")
        run(["git", "clone", "--depth", "50", "--single-branch",
             "--branch", GIT_BRANCH, GIT_REPO, LOCAL_CLONE])


def get_local_head() -> str:
    """获取本地当前 HEAD commit"""
    result = run(["git", "rev-parse", "HEAD"], cwd=LOCAL_CLONE)
    return result.stdout.strip()


def get_remote_head() -> str:
    """fetch 后获取远端最新 commit"""
    run(["git", "fetch", "--depth", "50", "origin", GIT_BRANCH], cwd=LOCAL_CLONE)
    result = run(["git", f"origin/{GIT_BRANCH}", "-s", "--format=%H"], cwd=LOCAL_CLONE)
    return result.stdout.strip()


def get_changed_files(old_head: str, new_head: str) -> list:
    """列出两个 commit 之间变更的文件"""
    result = run(["git", "diff", "--name-only", old_head, new_head], cwd=LOCAL_CLONE, check=False)
    return result.stdout.strip().splitlines() if result.stdout.strip() else []


def has_relevant_changes(changed: list) -> bool:
    """变更文件是否命中我们关心的范围"""
    for f in changed:
        for watch in WATCH_FILES:
            if f.endswith(watch) or f == watch:
                return True
    return False


def deploy() -> None:
    """执行 docker compose build + up -d"""
    log.info("🚀 开始重新构建部署...")
    try:
        run(["docker", "compose", "build", "--no-cache"], cwd=COMPOSE_DIR)
        run(["docker", "compose", "up", "-d"],           cwd=COMPOSE_DIR)
        log.info("✅ 部署完成")
    except Exception as e:
        log.error(f"❌ 部署失败: {e}")


def main():
    log.info("=" * 60)
    log.info("🐕 P2P Watchdog 启动")
    log.info(f"  仓库: {GIT_REPO}")
    log.info(f"  分支: {GIT_BRANCH}")
    log.info(f"  轮询间隔: {POLL_INTERVAL}s")
    log.info(f"  Compose 目录: {COMPOSE_DIR}")
    log.info(f"  监控文件: {WATCH_FILES}")
    log.info("=" * 60)

    ensure_repo()
    last_head = get_local_head()
    log.info(f"初始 HEAD: {last_head[:8]}")

    # 启动时先部署一次（确保是最新）
    deploy()

    while True:
        time.sleep(POLL_INTERVAL)
        try:
            remote_head = get_remote_head()
            if remote_head == last_head:
                continue  # 无新版本

            log.info(f"🆕 检测到新版本! {last_head[:8]} → {remote_head[:8]}")
            changed = get_changed_files(last_head, remote_head)
            log.info(f"  变更文件 ({len(changed)}):")
            for f in changed:
                log.info(f"    - {f}")

            if has_relevant_changes(changed):
                log.info("  ✅ 命中 P2P 相关文件，触发部署")
                run(["git", "pull", "--ff-only", "origin", GIT_BRANCH], cwd=LOCAL_CLONE, check=False)
                last_head = remote_head
                deploy()
            else:
                log.info("  ⏭️ 变更未命中 P2P 相关文件，跳过部署")
                last_head = remote_head

        except KeyboardInterrupt:
            log.info("🛑 收到中断信号，退出")
            sys.exit(0)
        except Exception as e:
            log.error(f"⚠️ 轮询出错: {e}")


if __name__ == "__main__":
    main()