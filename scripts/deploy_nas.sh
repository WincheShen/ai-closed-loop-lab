#!/usr/bin/env bash
# 热部署 bind-mount 的 src/scripts/config 到 NAS /volume1/docker/ai-lab/
#
#   ./scripts/deploy_nas.sh            # 同步 + 重启 scheduler/web
#   ./scripts/deploy_nas.sh --no-restart
#   ./scripts/deploy_nas.sh --skip-watchdog
#
# 必须 COPYFILE_DISABLE=1，否则 macOS tar 会带 ._*.yaml 打挂 Config 加载。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

NAS_HOST="${NAS_HOST:-192.168.3.73}"
NAS_PATH="${NAS_PATH:-/volume1/docker/ai-lab}"
COMPOSE_FILE="$NAS_PATH/docker-compose.yml"
STAGE="/var/services/homes/kingSY_9/ai-lab-deploy.tgz"
SSH_NAS=(python3 "$ROOT/scripts/ssh_nas.py")

RESTART=1
INSTALL_WATCHDOG=1
for arg in "$@"; do
  case "$arg" in
    --no-restart) RESTART=0 ;;
    --skip-watchdog) INSTALL_WATCHDOG=0 ;;
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *)
      echo "unknown arg: $arg" >&2
      exit 2
      ;;
  esac
done

need() { command -v "$1" >/dev/null || { echo "need $1" >&2; exit 1; }; }
need python3
need tar

TMP="$(mktemp -t ai-lab-deploy.XXXXXX).tgz"
cleanup() { rm -f "$TMP"; }
trap cleanup EXIT

echo "==> packing src/ scripts/ config/ docker/docker-compose.nas.yml"
# 只打热更新所需目录；不打 data / .env / logs
COPYFILE_DISABLE=1 tar -czf "$TMP" \
  --exclude='.DS_Store' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='._*' \
  src scripts config docker/docker-compose.nas.yml
ls -lh "$TMP"

echo "==> upload $STAGE"
python3 - <<PY
import pathlib, subprocess, sys
sys.path.insert(0, "scripts")
from ssh_nas import get_keychain_password
user = get_keychain_password("ai-lab-nas-usernam", "nas_username")
password = get_keychain_password("ai-lab-nas-password", "nas_password")
tgz = pathlib.Path("$TMP")
cmd = [
    "sshpass", "-p", password, "ssh", "-o", "StrictHostKeyChecking=no",
    f"{user}@${NAS_HOST}",
    "cat > ${STAGE} && ls -lh ${STAGE}",
]
with tgz.open("rb") as fh:
    r = subprocess.run(cmd, stdin=fh)
sys.exit(r.returncode)
PY

echo "==> extract + strip AppleDouble"
"${SSH_NAS[@]}" --sudo -c "sh -c '
set -e
cd ${NAS_PATH}
tar -xzf ${STAGE}
find ${NAS_PATH}/src ${NAS_PATH}/scripts ${NAS_PATH}/config -name \"._*\" -type f -delete
test -f src/graph/guards.py
test -f scripts/scheduler.py
test -f scripts/scheduler_watchdog.py
echo extract_ok
'"

if [[ "$INSTALL_WATCHDOG" -eq 1 ]]; then
  echo "==> install watchdog cron (every 5 min)"
  "${SSH_NAS[@]}" --sudo -c "sh -c '
set -e
mkdir -p ${NAS_PATH}/logs
touch ${NAS_PATH}/logs/watchdog.log
LINE=\"*/5\t*\t*\t*\t*\troot\tcd ${NAS_PATH} && DATA_DIR=${NAS_PATH}/data OPS_ALERT_LOG=${NAS_PATH}/data/logs/ops_alerts.log /usr/bin/python3 ${NAS_PATH}/scripts/scheduler_watchdog.py >> ${NAS_PATH}/logs/watchdog.log 2>&1\"
if [ -d /etc/cron.d ]; then
  printf \"%s\\n\" \"\$LINE\" > /etc/cron.d/ai-lab-watchdog
  chmod 644 /etc/cron.d/ai-lab-watchdog
  echo watchdog_crond_ok
elif [ -f /etc/crontab ] && ! grep -q scheduler_watchdog /etc/crontab; then
  printf \"%s\\n\" \"\$LINE\" >> /etc/crontab
  echo watchdog_crontab_ok
else
  echo watchdog_already_or_manual
fi
'" || echo "WARN: cron 写入失败，请在群晖任务计划里每 5 分钟跑 scripts/scheduler_watchdog.py"
fi

if [[ "$RESTART" -eq 1 ]]; then
  echo "==> restart web + scheduler (no image rebuild)"
  "${SSH_NAS[@]}" --sudo -c "sh -c '
set -e
/usr/local/bin/docker-compose -f ${COMPOSE_FILE} up -d
/usr/local/bin/docker-compose -f ${COMPOSE_FILE} --profile scheduler up -d
/usr/local/bin/docker ps --filter name=ai-lab --format \"{{.Names}} {{.Status}}\"
'"
  echo "==> wait heartbeat"
  sleep 8
  "${SSH_NAS[@]}" --sudo -c "sh -c '
stat ${NAS_PATH}/data/scheduler.heartbeat || true
python3 -c \"
import time, pathlib
p=pathlib.Path(\"${NAS_PATH}/data/scheduler.heartbeat\")
print(\"age\", round(time.time()-p.stat().st_mtime,1) if p.exists() else \"missing\")
\"
'"
fi

echo "done."
