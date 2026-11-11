"""Configuración de la conexión corporativa a SQL Server (EXACTUS).

La contraseña nunca se guarda en claro: ``core.seguridad`` la cifra con DPAPI
(cuenta de Windows) antes de escribirla en ``config/sqlserver.json``. El archivo
de configuración se escribe de forma atómica.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from core.seguridad import desproteger_texto, proteger_texto

LOGGER = logging.getLogger("automatizador")

# Las 18 empresas autorizadas. Los nombres de esquema SQL solo pueden salir de
# esta lista: nunca se interpola texto libre del usuario como identificador.
EMPRESAS: tuple[str, ...] = (
    "ANDINA",
    "CALCA",
    "CONDOR",
    "COYA",
    "ESTRATEGO",
    "FILASUR",
    "INKA",
    "MUNDIAL",
    "NK",
    "POLYSOL",
    "PROEXPLOR",
    "RELTEX",
    "SERVIAM",
    "TANKARPATA",
    "TARAY",
    "TMI",
    "TROPICAL",
    "YACLOT",
)

AUTENTICACIONES = ("windows", "sql")
DRIVERS_CONOCIDOS = (
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
    "ODBC Driver 13 for SQL Server",
    "SQL Server Native Client 11.0",
    "SQL Server",
)

CONFIG_SQL_PATH = Path(
    os.environ.get("INKACROPS_SQL_CONFIG", "")
) if os.environ.get("INKACROPS_SQL_CONFIG") else None

_IDENTIFICADOR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_SERVIDOR = re.compile(r"^[A-Za-z0-9._\\-]+(?:,\d{1,5})?$")


class ErrorConfiguracionSQL(ValueError):
    """La configuración de conexión no es válida."""


@dataclass
class ConfiguracionSQL:
    habilitado: bool = False
    servidor: str = ""
    puerto: int = 1433
    base_datos: str = "EXACTUS"
    autenticacion: str = "windows"
    usuario: str = ""
    contrasena: str = field(default="", repr=False)
    driver: str = "ODBC Driver 17 for SQL Server"
    timeout: int = 10
    encriptar: bool = True
    confiar_certificado: bool = True
    auto_sync: bool = True
    lote: int = 200
    empresas: list[str] = field(default_factory=list)
    columna_depto_empleado: str = "DEPARTAMENTO"
    columna_depto_catalogo: str = "DEPARTAMENTO"
    columna_puesto_empleado: str = "PUESTO"
    columna_puesto_catalogo: str = "PUESTO"
    ultima_prueba: str = ""
    ultimo_estado: str = "desconectado"
    ultimo_error: str = ""
    ultima_sincronizacion: str = ""

    def __post_init__(self) -> None:
        if not self.empresas:
            self.empresas = list(EMPRESAS)

    @property
    def usa_sql_auth(self) -> bool:
        return self.autenticacion == "sql"

    @property
    def configurada(self) -> bool:
        return bool(self.servidor.strip() and self.base_datos.strip())

    def empresas_activas(self) -> list[str]:
        permitidas = set(EMPRESAS)
        activas = [empresa for empresa in self.empresas if empresa in permitidas]
        return activas or list(EMPRESAS)

    def como_dict(self, incluir_secreto: bool = False) -> dict[str, Any]:
        datos = asdict(self)
        if not incluir_secreto:
            datos.pop("contrasena", None)
        return datos

    def clonar(self, **cambios: Any) -> "ConfiguracionSQL":
        return replace(self, **cambios)


def ruta_configuracion(ruta: Path | str | None = None) -> Path:
    if ruta is not None:
        return Path(ruta)
    if CONFIG_SQL_PATH is not None:
        return CONFIG_SQL_PATH
    # Se resuelve en tiempo de llamada para respetar la ubicación del proyecto.
    from config.configuracion import BASE_DIR

    return BASE_DIR / "config" / "sqlserver.json"


def _validar_identificador(valor: str, campo: str) -> str:
    valor = str(valor or "").strip()
    if not _IDENTIFICADOR.match(valor):
        raise ErrorConfiguracionSQL(
            f"El campo '{campo}' solo admite letras, números y guion bajo "
            "(sin corchetes ni punto y coma)."
        )
    return valor


def validar_configuracion(config: ConfiguracionSQL) -> ConfiguracionSQL:
    """Valida y normaliza la configuración antes de conectar o guardar."""

    servidor = str(config.servidor or "").strip()
    if servidor and not _SERVIDOR.match(servidor):
        raise ErrorConfiguracionSQL(
            "El servidor solo admite nombre/IP, instancia con '\\' y puerto opcional."
        )

    try:
        puerto = int(config.puerto)
    except (TypeError, ValueError) as exc:
        raise ErrorConfiguracionSQL("El puerto debe ser un número entero.") from exc
    if not 1 <= puerto <= 65535:
        raise ErrorConfiguracionSQL("El puerto debe estar entre 1 y 65535.")

    try:
        timeout = int(config.timeout)
    except (TypeError, ValueError) as exc:
        raise ErrorConfiguracionSQL("El timeout debe ser un número entero.") from exc
    if not 1 <= timeout <= 120:
        raise ErrorConfiguracionSQL("El timeout debe estar entre 1 y 120 segundos.")

    try:
        lote = int(config.lote)
    except (TypeError, ValueError) as exc:
        raise ErrorConfiguracionSQL("El tamaño de lote debe ser un número entero.") from exc
    if not 1 <= lote <= 1000:
        raise ErrorConfiguracionSQL("El tamaño de lote debe estar entre 1 y 1000.")

    autenticacion = str(config.autenticacion or "windows").lower()
    if autenticacion not in AUTENTICACIONES:
        raise ErrorConfiguracionSQL(
            "El tipo de autenticación debe ser 'windows' o 'sql'."
        )

    usuario = str(config.usuario or "").strip()
    if autenticacion == "sql" and config.habilitado and not usuario:
        raise ErrorConfiguracionSQL(
            "La autenticación SQL Server requiere un usuario."
        )
    if autenticacion == "sql" and config.habilitado and not config.contrasena:
        raise ErrorConfiguracionSQL(
            "La autenticación SQL Server requiere una contraseña."
        )

    base_datos = _validar_identificador(config.base_datos, "base de datos")
    driver = str(config.driver or "").strip()
    if not driver:
        raise ErrorConfiguracionSQL("Debe indicar un controlador ODBC.")
    columna_depto_empleado = _validar_identificador(
        config.columna_depto_empleado, "columna de departamento en EMPLEADO"
    )
    columna_depto_catalogo = _validar_identificador(
        config.columna_depto_catalogo, "columna de departamento en DEPARTAMENTO"
    )
    columna_puesto_empleado = _validar_identificador(
        config.columna_puesto_empleado, "columna de puesto en EMPLEADO"
    )
    columna_puesto_catalogo = _validar_identificador(
        config.columna_puesto_catalogo, "columna de puesto en PUESTO"
    )

    empresas_solicitadas = list(config.empresas or EMPRESAS)
    desconocidas = [empresa for empresa in empresas_solicitadas if empresa not in EMPRESAS]
    if desconocidas:
        raise ErrorConfiguracionSQL(
            "Empresas no autorizadas: " + ", ".join(desconocidas)
        )
    # Se conserva el orden oficial y se eliminan duplicados.
    empresas = [empresa for empresa in EMPRESAS if empresa in set(empresas_solicitadas)]

    return config.clonar(
        servidor=servidor,
        puerto=puerto,
        timeout=timeout,
        lote=lote,
        autenticacion=autenticacion,
        usuario=usuario,
        base_datos=base_datos,
        driver=driver,
        empresas=empresas,
        columna_depto_empleado=columna_depto_empleado,
        columna_depto_catalogo=columna_depto_catalogo,
        columna_puesto_empleado=columna_puesto_empleado,
        columna_puesto_catalogo=columna_puesto_catalogo,
    )


def _leer_json(ruta: Path) -> dict[str, Any]:
    if not ruta.is_file():
        return {}
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("No se pudo leer la configuración SQL: %s", exc)
        return {}
    return datos if isinstance(datos, dict) else {}


def cargar_configuracion(ruta: Path | str | None = None) -> ConfiguracionSQL:
    """Carga la configuración y descifra la contraseña en memoria."""

    archivo = ruta_configuracion(ruta)
    datos = _leer_json(archivo)
    if not datos:
        return ConfiguracionSQL()

    permitidos = set(ConfiguracionSQL.__dataclass_fields__)
    limpio: dict[str, Any] = {}
    for clave, valor in datos.items():
        if clave in permitidos and clave != "contrasena":
            limpio[clave] = valor
    contrasena = desproteger_texto(str(datos.get("contrasena_cifrada") or ""))
    if not contrasena and datos.get("contrasena"):
        contrasena = str(datos.get("contrasena") or "")
    limpio["contrasena"] = contrasena
    try:
        return validar_configuracion(ConfiguracionSQL(**limpio))
    except (TypeError, ErrorConfiguracionSQL) as exc:
        LOGGER.warning("Configuración SQL inválida; se usan valores seguros: %s", exc)
        return ConfiguracionSQL()


def guardar_configuracion(
    config: ConfiguracionSQL,
    ruta: Path | str | None = None,
    *,
    incluir_contrasena: bool = True,
) -> Path:
    """Guarda la configuración de forma atómica y cifra la contraseña."""

    validada = validar_configuracion(config)
    archivo = ruta_configuracion(ruta)
    archivo.parent.mkdir(parents=True, exist_ok=True)

    datos = validada.como_dict()
    if incluir_contrasena and validada.contrasena:
        datos["contrasena_cifrada"] = proteger_texto(validada.contrasena)
    elif archivo.is_file():
        # Conservar la contraseña ya guardada al cambiar solo parámetros.
        previos = _leer_json(archivo)
        if previos.get("contrasena_cifrada"):
            datos["contrasena_cifrada"] = previos["contrasena_cifrada"]

    temporal = archivo.with_name(f".{archivo.name}.{os.getpid()}.tmp")
    temporal.write_text(
        json.dumps(datos, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(temporal, archivo)
    LOGGER.info("Configuración SQL guardada en %s", archivo)
    return archivo


def registrar_prueba(
    config: ConfiguracionSQL,
    *,
    conectado: bool,
    mensaje: str = "",
) -> ConfiguracionSQL:
    """Devuelve la configuración con el resultado real de la última prueba."""

    ahora = datetime.now().astimezone().isoformat(timespec="seconds")
    return config.clonar(
        ultima_prueba=ahora,
        ultimo_estado="conectado" if conectado else "error",
        ultimo_error="" if conectado else str(mensaje)[:500],
    )


def empresas_desde(valores: Iterable[str]) -> list[str]:
    """Normaliza una selección de empresas contra la lista permitida."""

    seleccion = {str(valor).strip().upper() for valor in valores}
    return [empresa for empresa in EMPRESAS if empresa in seleccion]
