"""Modules for manipulating internal Synapse logic using monkey patching."""

# Callbacks to be called during the lifecycle of a room.
# See people_conversations/__init__.py for a usage example.
before_create_room_callbacks = []
after_create_room_callbacks = []
on_create_room_errbacks = []
