"""Connect server custom modules."""

from . import debugger

debugger.listen_if_enabled()
