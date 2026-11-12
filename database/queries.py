"""Consultas SQL centralizadas y de solo lectura para EXACTUS.

La estructura observada es ``[EXACTUS].[<EMPRESA>].[TABLA]``. Cada empresa
puede tener un subconjunto distinto de columnas, por lo que el ``SELECT`` se
construye únicamente con las columnas que existen en el esquema real.

Los identificadores (base, esquema y columnas) se validan contra listas
estrictas antes de interpolarse. Los valores de búsqueda siempre viajan como
parámetros ``?`` y nunca se concatenan en el texto SQL.
"""

from __future__ import annotations

import re
from typing import Iterable, Mapping

from config.sqlserver import EMPRESAS

_IDENTIFICADOR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

TABLA_EMPLEADO = "EMPLEADO"
TABLA_DEPARTAMENTO = "DEPARTAMENTO"
TABLA_PUESTO = "PUESTO"

CAMPOS_UNIFORMES = (
    "EMPLEADO",
    "NOMBRE",
    "EMPRESA",
    "FUENTE",
    "DEPARTAMENTO",
    "PUESTO",
    "ACTIVO",
    "CORREO",
)

# Columnas candidatas conocidas. La presencia real se comprueba siempre contra
# INFORMATION_SCHEMA antes de construir el SELECT.
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
)


class EsquemaInsuficiente(ValueError):
    """La tabla no tiene campos fundamentales para identificar empleados."""


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


def _limpiar_espacios(expresion: str) -> str:
    """Reemplaza puntuación por espacios sin perder la separación de palabras.

    Se usa para patrones ``LIKE``: el orden de los términos debe conservarse y
    ``JUAN-CARLOS`` debe poder encontrarse como ``JUAN CARLOS``.
    """

    for caracter in ("'.'", "','", "'-'", "'_'", "'/'", "''''"):
        expresion = f"REPLACE({expresion}, {caracter}, ' ')"
    return f"{expresion} COLLATE Latin1_General_CI_AI"


def _expresion_nombres(
    columnas: Iterable[str],
    *,
    compacto: bool = True,
) -> list[str]:
    """Expresión normalizada para comparar el nombre del solicitante.

    Se devuelve una sola expresión para que todas las empresas usen la misma
    cantidad de parámetros por lote. Si no existe ``NOMBRE`` se compone con las
    partes disponibles; si tampoco hay partes, la tabla es insuficiente.

    ``compacto=True`` conserva la comparación exacta histórica (sin espacios);
    ``compacto=False`` conserva los espacios para patrones ``LIKE`` ordenados.
    """

    disponibles = {str(columna).upper() for columna in columnas}

    if "NOMBRE" in disponibles:
        base = "UPPER(LTRIM(RTRIM(ISNULL(E.NOMBRE, ''))))"
    else:
        partes = [
            columna
            for columna in ("NOMBRE_PILA", "PRIMER_APELLIDO", "SEGUNDO_APELLIDO")
            if columna in disponibles
        ]
        if not partes:
            return []
        concat = ", ' ', ".join(f"ISNULL(E.{columna}, '')" for columna in partes)
        base = f"UPPER(LTRIM(RTRIM(CONCAT({concat}))))"

    return [_limpiar(base) if compacto else _limpiar_espacios(base)]


def _columnas_por_tabla(
    columnas_disponibles: Mapping[str, Iterable[str]] | Iterable[str] | None,
) -> dict[str, set[str]]:
    """Normaliza la entrada a ``TABLA -> {COLUMNAS}``."""

    if columnas_disponibles is None:
        return {
            TABLA_EMPLEADO: {columna.upper() for columna in COLUMNAS_EMPLEADO}
        }
    if isinstance(columnas_disponibles, Mapping):
        return {
            str(tabla).strip().upper(): {
                str(columna).strip().upper() for columna in columnas
            }
            for tabla, columnas in columnas_disponibles.items()
        }
    return {
        TABLA_EMPLEADO: {
            str(columna).strip().upper() for columna in columnas_disponibles
        }
    }


