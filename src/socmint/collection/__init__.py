"""Platform collectors.

Each collector returns normalised ``Post`` objects plus explicit status
information. Collectors never raise for a single bad source -- they report a
stage status so the run can continue and the gap stays visible.
"""
