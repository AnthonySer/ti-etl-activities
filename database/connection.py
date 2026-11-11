"""Conexión segura y de solo lectura a SQL Server.

``pyodbc`` es opcional: si no está instalado, la aplicación sigue funcionando
con la caché local y la interfaz muestra un mensaje comprensible. Ninguna
consulta puede modificar la base corporativa.
"""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from typing import Any, Iterator

from config.sqlserver import ConfiguracionSQL, validar_configuracion

LOGGER = logging.getLogger("automatizador")


class ErrorConexionSQL(Exception):
    """Error controlado de conexión, autenticación o consulta."""

    def __init__(self, mensaje: str, categoria: str = "desconocido") -> None:
        super().__init__(mensaje)
        self.categoria = categoria


try:  # pragma: no cover - depende del entorno
    import pyodbc as _pyodbc
except Exception:  # noqa: BLE001 - ImportError o falta de controladores
    _pyodbc = None

pyodbc = _pyodbc

_SQL_PROHIBIDO = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|TRUNCATE|CREATE|EXEC|EXECUTE|"
    r"GRANT|REVOKE|DENY|BACKUP|RESTORE|DBCC|SHUTDOWN|KILL|BULK)\b",
    re.IGNORECASE,
)


def controlador_disponible() -> bool:
    return pyodbc is not None


def _escapar_driver(driver: str) -> str:
    return "{" + driver.replace("}", "}}") + "}"

def _escapar_valor_odbc(valor: str) -> str:
    """Escapa valores para utilizarlos de forma segura en una cadena ODBC."""
    return "{" + str(valor).replace("}", "}}") + "}"


def construir_cadena_conexion(config: ConfiguracionSQL) -> str:
    """Construye la cadena ODBC usando un controlador instalado."""

    validada = validar_configuracion(config)

    if pyodbc is None:
        raise ErrorConexionSQL(
            "El módulo pyodbc no está instalado.",
            "driver",
        )

    # Consultar los controladores realmente disponibles.
    try:
        drivers_instalados = pyodbc.drivers()
    except Exception as exc:
        raise ErrorConexionSQL(
            f"No se pudieron consultar los controladores ODBC: {exc}",
            "driver",
        ) from exc

    # Priorizar el controlador configurado y luego buscar alternativas.
    candidatos = [
        validada.driver,
        "ODBC Driver 18 for SQL Server",
        "ODBC Driver 17 for SQL Server",
        "ODBC Driver 13 for SQL Server",
    ]

    driver_seleccionado = next(
        (
            driver
            for driver in candidatos
            if driver and driver in drivers_instalados
        ),
        None,
    )

    if not driver_seleccionado:
        raise ErrorConexionSQL(
            "No se encontró un controlador ODBC compatible. "
            f"Controladores detectados: {drivers_instalados}",
            "driver",
        )

    # LOGGER.info("Controlador ODBC seleccionado: %s", driver_seleccionado)
    LOGGER.info(
        "Configuración SQL: servidor=%s, base=%s, usuario=%s, "
        "autenticacion_sql=%s, driver=%s",
        validada.servidor,
        validada.base_datos,
        validada.usuario,
        validada.usa_sql_auth,
        driver_seleccionado,
    )

    partes = [
        f"DRIVER={_escapar_driver(driver_seleccionado)}",
        f"SERVER={validada.servidor}",
        f"DATABASE={validada.base_datos}",
        f"Encrypt={'yes' if validada.encriptar else 'no'}",
        f"TrustServerCertificate={'yes' if validada.confiar_certificado else 'no'}",
        f"Connection Timeout={validada.timeout}",
    ]

    if validada.usa_sql_auth:
        partes.append(f"UID={_escapar_valor_odbc(validada.usuario)}")
        partes.append(f"PWD={_escapar_valor_odbc(validada.contrasena)}")
    else:
        partes.append("Trusted_Connection=yes")

    return ";".join(partes) + ";"


def _clasificar_error(exc: Exception) -> tuple[str, str]:
    texto = str(exc)
    sqlstate = ""
    args = getattr(exc, "args", ())
    if args:
        sqlstate = str(args[0] or "")
    codigo = f"{sqlstate} {texto}".upper()
    if "IM002" in codigo or "DATA SOURCE NAME NOT FOUND" in codigo:
        return "Controlador ODBC no encontrado. Instale Microsoft ODBC Driver 17/18.", "driver"
    if "28000" in codigo or "LOGIN FAILED" in codigo or "AUTENTICACIÓN" in codigo:
        return "Error de autenticación con SQL Server.", "autenticacion"
    if "HYT00" in codigo or "TIMEOUT" in codigo or "TIEMPO DE ESPERA" in codigo:
        return "Se agotó el tiempo de espera al conectar con SQL Server.", "timeout"
    if "08S01" in codigo or "08001" in codigo or "NETWORK" in codigo or "TCP" in codigo:
        return "No se pudo alcanzar el servidor SQL Server (red o instancia).", "servidor"
    if "4060" in codigo or "CANNOT OPEN DATABASE" in codigo:
        return "No se pudo abrir la base de datos configurada.", "base_datos"
    return "No se pudo conectar con SQL Server.", "servidor"


