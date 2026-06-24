import sys
from pathlib import Path


def _load_delivery_symbols():  # noqa: ANN202
    from openminion.services.cron.delivery import (
        HttpPost,
        OutboundSender,
        deliver_cron_result,
    )

    return HttpPost, OutboundSender, deliver_cron_result


def _import_delivery_symbols():  # noqa: ANN202
    try:
        return _load_delivery_symbols()
    except ModuleNotFoundError:
        for parent in Path(__file__).resolve().parents:
            for candidate in (
                parent / "openminion" / "src",
                parent / "openminion-cron" / "src",
            ):
                if not candidate.exists():
                    continue
                candidate_str = str(candidate)
                if candidate_str not in sys.path:
                    sys.path.insert(0, candidate_str)
        return _load_delivery_symbols()


HttpPost, OutboundSender, deliver_cron_result = _import_delivery_symbols()

__all__ = ["HttpPost", "OutboundSender", "deliver_cron_result"]
