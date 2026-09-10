"""Conformance kits for this project's ports.

Shipping the job-store contract with the package is deliberate: the local suite and the
opt-in cloud suite must run the same checks against the same state machine, and a suite
that lives under one test tree cannot be imported by the other.
"""
