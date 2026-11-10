"""Consultas SQL centralizadas y de solo lectura para EXACTUS.

Los identificadores (base, esquema y columnas de unión) se validan contra una
lista estricta antes de interpolarse. Los valores de búsqueda siempre viajan
como parámetros ``?`` y nunca se concatenan en el texto SQL.

La estructura observada es ``[EXACTUS].[<EMPRESA>].[EMPLEADO]`` con catálogos
``DEPARTAMENTO`` y ``PUESTO``. Las columnas de unión son configurables porque
los nombres reales deben confirmarse con ``consulta_columnas_esquema`` antes de
usar la integración en producción.
"""

from __future__ import annotations

import re
from config.sqlserver import EMPRESAS

_IDENTIFICADOR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

COLUMNAS_EMPLEADO = (
    "EMPLEADO",
    "NOMBRE",
    "ESTADO_EMPLEADO",
    "ACTIVO",
    "DEPARTAMENTO",
    "PUESTO",
    "PRIMER_APELLIDO",
    "SEGUNDO_APELLIDO",
    "NOMBRE_PILA",
    "E_MAIL",
    "U_CORREO_CORP",
    "FUENTE",
)


def validar_identificador(valor: str, contexto: str = "identificador") -> str:
    valor = str(valor or "").strip()
    if not _IDENTIFICADOR.match(valor):
        raise ValueError(f"{contexto} no válido: {valor!r}")
    return valor


def validar_esquema(esquema: str) -> str:
    esquema = str(esquema or "").strip().upper()
    if esquema not in EMPRESAS:
        raise ValueError(f"Esquema de empresa no autorizado: {esquema!r}")
    return esquema


def _tabla(base: str, esquema: str, tabla: str) -> str:
    return (
        f"[{validar_identificador(base, 'base de datos')}]."
        f"[{validar_esquema(esquema)}]."
        f"[{validar_identificador(tabla, 'tabla')}]"
    )


def _limpiar(expresion: str) -> str:
    """Quita espacios y puntuación para una comparación tolerante."""

    for caracter in ("' '", "','", "'.'", "'-'", "'_'", "'/'"):
        expresion = f"REPLACE({expresion}, {caracter}, '')"
    return f"{expresion} COLLATE Latin1_General_CI_AI"


def _expresion_nombres() -> str:
    """Variantes de nombre completo que se comparan en lote."""

    return (
        _limpiar("UPPER(LTRIM(RTRIM(ISNULL(E.NOMBRE, ''))))"),
        _limpiar(
            "UPPER(LTRIM(RTRIM(CONCAT("
            "ISNULL(E.NOMBRE_PILA, ''), ' ', ISNULL(E.PRIMER_APELLIDO, ''), ' ', "
            "ISNULL(E.SEGUNDO_APELLIDO, '')))))"
        ),
        _limpiar(
            "UPPER(LTRIM(RTRIM(CONCAT("
            "ISNULL(E.PRIMER_APELLIDO, ''), ' ', ISNULL(E.SEGUNDO_APELLIDO, ''), ' ', "
            "ISNULL(E.NOMBRE_PILA, '')))))"
        ),
        _limpiar(
            "UPPER(LTRIM(RTRIM(CONCAT("
            "ISNULL(E.PRIMER_APELLIDO, ''), ' ', ISNULL(E.SEGUNDO_APELLIDO, ''), ' ', "
            "ISNULL(E.NOMBRE_PILA, ''), ' ', ISNULL(E.NOMBRE, '')))))"
        ),
    )


