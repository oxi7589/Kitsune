"""
Add role column to account table
"""
from yoyo import step
__depends__ = {'20210322_01_In37S-add-account-field'}

steps = [
    step(
    "ALTER TABLE account ADD COLUMN  role varchar DEFAULT 'consumer';",

    "ALTER TABLE account DROP COLUMN role;"
    )
]