def columnas_por_tabla(filas: Iterable[Mapping[str, object]]) -> dict[str, set[str]]:
    """Agrupa filas de ``INFORMATION_SCHEMA.COLUMNS`` por tabla."""

    agrupadas: dict[str, set[str]] = {}
    for fila in filas:
        tabla = str(fila.get("TABLE_NAME") or "").strip().upper()
        columna = str(fila.get("COLUMN_NAME") or "").strip().upper()
        if tabla and columna:
            agrupadas.setdefault(tabla, set()).add(columna)
    return agrupadas


def sql_empleados(
    *,
    base_datos: str,
    esquema: str,
    cantidad: int = 0,
    patrones: Iterable[str] | None = None,
    columnas_disponibles: Mapping[str, Iterable[str]] | Iterable[str] | None = None,
    columna_depto_empleado: str = "DEPARTAMENTO",
    columna_depto_catalogo: str = "DEPARTAMENTO",
    columna_puesto_empleado: str = "PUESTO",
    columna_puesto_catalogo: str = "PUESTO",
) -> tuple[str, int]:
    """Devuelve el SQL adaptado y la cantidad de parámetros por nombre.

    ``cantidad`` = 0 genera la consulta completa (sincronización manual).
    Con ``cantidad`` > 0 se agrega un ``IN`` parametrizado por cada variante
    de nombre; el repositorio repite los valores en el mismo orden.

    ``patrones`` permite una búsqueda ordenada: genera
    ``(expresión LIKE ? ESCAPE '\\' OR ...)`` conservando el orden de los
    términos. Los patrones siempre viajan como parámetros y sus comodines
    deben venir escapados por ``core.busqueda_solicitantes``.

    Solo se incluyen columnas presentes en ``columnas_disponibles``. Si faltan
    los campos fundamentales se lanza ``EsquemaInsuficiente`` antes de ejecutar
    cualquier consulta.
    """

    lista_patrones = list(patrones) if patrones is not None else None
    if lista_patrones is not None and not lista_patrones:
        raise ValueError("La búsqueda por patrones requiere al menos un patrón.")

    columnas = _columnas_por_tabla(columnas_disponibles)
    columnas_emp = columnas.get(TABLA_EMPLEADO, set())
    if "EMPLEADO" not in columnas_emp:
        raise EsquemaInsuficiente(
            "La tabla EMPLEADO no expone la columna fundamental EMPLEADO."
        )

    expresiones_nombre = _expresion_nombres(
        columnas_emp, compacto=lista_patrones is None
    )
    if not expresiones_nombre:
        raise EsquemaInsuficiente(
            "La tabla EMPLEADO no tiene NOMBRE ni columnas de nombre "
            "suficientes para identificar empleados."
        )

    col_depto_emp = (
        validar_identificador(columna_depto_empleado, "columna DEPARTAMENTO de EMPLEADO")
        if columna_depto_empleado.upper() in columnas_emp
        else ("DEPARTAMENTO" if "DEPARTAMENTO" in columnas_emp else "")
    )
    col_puesto_emp = (
        validar_identificador(columna_puesto_empleado, "columna PUESTO de EMPLEADO")
        if columna_puesto_empleado.upper() in columnas_emp
        else ("PUESTO" if "PUESTO" in columnas_emp else "")
    )

    tabla_empleado = _tabla(base_datos, esquema, TABLA_EMPLEADO)
    columnas_sql: list[str] = [
        "E.[EMPLEADO] AS EMPLEADO",
        "E.[EMPLEADO] AS CODIGO_EMPLEADO",
        "'EMPLEADO' AS FUENTE",
    ]

    # Nombre: se prefiere NOMBRE y, si no existe, se compone con las partes.
    if "NOMBRE" in columnas_emp:
        columnas_sql.append("E.[NOMBRE] AS NOMBRE")
    else:
        columnas_sql.append(
            "LTRIM(RTRIM(CONCAT("
            + ", ' ', ".join(
                f"ISNULL(E.[{col}], '')"
                for col in ("NOMBRE_PILA", "PRIMER_APELLIDO", "SEGUNDO_APELLIDO")
                if col in columnas_emp
            )
            + "))) AS NOMBRE"
        )

    for columna in (
        "PRIMER_APELLIDO",
        "SEGUNDO_APELLIDO",
        "NOMBRE_PILA",
        "ESTADO_EMPLEADO",
        "E_MAIL",
        "U_CORREO_CORP",
    ):
        if columna in columnas_emp:
            columnas_sql.append(f"E.[{columna}] AS {columna}")

    if col_puesto_emp:
        columnas_sql.append(f"E.[{col_puesto_emp}] AS PUESTO_CODIGO")
    else:
        columnas_sql.append("NULL AS PUESTO_CODIGO")

    if col_depto_emp:
        columnas_sql.append(f"E.[{col_depto_emp}] AS DEPARTAMENTO_CODIGO")
    else:
        columnas_sql.append("NULL AS DEPARTAMENTO_CODIGO")

    if "ACTIVO" in columnas_emp:
        columnas_sql.append("E.[ACTIVO] AS ACTIVO")
    else:
        columnas_sql.append("NULL AS ACTIVO")

    # Catálogo de departamentos: solo si existen tabla y columnas de unión.
    columnas_depto = columnas.get(TABLA_DEPARTAMENTO, set())
    unir_depto = bool(
        col_depto_emp
        and columna_depto_catalogo.upper() in columnas_depto
        and "DESCRIPCION" in columnas_depto
    )
    if unir_depto:
        columnas_sql.append("D.[DESCRIPCION] AS DEPARTAMENTO")
    elif col_depto_emp:
        columnas_sql.append(f"E.[{col_depto_emp}] AS DEPARTAMENTO")
    else:
        columnas_sql.append("NULL AS DEPARTAMENTO")

    columnas_puesto = columnas.get(TABLA_PUESTO, set())
    unir_puesto = bool(
        col_puesto_emp
        and columna_puesto_catalogo.upper() in columnas_puesto
        and "DESCRIPCION" in columnas_puesto
    )
    if unir_puesto:
        columnas_sql.append("P.[DESCRIPCION] AS PUESTO")
    elif col_puesto_emp:
        columnas_sql.append(f"E.[{col_puesto_emp}] AS PUESTO")
    else:
        columnas_sql.append("NULL AS PUESTO")

    if "E_MAIL" in columnas_emp:
        columnas_sql.append("E.[E_MAIL] AS CORREO")
    elif "U_CORREO_CORP" in columnas_emp:
        columnas_sql.append("E.[U_CORREO_CORP] AS CORREO")
    else:
        columnas_sql.append("NULL AS CORREO")

    sql_texto = (
        "SELECT " + ", ".join(columnas_sql) + f" FROM {tabla_empleado} E"
    )
    if unir_depto:
        tabla_depto = _tabla(base_datos, esquema, TABLA_DEPARTAMENTO)
        sql_texto += (
            f" LEFT JOIN {tabla_depto} D"
            f" ON D.[{validar_identificador(columna_depto_catalogo)}]"
            f" = E.[{col_depto_emp}]"
        )
    if unir_puesto:
        tabla_puesto = _tabla(base_datos, esquema, TABLA_PUESTO)
        sql_texto += (
            f" LEFT JOIN {tabla_puesto} P"
            f" ON P.[{validar_identificador(columna_puesto_catalogo)}]"
            f" = E.[{col_puesto_emp}]"
        )

    if lista_patrones is not None:
        clausulas = " OR ".join(
            f"{expresiones_nombre[0]} LIKE ? ESCAPE '\\'"
            for _ in lista_patrones
        )
        sql_texto += f" WHERE ({clausulas}) ORDER BY E.[EMPLEADO]"
        return sql_texto, len(lista_patrones)

    if cantidad <= 0:
        return sql_texto + " ORDER BY E.[EMPLEADO]", 0

    marcas = ", ".join("?" for _ in range(cantidad))
    sql_texto += (
        f" WHERE {expresiones_nombre[0]} IN ({marcas})"
        " ORDER BY E.[EMPLEADO]"
    )
    return sql_texto, cantidad


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


def sql_tablas_empresa(base_datos: str, esquema: str) -> str:
    """Lista tablas y vistas del esquema de una empresa."""

    base = validar_identificador(base_datos, "base de datos")
    esquema = validar_esquema(esquema)
    return (
        "SELECT TABLE_NAME, TABLE_TYPE "
        f"FROM [{base}].INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = ? ORDER BY TABLE_NAME"
    )
