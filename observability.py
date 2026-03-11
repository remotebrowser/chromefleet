from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import logfire
import sentry_sdk
import yaml
from fastapi import FastAPI
from loguru import logger
from rich.logging import RichHandler
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

if TYPE_CHECKING:
    from loguru import HandlerConfig, Record


def _settings():
    from chromefleet import settings  # late import to avoid circular dependency

    return settings


def instrument_fastapi(app: FastAPI):
    if not _settings().LOGFIRE_TOKEN:
        return
    logfire.instrument_fastapi(app, capture_headers=True, excluded_urls="/health")


def setup_logging():
    _setup_logfire()
    _setup_logger()
    _setup_sentry()


def _setup_logfire():
    if not _settings().LOGFIRE_TOKEN:
        logger.warning("Logfire is disabled, no LOGFIRE_TOKEN provided")
        return

    logger.info("Initializing Logfire")
    logfire.configure(
        service_name="lambda-chromefleet",
        send_to_logfire="if-token-present",
        token=_settings().LOGFIRE_TOKEN,
        environment=_settings().ENVIRONMENT,
        code_source=logfire.CodeSource(repository="https://github.com/remotebrowser/chromefleet", revision="main"),
        distributed_tracing=True,
        console=False,
        scrubbing=False,
    )


def _setup_logger():
    logger.remove()

    rich_handler = RichHandler(rich_tracebacks=True, log_time_format="%X", markup=True)

    def _format_with_extra(record: "Record") -> str:
        message = record["message"]

        if record["extra"]:
            extra = yaml.dump(record["extra"], sort_keys=False, default_flow_style=False)
            message = f"{message}\n{extra}"

        return message.replace("[", r"\[").replace("{", "{{").replace("}", "}}").replace("<", r"\<")

    handlers: list[HandlerConfig] = [
        {
            "sink": rich_handler,
            "format": _format_with_extra,
            "level": _settings().LOG_LEVEL,
            "backtrace": True,
            "diagnose": True,
        }
    ]

    if _settings().LOGFIRE_TOKEN:
        logfire_handler = logfire.loguru_handler()
        logfire_handler["level"] = _settings().LOG_LEVEL  # Match the log level with other handlers
        handlers.append(logfire_handler)

    logger.configure(handlers=handlers)

    # Override the loggers of external libraries to ensure consistent formatting
    for logger_name in (
        "uvicorn",
        "uvicorn.access",
        "uvicorn.error",
    ):
        lib_logger = logging.getLogger(logger_name)
        lib_logger.setLevel(_settings().LOG_LEVEL)
        lib_logger.handlers.clear()
        lib_logger.addHandler(rich_handler)
        lib_logger.propagate = False


def _setup_sentry():
    if not _settings().SENTRY_DSN:
        logger.warning("Sentry is disabled, no SENTRY_DSN provided")
        return

    logger.info("Initializing Sentry")
    sentry_sdk.init(
        dsn=_settings().SENTRY_DSN,
        environment=_settings().ENVIRONMENT,
        integrations=[
            StarletteIntegration(
                transaction_style="endpoint",
                failed_request_status_codes={403, *range(500, 599)},
            ),
            FastApiIntegration(
                transaction_style="endpoint",
                failed_request_status_codes={403, *range(500, 599)},
            ),
            LoggingIntegration(level=logging.getLevelNamesMapping()[_settings().LOG_LEVEL]),
        ],
        send_default_pii=True,
    )
