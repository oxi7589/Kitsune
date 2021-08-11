from flask import g, current_app
from psycopg2_pool import ThreadSafeConnectionPool
from psycopg2.extensions import make_dsn
from psycopg2.extras import RealDictCursor
from os import getenv
import traceback
import config

pool = None

def init():
    global pool
    try:
        pool = ThreadSafeConnectionPool(minconn=0, maxconn=5000, idle_timeout=300,
            dsn=make_dsn(
                host = config.database_host,
                dbname = config.database_dbname,
                user = config.database_user,
                password = config.database_password,
                port = 5432
            )
        )
    except Exception as e:
        print(f'Failed to connect to the database: {e}')
    return pool

def get_pool():
    global pool
    return pool

def get_cursor():
    if 'cursor' not in g:
        g.connection = get_conn()
        g.cursor = g.connection.cursor()
    return g.cursor

def get_raw_conn():
    conn = pool.getconn()
    conn.cursor_factory = RealDictCursor
    return conn

def get_conn():
    if 'connection' not in g:
        g.connection = pool.getconn()
        g.connection.cursor_factory = RealDictCursor
    return g.connection

def return_conn(conn):
    if conn is not None:
        pool.putconn(conn)
