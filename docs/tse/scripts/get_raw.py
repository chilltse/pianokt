import json
import os
import subprocess
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

# 读取本地环境变量
load_dotenv("/Users/tse/my_code/pianokt/backend/.env.local")

database_url = os.environ["DATABASE_URL"]
raw_root = os.environ["RAW_ROOT"].rstrip("/")
environment = os.getenv("SOURCE_ENV") or os.getenv("PIANOKT_ENV", "development")

# 查询最新一组已完成处理的录音
with psycopg.connect(database_url, row_factory=dict_row) as conn:
    row = conn.execute("""
        SELECT
            id, song_id, metadata, finalization,
            reference_key,
            performance_key,
            performance_events_key
        FROM public.piano_attempts
        WHERE source_environment = %s
          AND status = 'READY'
          AND performance_events_key IS NOT NULL
        ORDER BY created_at DESC, id DESC
        LIMIT 1
    """, (environment,)).fetchone()

if row is None:
    raise SystemExit(f"没有找到 {environment} 环境下符合条件的 READY Attempt")

# Save replay context beside the immutable inputs. No credentials/user IDs.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'backend'))
from pianokt_backend.attempt_context import attempt_context
from pianokt_backend.worker import _alignment_window

# 下载三份文件
output = Path("output/attempts") / str(row["id"])
output.mkdir(parents=True, exist_ok=True)

context = attempt_context(row)
settings = (row.get('metadata') or {}).get('settings') or {}
context['observed_end_sec'], context['reference_origin_sec'] = _alignment_window(settings)
(output / 'attempt-context.json').write_text(json.dumps(context, ensure_ascii=False, indent=2) + '\n')

files = {
    "reference.mid": row["reference_key"],
    "performance.mid": row["performance_key"],
    "performance-events.json": row["performance_events_key"],
}

for filename, key in files.items():
    subprocess.run([
        "gcloud", "storage", "cp",
        f"{raw_root}/{key}",
        str(output / filename),
    ], check=True)

print(f"\nAttempt: {row['id']}")
print(f"Environment: {environment}")
print(f"下载完成: {output.resolve()}")