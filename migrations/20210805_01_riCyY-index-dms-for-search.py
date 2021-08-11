"""
Index DMs for search
"""

from yoyo import step

__depends__ = {'20210729_01_jopn6-add-contributor-id-column-to-dm-table'}

steps = [
    step(
        "CREATE INDEX IF NOT EXISTS dm_search_idx ON dms USING GIN (to_tsvector('english', content))",
        "DROP INDEX dm_search_idx"
    )
]