@contextmanager
def conectar(config: ConfiguracionSQL) -> Iterator[Any]:
    """Abre una conexión y la cierra siempre, incluso ante errores."""

    if pyodbc is None:
        raise ErrorConexionSQL(
            "El módulo 'pyodbc' no está instalado. "
            "Instale requirements.txt para habilitar SQL Server.",
            "driver",
        )
    if not config.configurada:
        raise ErrorConexionSQL(
            "La conexión no está configurada: indique servidor y base de datos.",
            "configuracion",
        )
    cadena = construir_cadena_conexion(config)
    conexion = None
    try:
        conexion = pyodbc.connect(cadena, timeout=int(config.timeout), autocommit=False)
        yield conexion
    except Exception as exc:  # noqa: BLE001 - se traduce a error controlado
        if isinstance(exc, ErrorConexionSQL):
            raise
        mensaje, categoria = _clasificar_error(exc)
        LOGGER.error("Conexión SQL Server: %s (%s)", mensaje, categoria)
        raise ErrorConexionSQL(mensaje, categoria) from exc
    finally:
        if conexion is not None:
            try:
                conexion.close()
            except Exception:  # noqa: BLE001 - el cierre no debe ocultar el error
                LOGGER.debug("No se pudo cerrar la conexión SQL Server", exc_info=True)


def _sin_literales(sql: str) -> str:
    sin_cadenas = re.sub(r"'(?:''|[^'])*'", "''", sql)
    sin_bloques = re.sub(r"/\*.*?\*/", " ", sin_cadenas, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", sin_bloques)


def validar_solo_lectura(sql: str) -> None:
    """Rechaza cualquier sentencia que no sea SELECT/WITH de solo lectura."""

    limpio = _sin_literales(sql).strip()
    if not limpio:
        raise ErrorConexionSQL("La consulta SQL está vacía.", "sql")
    if not re.match(r"^(SELECT|WITH)\b", limpio, flags=re.IGNORECASE):
        raise ErrorConexionSQL(
            "Por seguridad solo se permiten consultas SELECT.", "sql"
        )
    if _SQL_PROHIBIDO.search(limpio):
        raise ErrorConexionSQL(
            "La consulta contiene una operación no permitida. "
            "El sistema es de solo lectura.",
            "sql",
        )


def ejecutar_consulta(
    conexion: Any,
    sql: str,
    parametros: tuple[Any, ...] | list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Ejecuta un SELECT parametrizado y devuelve filas como diccionarios."""

    validar_solo_lectura(sql)
    cursor = conexion.cursor()
    try:
        cursor.execute(sql, list(parametros or []))
        if cursor.description is None:
            return []
        columnas = [str(columna[0]) for columna in cursor.description]
        return [dict(zip(columnas, fila)) for fila in cursor.fetchall()]
    except Exception as exc:  # noqa: BLE001
        mensaje, categoria = _clasificar_error(exc)
        if categoria == "servidor" and "consulta" not in str(exc).lower():
            categoria = "consulta"
            mensaje = f"No se pudo ejecutar la consulta: {exc}"
        raise ErrorConexionSQL(mensaje, categoria) from exc
    finally:
        try:
            cursor.close()
        except Exception:  # noqa: BLE001
            pass


def probar_conexion(config: ConfiguracionSQL) -> dict[str, Any]:
    """Prueba real de conexión. Devuelve estado, mensaje y categoría."""

    try:
        validada = validar_configuracion(config)
        with conectar(validada) as conexion:
            ejecutar_consulta(conexion, "SELECT 1 AS OK")
        return {
            "ok": True,
            "estado": "conectado",
            "categoria": "ok",
            "mensaje": (
                f"Conexión correcta con {validada.servidor} / "
                f"{validada.base_datos}."
            ),
        }
    except ErrorConexionSQL as exc:
        return {
            "ok": False,
            "estado": "error",
            "categoria": exc.categoria,
            "mensaje": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        mensaje, categoria = _clasificar_error(exc)
        return {
            "ok": False,
            "estado": "error",
            "categoria": categoria,
            "mensaje": mensaje,
        }
