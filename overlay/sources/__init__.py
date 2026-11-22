"""Chat sources. Each one runs in its own thread and posts plain dicts
({"src": ..., "kind": ..., ...}) to the hub through an `emit` callback."""
