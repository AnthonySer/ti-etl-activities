"""Validación y mapeo de consultas SQL manuales de solo lectura."""

from __future__ import annotations

from typing import Any


class ErrorConsultaManual(ValueError):
    pass


def validar_select(sql: str) -> str:
    """Acepta un único SELECT (incluido WITH) validado como AST T-SQL.

    sqlglot es obligatorio para esta función: si no está disponible, se falla
    cerrado y el editor no ejecuta SQL sin validación sintáctica.
    """

    texto = str(sql or "").strip()
    if not texto:
        raise ErrorConsultaManual("Escriba una consulta SELECT.")
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError as exc:  # fail-closed
        raise ErrorConsultaManual(
            "La validación SQL segura requiere sqlglot. Instale las dependencias del proyecto."
        ) from exc
    try:
        sentencias = [arbol for arbol in sqlglot.parse(texto, read="tsql") if arbol]
    except Exception as exc:  # parser devuelve errores específicos por versión
        raise ErrorConsultaManual(f"La consulta SQL no es válida: {exc}") from exc
    if len(sentencias) != 1:
        raise ErrorConsultaManual("Solo se permite una sentencia SQL.")
    arbol = sentencias[0]
    raiz = arbol.this if isinstance(arbol, exp.With) else arbol
    if not isinstance(raiz, exp.Select):
        raise ErrorConsultaManual("Solo se permiten consultas SELECT o WITH ... SELECT.")

    bloqueadas = tuple(
        clase for nombre in (
            "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter",
            "Command", "Transaction", "Use", "Set", "Grant", "Revoke",
            "Into", "TruncateTable", "NextValueFor",
        ) if (clase := getattr(exp, nombre, None)) is not None
    )
    if bloqueadas and any(isinstance(nodo, bloqueadas) for nodo in arbol.walk()):
        raise ErrorConsultaManual("La consulta contiene una operación que no es de lectura.")

    # Denegar funciones con efectos laterales o ejecución externa. Las funciones
    # anónimas también quedan rechazadas porque podrían ser UDF/procedimientos.
    peligrosas = {
        "OPENROWSET", "OPENDATASOURCE", "OPENQUERY", "XP_CMDSHELL",
        "SP_EXECUTESQL", "EXEC", "EXECUTE",
    }
    for nodo in arbol.walk():
        tipo_nodo = type(nodo).__name__.upper()
        if any(nombre in tipo_nodo for nombre in (
            "OPENROWSET", "OPENDATASOURCE", "OPENQUERY", "NEXTVALUE", "EXECUTE"
        )):
            raise ErrorConsultaManual("La consulta incluye una operación SQL no permitida.")
        if isinstance(nodo, getattr(exp, "Anonymous", ())):
            nombre = str(nodo.name or "").upper()
            if nombre in peligrosas or nombre:
                raise ErrorConsultaManual(
                    f"No se permite la función SQL '{nombre}' en consultas manuales."
                )
    return texto


def mapear_filas(filas: list[dict[str, Any]]) -> dict[str, Any]:
    """Normaliza nombres de columnas disponibles sin asumir un esquema fijo."""

    if not filas:
        return {"columnas": [], "registros": [], "faltantes": ["EMPLEADO", "NOMBRE", "FUENTE"]}
    columnas = list(filas[0])
    canonicas = {str(columna).strip().upper(): columna for columna in columnas}
    requeridas = ("EMPLEADO", "NOMBRE", "FUENTE")
    faltantes = [nombre for nombre in requeridas if nombre not in canonicas]
    return {
        "columnas": columnas,
        "registros": filas,
        "faltantes": faltantes,
        "mapeo": {
            nombre: canonicas[nombre]
            for nombre in ("EMPLEADO", "NOMBRE", "FUENTE", "DEPARTAMENTO", "PUESTO")
            if nombre in canonicas
        },
    }
