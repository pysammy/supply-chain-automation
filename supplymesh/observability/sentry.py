import logging

import sentry_sdk

LOGGER = logging.getLogger(__name__)


def initialize_sentry(
    *, dsn: str | None, traces_sample_rate: float, profiles_sample_rate: float
) -> bool:
    if not dsn:
        LOGGER.warning("Sentry disabled because SENTRY_DSN is not configured")
        return False
    try:
        sentry_sdk.init(
            dsn=dsn,
            traces_sample_rate=traces_sample_rate,
            profiles_sample_rate=profiles_sample_rate,
            send_default_pii=False,
        )
    except Exception:
        LOGGER.warning("Sentry initialization failed; continuing without external telemetry")
        return False
    return True
