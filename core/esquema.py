"""Esquema oficial inalterable de 11 columnas para los tickets de TI.

Este módulo centraliza:

- los nombres oficiales y su orden obligatorio;
- los alias aceptados en los archivos de origen;
- la conversión de encabezados de origen a encabezados oficiales;
- la validación estricta de estructura;
- la huella canónica de una fila (para deduplicación y trazabilidad).

Ninguna otra parte del sistema debe redefinir estos nombres.
"""

from __future__ import annotations

import hashlib
import math
import unicodedata
from datetime import date, datetime
from typing import Any, Iterable, Mapping

import pandas as pd

from core.archivos import ErrorEstructura, clave_columna

# ---------------------------------------------------------------------------
# Estructura oficial
# ---------------------------------------------------------------------------
C_DETALLE = "Detalle"
C_FECHA = "Fecha"
C_SOLICITANTE = "Solicitante"
C_AREA = "Area"
C_ATENCION = "Atención"
C_REQUERIMIENTO = "Requerimiento"
C_RESPONSABLE = "Responsable"
C_SUBTIPO = "Subtipo"
C_PROYECTO = "Proyecto"
C_HORAS = "Horas"
C_ACTIVIDAD = "Actividad"

COLUMNAS_OFICIALES: tuple[str, ...] = (
    C_DETALLE,
    C_FECHA,
    C_SOLICITANTE,
    C_AREA,
    C_ATENCION,
    C_REQUERIMIENTO,
    C_RESPONSABLE,
    C_SUBTIPO,
    C_PROYECTO,
    C_HORAS,
    C_ACTIVIDAD,
)

COLUMNAS_TEXTO: tuple[str, ...] = (
    C_DETALLE,
    C_SOLICITANTE,
    C_AREA,
    C_ATENCION,
    C_REQUERIMIENTO,
    C_RESPONSABLE,
    C_SUBTIPO,
    C_PROYECTO,
    C_ACTIVIDAD,
)

# Alias reconocidos. El primero es el nombre oficial; el resto son variantes
# observadas en los archivos reales (incluidos errores de escritura).
ALIAS_OFICIALES: Mapping[str, tuple[str, ...]] = {
    C_DETALLE: (
        "DETALLE",
        "DETALLE_TICKET",
        "DESCRIPCION",
        "DETALLE_INCIDENCIA",
    ),
    C_FECHA: (
        "FECHA",
        "FECHA_REGISRO",
        "FECHA_REGISTRO",
        "FECHA_REGISTR0",
        "FECHA_SOLICITUD",
    ),
    C_SOLICITANTE: (
        "SOLICITANTE",
        "USUARIO",
        "USUARIO_SOLICITANTE",
        "NOMBRE_SOLICITANTE",
    ),
    C_AREA: (
        "AREA",
        "AREA_USUARIO",
        "ÁREA",
    ),
    C_ATENCION: (
        "ATENCION",
        "TIPO_ATENCION",
        "TIPO DE ATENCIÓN",
    ),
    C_REQUERIMIENTO: (
        "REQUERIMIENTO",
        "TIPO_REQUERIMIENTO",
    ),
    C_RESPONSABLE: (
        "RESPONSABLE",
        "TECNICO",
        "ASIGNADO",
    ),
    C_SUBTIPO: (
        "SUBTIPO",
        "SUB_TIPO",
        "SUBT_TIPO",
        "SUBTIPO_TICKET",
    ),
    C_PROYECTO: (
        "PROYECTO",
        "NOMBRE_PROYECTO",
    ),
    C_HORAS: (
        "HORAS",
        "HORAS_TOTAL",
        "HORAS_TOTALES",
        "TIEMPO",
        "TIEMPO_H",
    ),
    C_ACTIVIDAD: (
        "ACTIVIDAD",
        "GRUPO_1",
        "GRUPO1",
        "TIPO_ACTIVIDAD",
    ),
}

# Un encabezado que no coincide con ningún alias se considera columna adicional
# y nunca se incorpora a la hoja de detalle oficial.


