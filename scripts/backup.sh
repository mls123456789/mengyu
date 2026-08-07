#!/usr/bin/env bash
# mengyu.db 在线备份（WAL 模式下安全；切勿直接 cp，可能拷到不一致快照）。
#
# 用法：scripts/backup.sh
# 定时：crontab -e 加一行（每天凌晨 3:17，避开整点）：
#   17 3 * * * /opt/mengyu/scripts/backup.sh >> /opt/mengyu/backups/backup.log 2>&1
#
# 依赖：sqlite3 CLI。环境变量可覆盖：DB_PATH / BACKUP_DIR / KEEP_DAYS。
set -euo pipefail
cd "$(dirname "$0")/.."

DB_PATH="${DB_PATH:-./data/mengyu.db}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
KEEP_DAYS="${KEEP_DAYS:-30}"

mkdir -p "$BACKUP_DIR"
stamp="$(date +%Y%m%d-%H%M%S)"
sqlite3 "$DB_PATH" ".backup '$BACKUP_DIR/mengyu-$stamp.db'"

# 只清理本脚本生成的备份，保留最近 KEEP_DAYS 天
find "$BACKUP_DIR" -name 'mengyu-*.db' -mtime +"$KEEP_DAYS" -delete

echo "backup ok: $BACKUP_DIR/mengyu-$stamp.db"
