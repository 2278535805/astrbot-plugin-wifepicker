from astrbot.api import logger


def debug_enabled(plugin) -> bool:
    return bool(plugin.config.get("debug_enabled", False))


def debug_log(plugin, area: str, message: str) -> None:
    if debug_enabled(plugin):
        logger.info(f"[wifepicker.{area}] {message}")