def _clave_base(nombre: Any) -> str:
    """Clave de encabezado eliminando el sufijo que agrega pandas a duplicados."""

    clave = clave_columna(nombre)
    if "_" in clave:
        cabeza, _, cola = clave.rpartition("_")
        if cola.isdigit() and len(cola) <= 2:
            return cabeza
    return clave


_MAPA_ALIAS: dict[str, str] = {}
for _canonica, _alias in ALIAS_OFICIALES.items():
    for _nombre in _alias:
        _MAPA_ALIAS.setdefault(_clave_base(_nombre), _canonica)


def canonizar_encabezado(nombre: Any) -> str | None:
    """Devuelve el nombre oficial de una columna o None si es adicional."""

    return _MAPA_ALIAS.get(_clave_base(nombre))


def columnas_de_origen(df: pd.DataFrame) -> dict[str, list[str]]:
    """Agrupa las columnas de origen por nombre oficial."""

    grupos: dict[str, list[str]] = {}
    for columna in df.columns:
        canonica = canonizar_encabezado(columna)
        if canonica is not None:
            grupos.setdefault(canonica, []).append(str(columna))
    return grupos


def _tiene_datos(serie: pd.Series) -> bool:
    for valor in serie.tolist():
        if valor is None:
            continue
        try:
            if pd.isna(valor):
                continue
        except (TypeError, ValueError):
            pass
        if str(valor).strip() != "":
            return True
    return False


class ErrorColumnasDuplicadas(ErrorEstructura):
    """Dos columnas de origen representan el mismo campo y ambas tienen datos."""


def mapear_oficial(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame | None, dict[str, str]]:
    """Convierte un DataFrame de origen al esquema oficial de 11 columnas.

    Devuelve ``(detalle, extras, mapeo)``:

    - ``detalle``: DataFrame con exactamente las 11 columnas oficiales en orden.
    - ``extras``: columnas adicionales no reconocidas (o None si no hay).
    - ``mapeo``: encabezado original -> encabezado oficial.

    Reglas:

    - Si falta una columna oficial se lanza ErrorEstructura indicando cuál.
    - Si dos columnas de origen representan el mismo campo y ambas contienen
      información, se detiene la importación (no se descarta ninguna).
    - Si una de las dos está vacía, se usa la que contiene datos.
    """

    df = df.copy()
    df.columns = [
        "" if columna is None else str(columna).strip() for columna in df.columns
    ]

    grupos = columnas_de_origen(df)
    faltantes = [columna for columna in COLUMNAS_OFICIALES if columna not in grupos]
    if faltantes:
        encontradas = ", ".join(repr(c) for c in df.columns if str(c).strip())
        raise ErrorEstructura(
            "Faltan columnas obligatorias del esquema oficial: "
            + ", ".join(faltantes)
            + ". Columnas encontradas: "
            + (encontradas or "(ninguna)")
        )

    elegidas: dict[str, str] = {}
    for canonica, columnas in grupos.items():
        con_datos = [col for col in columnas if _tiene_datos(df[col])]
        if len(con_datos) > 1:
            raise ErrorColumnasDuplicadas(
                f"Las columnas {con_datos} representan el mismo campo oficial "
                f"'{canonica}' y todas contienen información. "
                "No se descarta ninguna automáticamente: revise el archivo de "
                "origen y deje una sola columna con datos para ese campo."
            )
        elegidas[canonica] = (con_datos or columnas)[0]

    detalle = pd.DataFrame(
        {canonica: df[elegidas[canonica]].reset_index(drop=True)
         for canonica in COLUMNAS_OFICIALES}
    )

    extras_columnas = [
        str(columna)
        for columna in df.columns
        if str(columna).strip() and canonizar_encabezado(columna) is None
    ]
    extras: pd.DataFrame | None = None
    if extras_columnas:
        extras = df.loc[:, extras_columnas].reset_index(drop=True).copy()

    mapeo = {origen: canonica for canonica, origen in elegidas.items()}
    return detalle, extras, mapeo