def sql_empleados(
    *,
    base_datos: str,
    esquema: str,
    cantidad: int = 0,
    columna_depto_empleado: str = "DEPARTAMENTO",
    columna_depto_catalogo: str = "DEPARTAMENTO",
    columna_puesto_empleado: str = "PUESTO",
    columna_puesto_catalogo: str = "PUESTO",
) -> tuple[str, int]:
    """Devuelve el SQL y la cantidad de parámetros por cada nombre buscado.

    ``cantidad`` = 0 genera la consulta completa (sincronización manual).
    Con ``cantidad`` > 0 se agrega un ``IN`` parametrizado por cada variante de
    nombre; el repositorio repite los valores en el mismo orden.
    """

    col_depto_emp = validar_identificador(
        columna_depto_empleado, "columna DEPARTAMENTO de EMPLEADO"
    )
    col_depto_cat = validar_identificador(
        columna_depto_catalogo, "columna DEPARTAMENTO del catálogo"
    )
    col_puesto_emp = validar_identificador(
        columna_puesto_empleado, "columna PUESTO de EMPLEADO"
    )
    col_puesto_cat = validar_identificador(
        columna_puesto_catalogo, "columna PUESTO del catálogo"
    )

    tabla_empleado = _tabla(base_datos, esquema, "EMPLEADO")
    tabla_depto = _tabla(base_datos, esquema, "DEPARTAMENTO")
    tabla_puesto = _tabla(base_datos, esquema, "PUESTO")

    columnas = [
        "E.EMPLEADO AS EMPLEADO",
        "E.NOMBRE AS NOMBRE",
        "E.ESTADO_EMPLEADO AS ESTADO_EMPLEADO",
        "E.ACTIVO AS ACTIVO",
        f"E.{col_depto_emp} AS DEPARTAMENTO_CODIGO",
        f"E.{col_puesto_emp} AS PUESTO_CODIGO",
        "E.PRIMER_APELLIDO AS PRIMER_APELLIDO",
        "E.SEGUNDO_APELLIDO AS SEGUNDO_APELLIDO",
        "E.NOMBRE_PILA AS NOMBRE_PILA",
        "E.E_MAIL AS E_MAIL",
        "E.U_CORREO_CORP AS U_CORREO_CORP",
        "E.FUENTE AS FUENTE",
        f"D.DESCRIPCION AS DEPARTAMENTO_DESCRIPCION",
        f"P.DESCRIPCION AS PUESTO_DESCRIPCION",
    ]

    sql = (
        "SELECT "
        + ", ".join(columnas)
        + f" FROM {tabla_empleado} E"
        + f" LEFT JOIN {tabla_depto} D"
        + f" ON D.{col_depto_cat} = E.{col_depto_emp}"
        + f" LEFT JOIN {tabla_puesto} P"
        + f" ON P.{col_puesto_cat} = E.{col_puesto_emp}"
    )

    if cantidad <= 0:
        return sql + " ORDER BY E.EMPLEADO", 0

    expresiones = _expresion_nombres()
    parametros_por_expresion = cantidad
    grupos = []
    for expresion in expresiones:
        marcas = ", ".join("?" for _ in range(cantidad))
        grupos.append(f"{expresion} IN ({marcas})")
    sql += " WHERE (" + " OR ".join(grupos) + ") ORDER BY E.EMPLEADO"
    return sql, parametros_por_expresion * len(expresiones)


def sql_columnas_esquema(base_datos: str, esquema: str) -> str:
    """Consulta de verificación de columnas reales (SELECT de metadatos)."""

    base = validar_identificador(base_datos, "base de datos")
    esquema = validar_esquema(esquema)
    return (
        "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE "
        f"FROM [{base}].INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = ? "
        "ORDER BY TABLE_NAME, ORDINAL_POSITION"
    )


def sql_tablas_existen(base_datos: str, esquema: str) -> str:
    """Verifica la existencia de EMPLEADO, DEPARTAMENTO y PUESTO."""

    base = validar_identificador(base_datos, "base de datos")
    esquema = validar_esquema(esquema)
    return (
        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
        f"WHERE TABLE_CATALOG = ? AND TABLE_SCHEMA = ? "
        "AND TABLE_NAME IN ('EMPLEADO', 'DEPARTAMENTO', 'PUESTO') "
        "ORDER BY TABLE_NAME"
    )
