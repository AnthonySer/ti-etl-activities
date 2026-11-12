"""Normalización de nombres para identificar empleados corporativos.

La comparación no modifica el nombre original del ticket. Solo genera una clave
estable en mayúsculas, sin tildes, sin puntuación y con espacios simples.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable

from core.normalizador import limpiar_usuario

_NO_ALFANUMERICO = re.compile(r"[^A-Z0-9 ]+")
_ESPACIOS = re.compile(r"\s+")


def normalizar_nombre(valor: Any) -> str:
    """Clave de comparación de nombres y apellidos."""

    limpio = limpiar_usuario(valor)
    if not limpio:
        return ""
    texto = unicodedata.normalize("NFKD", limpio.upper())
    texto = "".join(caracter for caracter in texto if not unicodedata.combining(caracter))
    texto = texto.replace("Ñ", "N")
    texto = _NO_ALFANUMERICO.sub(" ", texto)
    return _ESPACIOS.sub(" ", texto).strip()


def nombre_visible(valor: Any) -> str:
    limpio = limpiar_usuario(valor)
    return limpio or ""


def claves_nombre(nombre: Any) -> set[str]:
    """Variantes razonables para comparar nombres con distinto orden."""

    clave = normalizar_nombre(nombre)
    if not clave:
        return set()
    return {clave}


def claves_empleado(
    *,
    nombre: Any = "",
    nombre_pila: Any = "",
    primer_apellido: Any = "",
    segundo_apellido: Any = "",
) -> set[str]:
    """Genera todas las claves con las que puede aparecer un empleado."""

    partes_pila = normalizar_nombre(nombre_pila)
    partes_apellidos = normalizar_nombre(
        f"{primer_apellido or ''} {segundo_apellido or ''}"
    )
    nombre_completo = normalizar_nombre(nombre)

    claves: set[str] = set()
    for valor in (nombre_completo,):
        if valor:
            claves.add(valor)

    # Apellidos + nombres y nombres + apellidos: cubren los dos órdenes que
    # pueden usar el Excel de tickets y la base corporativa.
    if partes_apellidos and partes_pila:
        claves.add(f"{partes_apellidos} {partes_pila}".strip())
        claves.add(f"{partes_pila} {partes_apellidos}".strip())
    if nombre_completo and partes_pila and partes_pila not in nombre_completo:
        claves.add(f"{nombre_completo} {partes_pila}".strip())
    if nombre_completo and partes_apellidos and partes_apellidos not in nombre_completo:
        claves.add(f"{partes_apellidos} {nombre_completo}".strip())

    # Las claves compuestas solo por apellidos no identifican a una persona;
    # se descartan para evitar coincidencias ambiguas innecesarias.
    return {clave for clave in claves if len(clave.split()) >= 2}


def claves_desde_empleado(empleado: Any) -> set[str]:
    return claves_empleado(
        nombre=getattr(empleado, "nombre", ""),
        nombre_pila=getattr(empleado, "nombre_pila", ""),
        primer_apellido=getattr(empleado, "primer_apellido", ""),
        segundo_apellido=getattr(empleado, "segundo_apellido", ""),
    )


def _huella_empleado(empleado: Any) -> tuple[str, str, str]:
    return (
        str(getattr(empleado, "empresa", "") or ""),
        str(
            getattr(empleado, "fuente", "")
            or getattr(empleado, "tabla", "")
            or ""
        ),
        str(getattr(empleado, "codigo", "") or ""),
    )


def indice_por_nombre(empleados: Iterable[Any]) -> dict[str, list[Any]]:
    """Construye un índice ``clave de nombre -> empleados`` sin perder duplicados.

    La identidad lógica es ``(empresa, fuente, código)``: dos fuentes de la
    misma empresa no se fusionan aunque compartan código y nombre.
    """

    indice: dict[str, list[Any]] = {}
    vistos: set[tuple[str, str, str]] = set()
    for empleado in empleados:
        huella = _huella_empleado(empleado)
        for clave in claves_desde_empleado(empleado):
            lista = indice.setdefault(clave, [])
            if huella not in vistos or all(
                _huella_empleado(item) != huella for item in lista
            ):
                lista.append(empleado)
                vistos.add(huella)
    return indice