def validar_estructura_exacta(
    columnas: Iterable[Any],
    contexto: str = "archivo",
) -> None:
    """Exige exactamente las 11 columnas oficiales, en el orden obligatorio."""

    actuales = ["" if c is None else str(c).strip() for c in columnas]
    oficiales = list(COLUMNAS_OFICIALES)

    if actuales == oficiales:
        return

    problemas: list[str] = []
    if len(actuales) != len(oficiales):
        problemas.append(
            f"cantidad de columnas {len(actuales)} (se esperaban {len(oficiales)})"
        )
    faltantes = [c for c in oficiales if c not in actuales]
    adicionales = [c for c in actuales if c not in oficiales]
    if faltantes:
        problemas.append("faltan: " + ", ".join(faltantes))
    if adicionales:
        problemas.append("sobran: " + ", ".join(adicionales))
    if not faltantes and not adicionales and actuales != oficiales:
        problemas.append("el orden no es el oficial")

    raise ErrorEstructura(
        f"Estructura inválida en {contexto}: " + "; ".join(problemas)
        + ". Orden oficial: " + ", ".join(oficiales) + "."
    )


# ---------------------------------------------------------------------------
# Huellas y comparación canónica
# ---------------------------------------------------------------------------
def _texto_clave(valor: Any) -> str:
    if valor is None:
        return ""
    try:
        if pd.isna(valor):
            return ""
    except (TypeError, ValueError):
        pass
    texto = unicodedata.normalize("NFC", str(valor)).replace("\u00a0", " ")
    texto = " ".join(texto.split()).strip().upper()
    return texto


def _numero_clave(valor: Any) -> str:
    if valor is None:
        return ""
    try:
        if pd.isna(valor):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(valor, str):
        texto = valor.strip().replace(",", ".")
        try:
            valor = float(texto) if texto else None
        except ValueError:
            return ""
    if valor is None:
        return ""
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(numero):
        return ""
    if numero == int(numero):
        return str(int(numero))
    return f"{numero:.4f}".rstrip("0").rstrip(".")


def _fecha_clave(valor: Any) -> str:
    if valor is None:
        return ""
    try:
        if pd.isna(valor):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(valor, (datetime, date)):
        return valor.strftime("%Y-%m-%d")
    if isinstance(valor, pd.Timestamp):
        return valor.strftime("%Y-%m-%d")
    return _texto_clave(valor)


def valor_canonico(columna: str, valor: Any) -> str:
    """Representación estable de un valor para comparar filas."""

    if columna == C_FECHA:
        return _fecha_clave(valor)
    if columna == C_HORAS:
        return _numero_clave(valor)
    return _texto_clave(valor)


def datos_canonicos(fila: Mapping[str, Any]) -> dict[str, str]:
    return {columna: valor_canonico(columna, fila.get(columna)) for columna in COLUMNAS_OFICIALES}


def huella_fila(fila: Mapping[str, Any]) -> str:
    """SHA-256 de los 11 valores canónicos. No incluye estado ni área técnica."""

    canonicos = datos_canonicos(fila)
    texto = "\x1f".join(canonicos[columna] for columna in COLUMNAS_OFICIALES)
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def serializar_valor(valor: Any) -> Any:
    """Convierte valores de pandas/Excel a tipos JSON."""

    if valor is None:
        return None
    try:
        if pd.isna(valor):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(valor, (datetime, date, pd.Timestamp)):
        return valor.strftime("%Y-%m-%d")
    if isinstance(valor, (int, float)):
        return valor
    return str(valor)


def fila_a_dict(fila: Mapping[str, Any]) -> dict[str, Any]:
    return {columna: serializar_valor(fila.get(columna)) for columna in COLUMNAS_OFICIALES}


def diferencia_campos(
    antes: Mapping[str, Any],
    despues: Mapping[str, Any],
) -> dict[str, tuple[str, str]]:
    """Devuelve los campos oficiales cuyo valor canónico cambió."""

    cambios: dict[str, tuple[str, str]] = {}
    for columna in COLUMNAS_OFICIALES:
        if valor_canonico(columna, antes.get(columna)) != valor_canonico(
            columna, despues.get(columna)
        ):
            cambios[columna] = (
                "" if antes.get(columna) is None else str(antes.get(columna)),
                "" if despues.get(columna) is None else str(despues.get(columna)),
            )
    return cambios
