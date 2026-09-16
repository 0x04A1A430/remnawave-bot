from contextvars import ContextVar


admin_output_enabled: ContextVar[bool] = ContextVar('admin_output_enabled', default=False)
