"""Top-level infra package — Phase-7 distributed-execution primitives.

`infra/distributed/` houses the worker pool, task scheduler, shard
manager and load balancer. Nothing inside `infra/` mutates Phase 1-6
behavior; it composes those layers behind a horizontal-scale facade.
"""
