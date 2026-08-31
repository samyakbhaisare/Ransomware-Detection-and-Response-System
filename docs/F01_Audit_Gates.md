# F01 Audit Gates

1. Core runtime lifecycle exists and is permanent.
2. States include STOPPED, STARTING, RUNNING, DEGRADED, SHUTTING_DOWN, FAILED.
3. Startup and shutdown transitions are real and observable.
4. Configuration initialization and validation happens on startup.
5. Logging initialization is configured and structured.
6. Persistence/database initialization is required and operational.
7. Subsystem registry and health tracking are extensible and truthful.
8. Runtime status reports actual runtime state, not placeholders.
9. Graceful shutdown, signal handling, cleanup, and failure handling work.
10. CLI commands status/start/stop reflect the actual state and reject invalid lifecycle operations.

## F01 status

All gates are implemented and revalidated in the test suite.
