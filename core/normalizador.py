"""Normalización centralizada de usuarios y áreas."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

import pandas as pd

# Valores que, además de los nulos reales, se consideran ausencia de dato.
_TOKENS_NULOS = {
    "",
    "NAN",
    "NONE",
    "NULL",
    "N/A",
    "N / A",
    "NA",
    "NAT",
    "<NA>",
}


def _a_texto(valor: Any) -> str | None:
    """Convierte un valor a texto limpio (sin espacios extra ni caracteres raros)."""

    if valor is None:
        return None

    try:
        if pd.isna(valor):
            return None
    except (TypeError, ValueError):
        # pd.isna sobre listas/estructuras no aplica: se sigue con str().
        pass

    texto = unicodedata.normalize("NFC", str(valor))
    texto = (
        texto.replace("\u00a0", " ")  # espacio duro
        .replace("\u200b", "")  # espacio de ancho cero
        .replace("\ufeff", "")  # BOM
    )
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto or None


def limpiar_usuario(valor: Any) -> str | None:
    """Devuelve el usuario tal como se escribió, pero sin espacios sobrantes."""

    return _a_texto(valor)


def _clave_texto(texto: str) -> str:
    """Clave de comparación: mayúsculas y sin tildes/diacríticos."""

    clave = unicodedata.normalize("NFKD", texto.upper())
    return "".join(c for c in clave if not unicodedata.combining(c))


def normalizar_usuario(valor: Any) -> str | None:
    """Clave interna de comparación.

    Unifica mayúsculas, espacios adicionales y variantes con/sin tilde.
    El valor original se conserva por separado con ``limpiar_usuario``.
    """

    texto = _a_texto(valor)
    if texto is None:
        return None

    clave = _clave_texto(texto)
    if clave in _TOKENS_NULOS:
        return None
    return clave


def limpiar_area(valor: Any) -> str | None:
    """Devuelve el área sin espacios sobrantes; None si no es un área válida."""

    texto = _a_texto(valor)
    if texto is None:
        return None
    if texto.upper() in _TOKENS_NULOS:
        return None
    return texto


def normalizar_area(valor: Any) -> str | None:
    """Clave interna para comparar áreas (sin distinguir tildes ni mayúsculas)."""

    texto = limpiar_area(valor)
    return _clave_texto(texto) if texto else None


def es_usuario_valido(valor: Any) -> bool:
    return normalizar_usuario(valor) is not None


def es_area_valida(valor: Any) -> bool:
    return limpiar_area(valor) is not None
