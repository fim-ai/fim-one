"""The usage ledger: what was consumed, counted where it happens, stored once.

A model client reports each call to the meter of the scope it ran in
(:mod:`fim_one.core.model.usage`). An entry point opens that scope with
:func:`fim_one.core.usage.meter.metered` (or ``open_meter``) and says whose
it is; at the scope's boundaries the meter's totals become rows of
``usage_ledger``, written only by :mod:`.ledger`. Quota and the usage
figures read the ledger; nothing updates or deletes its rows.
"""
