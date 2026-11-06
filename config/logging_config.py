"""Configuración de logging y consola."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from config import configuracion as cfg


def configurar_consola() -> None:
    """Fuerza UTF-8 en consola para mostrar tildes y Ñ correctamente."""

    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def configurar_logging() -> logging.Logger:
    """Configura log rotativo en logs/sistema.log y avisos en consola."""

    cfg.asegurar_directorios()
    logger = logging.getLogger("automatizador")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    if logger.handlers:
        return logger

    formato = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    archivo = RotatingFileHandler(
        cfg.LOG_FILE,
        maxBytes=cfg.LOG_MAX_BYTES,
        backupCount=cfg.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    archivo.setLevel(logging.DEBUG)
    archivo.setFormatter(formato)
    logger.addHandler(archivo)

    consola = logging.StreamHandler(sys.stderr)
    consola.setLevel(logging.WARNING)
    consola.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(consola)

    return logger
