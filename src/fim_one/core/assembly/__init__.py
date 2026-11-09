"""The assembly layer: one place for the rules every entry point applies.

Chat, sub-agents, workflow nodes, schedules, trigger keys and channel
callbacks all decide the same five things before a resource is used: who is
acting, what they may see, which credential applies, whether a human must
confirm, and whether quota allows it. Those rules live here so that a new
entry point is a new caller, not a new copy.

Import rules (``dev/C1-assembly-layer.md`` §2.1):

- Nothing in this package imports ``fim_one.web``, not even inside a
  function. Data only the web layer has arrives through a port the web layer
  registers at start-up.
- ORM models are imported inside functions, and this package is not
  re-exported from ``fim_one.core``: ``db/models/channel.py`` imports
  ``core.security`` at module level, so a top-level import either way is a
  cycle.

Import the submodules directly (``fim_one.core.assembly.visibility`` …).
"""
