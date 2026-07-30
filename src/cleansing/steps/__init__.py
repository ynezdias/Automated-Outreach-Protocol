"""Cleansing pipeline step Lambdas — one module per step, independently invocable.

Common event contract (every step): ``{"bucket": str, "key": str, "run_id": str}``
where ``key`` is the step's input object. Every handler returns at least
``{"bucket", "key", "run_id", "rows_in", "rows_out"}`` with ``key`` pointing at
its output object, so Step Functions chains steps by passing each handler's
return value as the next handler's event — and any single step can be rerun in
isolation by hand-crafting that same shape.
"""
