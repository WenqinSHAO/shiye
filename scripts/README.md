# Scripts

Utilities to operate on Shiye data without digging into other docs.

## backup_restore.py

Back up and restore the primary storage files (`shiye.db`, `shiye.faiss`).

Defaults:
- Data dir: `SHIYE_DATA_DIR` or `~/.shiye`
- Backup dest: `<data_dir>/backups/backup-YYYYMMDD-HHMMSS/`

Usage:
```bash
# Backup to default location
python scripts/backup_restore.py backup

# Backup to a custom folder
python scripts/backup_restore.py --data-dir /path/to/data backup --dest /tmp/shiye-backup

# Restore from a backup folder
python scripts/backup_restore.py --data-dir /path/to/data restore /tmp/shiye-backup
```
