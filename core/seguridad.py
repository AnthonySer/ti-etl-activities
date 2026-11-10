"""Protección local de credenciales.

En Windows se usa la API DPAPI (``CryptProtectData`` / ``CryptUnprotectData``),
de modo que el texto cifrado solo puede descifrarlo la misma cuenta de usuario
en el mismo equipo. Fuera de Windows, o si DPAPI no está disponible, se usa un
respaldo codificado en Base64 con una advertencia explícita en el log.

Este módulo nunca imprime ni registra el valor en claro.
"""

from __future__ import annotations

import base64
import ctypes
import logging
from ctypes import wintypes
from typing import Final

LOGGER = logging.getLogger("automatizador")

_PREFIJO_DPAPI: Final = "dpapi:"
_PREFIJO_BASE64: Final = "b64:"


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _blob(datos: bytes) -> tuple[_DATA_BLOB, ctypes.Array]:
    buffer = ctypes.create_string_buffer(datos, len(datos))
    return (
        _DATA_BLOB(len(datos), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))),
        buffer,
    )


def _dpapi_disponible() -> bool:
    return hasattr(ctypes, "windll") and hasattr(ctypes.windll, "crypt32")


def _proteger_dpapi(texto: str) -> str | None:
    if not _dpapi_disponible():
        return None
    try:
        entrada, _ = _blob(texto.encode("utf-8"))
        salida = _DATA_BLOB()
        ok = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(entrada),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(salida),
        )
        if not ok:
            return None
        try:
            cifrado = ctypes.string_at(salida.pbData, salida.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(salida.pbData)
        return _PREFIJO_DPAPI + base64.b64encode(cifrado).decode("ascii")
    except (AttributeError, OSError, ValueError) as exc:  # pragma: no cover
        LOGGER.warning("No se pudo cifrar la credencial con DPAPI: %s", type(exc).__name__)
        return None


def _desproteger_dpapi(valor: str) -> str | None:
    if not _dpapi_disponible():
        return None
    try:
        cifrado = base64.b64decode(valor[len(_PREFIJO_DPAPI) :])
        entrada, _ = _blob(cifrado)
        salida = _DATA_BLOB()
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(entrada),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(salida),
        )
        if not ok:
            return None
        try:
            datos = ctypes.string_at(salida.pbData, salida.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(salida.pbData)
        return datos.decode("utf-8")
    except (AttributeError, OSError, ValueError):  # pragma: no cover
        return None


def proteger_texto(texto: str) -> str:
    """Devuelve una representación cifrada y segura para guardar en disco."""

    if not texto:
        return ""
    protegido = _proteger_dpapi(texto)
    if protegido is not None:
        return protegido
    LOGGER.warning(
        "DPAPI no está disponible; la credencial se guardará codificada, "
        "no cifrada con la cuenta de Windows."
    )
    return _PREFIJO_BASE64 + base64.b64encode(texto.encode("utf-8")).decode("ascii")


def desproteger_texto(valor: str) -> str:
    """Recupera el texto original; devuelve cadena vacía si no puede."""

    if not valor:
        return ""
    if valor.startswith(_PREFIJO_DPAPI):
        recuperado = _desproteger_dpapi(valor)
        if recuperado is None:
            LOGGER.error(
                "No se pudo descifrar la credencial guardada con la cuenta actual."
            )
            return ""
        return recuperado
    if valor.startswith(_PREFIJO_BASE64):
        try:
            return base64.b64decode(valor[len(_PREFIJO_BASE64) :]).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return ""
    # Compatibilidad con valores antiguos en claro; se migran al guardar.
    return valor


def esta_protegido(valor: str) -> bool:
    return bool(valor) and valor.startswith((_PREFIJO_DPAPI, _PREFIJO_BASE64))
