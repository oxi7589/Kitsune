
"""
Add index to account table
"""
from yoyo import step
__depends__ = {"20210814_01_xwCee_add_role_column_to_accounts_table"}

steps = [
    step(
    "CREATE INDEX IF NOT EXISTS account_idx USING BTREE ON account(username, created_at, role);",

    "DROP INDEX IF EXISTS account_idx;"
    )
    ]
