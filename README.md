# RDRS CLI — F01 Runtime

This project implements the F01 core runtime for the RDRS CLI.

## Scope

This is the Core Runtime only. F02 and later features are intentionally not implemented.

## Runtime states

- STOPPED
- STARTING
- RUNNING
- DEGRADED
- SHUTTING_DOWN
- FAILED

## Supported CLI commands

- rdrs status
- rdrs start
- rdrs stop

## Runtime contract

- validates configuration before startup
- initializes logging and persistence
- maintains a subsystem registry and health state
- supports graceful shutdown and signal handling
- records startup and shutdown transitions in SQLite
- reports truthful lifecycle status

## Validation

Run:

```bash
python -m pytest tests/unit/test_runtime.py -q
```

and verify:

```bash
rdrs status
rdrs start
rdrs status
rdrs stop
rdrs status
```
