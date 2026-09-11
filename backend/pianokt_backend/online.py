import os
import psycopg
from psycopg.rows import dict_row

def connect():
    return psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,connect_timeout=10)

def owned_attempt(c,attempt,uid):
    return c.execute('select * from public.piano_attempts where id=%s and user_id=%s',(attempt,uid)).fetchone()
