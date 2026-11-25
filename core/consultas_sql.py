"""Consultas SQL de solo lectura y exportación personalizada a Excel.

Este módulo reutiliza la configuración y la conexión corporativa existentes
(``config.sqlserver`` y ``database.connection``). No crea credenciales ni
conexiones paralelas. Las consultas guardadas y el historial se almacenan en
``data/consultas_sql.json``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from config import configuracion as cfg
from config.sqlserver import cargar_configuracion
from core.consulta_sql_manual import ErrorConsultaManual
from database.connection import ErrorConexionSQL, conectar

LOGGER = logging.getLogger("automatizador")

TIPOS = ("Texto", "Número", "Fecha", "Booleano")
MAX_HISTORIAL = 50
TIMEOUT_MAXIMO = 60
_LOCK = threading.RLock()


class ErrorConsultaSQL(ErrorConexionSQL):
    """Error controlado de validación, ejecución o exportación SQL."""


# ---------------------------------------------------------------------------
# Almacenamiento local (consultas guardadas e historial)
# ---------------------------------------------------------------------------
def ruta_almacen() -> Path:
    """Ruta del JSON local; se resuelve en cada llamada por si cambia DATA_DIR."""

    return Path(cfg.DATA_DIR) / "consultas_sql.json"


def _datos_vacios() -> dict[str, Any]:
    return {"guardadas": [], "historial": []}


def _leer_datos() -> dict[str, Any]:
    ruta = ruta_almacen()
    if not ruta.is_file():
        return _datos_vacios()
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("No se pudo leer %s: %s", ruta, exc)
        return _datos_vacios()
    if not isinstance(datos, dict):
        return _datos_vacios()
    guardadas = [
        item
        for item in datos.get("guardadas", [])
        if isinstance(item, dict) and item.get("nombre") and item.get("sql")
    ]
    historial = [
        item for item in datos.get("historial", []) if isinstance(item, dict)
    ]
    return {"guardadas": guardadas, "historial": historial[:MAX_HISTORIAL]}


def cargar_datos() -> dict[str, Any]:
    with _LOCK:
        return _leer_datos()


def guardar_datos(datos: dict[str, Any]) -> Path:
    ruta = ruta_almacen()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_name(f".{ruta.name}.{os.getpid()}.tmp")
    with _LOCK:
        temporal.write_text(
            json.dumps(datos, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporal, ruta)
    return ruta


def guardar_consulta(
    nombre: str,
    sql: str,
    columnas: Iterable[dict[str, Any]] | None = None,
    *,
    id: str | None = None,
) -> dict[str, Any]:
    """Crea o actualiza una consulta guardada. El nombre es único."""

    nombre = str(nombre or "").strip()
    if not nombre:
        raise ValueError("Indique un nombre para la consulta.")
    sentencia = validar_consulta_lectura(sql)
    columnas_limpias = normalizar_columnas(columnas) if columnas else []

    with _LOCK:
        datos = _leer_datos()
        entrada: dict[str, Any] | None = None
        if id:
            entrada = next(
                (g for g in datos["guardadas"] if g.get("id") == id), None
            )
        if entrada is None:
            entrada = next(
                (
                    g
                    for g in datos["guardadas"]
                    if str(g.get("nombre", "")).casefold() == nombre.casefold()
                ),
                None,
            )
        ahora = datetime.now().isoformat(timespec="seconds")
        if entrada is None:
            entrada = {"id": uuid.uuid4().hex, "creado": ahora}
            datos["guardadas"].append(entrada)
        entrada.update(
            {
                "nombre": nombre,
                "sql": sentencia,
                "columnas": columnas_limpias,
                "actualizado": ahora,
            }
        )
        guardar_datos(datos)
        return dict(entrada)


def eliminar_consulta(id: str) -> bool:
    with _LOCK:
        datos = _leer_datos()
        antes = len(datos["guardadas"])
        datos["guardadas"] = [
            g for g in datos["guardadas"] if g.get("id") != id
        ]
        if len(datos["guardadas"]) == antes:
            return False
        guardar_datos(datos)
        return True


def registrar_historial(
    *,
    nombre: str,
    sql: str,
    total: int,
    duracion: float,
    exito: bool,
    mensaje: str = "",
) -> None:
    with _LOCK:
        datos = _leer_datos()
        datos["historial"].insert(
            0,
            {
                "fecha": datetime.now().isoformat(timespec="seconds"),
                "nombre": str(nombre or "(sin nombre)"),
                "sql": str(sql or ""),
                "total": int(total or 0),
                "duracion": round(float(duracion or 0), 3),
                "exito": bool(exito),
                "mensaje": str(mensaje or "")[:1000],
            },
        )
        datos["historial"] = datos["historial"][:MAX_HISTORIAL]
        guardar_datos(datos)


# ---------------------------------------------------------------------------
# Validación de solo lectura
# ---------------------------------------------------------------------------
_PALABRAS_BLOQUEADAS = frozenset(
    {
        "INSERT", "UPDATE", "DELETE", "MERGE", "DROP", "ALTER", "TRUNCATE",
        "CREATE", "EXEC", "EXECUTE", "GRANT", "REVOKE", "DENY", "BACKUP",
        "RESTORE", "DBCC", "SHUTDOWN", "KILL", "BULK", "USE", "SET", "INTO",
        "OPENROWSET", "OPENDATASOURCE", "OPENQUERY", "XP_CMDSHELL",
        "SP_EXECUTESQL", "WAITFOR", "RECONFIGURE", "GO",
    }
)
_FUNCIONES_PELIGROSAS = frozenset(
    {
        "OPENROWSET", "OPENDATASOURCE", "OPENQUERY", "XP_CMDSHELL",
        "SP_EXECUTESQL", "EXEC", "EXECUTE",
    }
)
_PATRON_TOKEN = re.compile(
    r"""
    (?P<comentario>--[^\n]*|/\*.*?\*/)
  | (?P<cadena>N?'(?:''|[^'])*'|"(?:[^"]|"")*")
  | (?P<corchete>\[[^\]]*\])
  | (?P<palabra>[A-Za-z_@#][A-Za-z0-9_@#$]*)
  | (?P<numero>\d+(?:\.\d+)?)
  | (?P<simbolo>.)
    """,
    re.VERBOSE | re.DOTALL | re.IGNORECASE,
)


def _validar_lexico(texto: str) -> str:
    """Respaldo seguro cuando sqlglot no está instalado.

    No es una búsqueda de palabras con expresiones regulares: tokeniza la
    sentencia, ignora cadenas y comentarios, y rechaza cualquier palabra clave
    de escritura, sentencias encadenadas o funciones de ejecución externa.
    """

    tokens = [
        (coincidencia.lastgroup, coincidencia.group(0))
        for coincidencia in _PATRON_TOKEN.finditer(texto)
    ]
    significativos = [
        token
        for token in tokens
        if token[0] != "comentario"
        and not (token[0] == "simbolo" and token[1].isspace())
    ]
    if not significativos:
        raise ErrorConsultaManual("Escriba una consulta SELECT.")

    if significativos[-1] == ("simbolo", ";"):
        significativos = significativos[:-1]
    if any(
        tipo == "simbolo" and valor == ";" for tipo, valor in significativos
    ):
        raise ErrorConsultaManual("Solo se permite una sentencia SQL.")

    primer_palabra = next(
        (
            valor.upper()
            for tipo, valor in significativos
            if tipo == "palabra"
        ),
        "",
    )
    if primer_palabra not in {"SELECT", "WITH"}:
        raise ErrorConsultaManual(
            "Solo se permiten consultas SELECT o WITH ... SELECT."
        )
    for indice, (tipo, valor) in enumerate(significativos):
        if tipo != "palabra":
            continue
        palabra = valor.upper()
        if palabra in _PALABRAS_BLOQUEADAS:
            raise ErrorConsultaManual(
                "La consulta contiene una operación que no es de lectura."
            )
        if (
            palabra == "NEXT"
            and indice + 2 < len(significativos)
            and significativos[indice + 1][1].upper() == "VALUE"
            and significativos[indice + 2][1].upper() == "FOR"
        ):
            raise ErrorConsultaManual(
                "La consulta utiliza una secuencia no permitida."
            )
    return texto.strip()


def validar_consulta_lectura(sql: str) -> str:
    """Valida una única sentencia SELECT/WITH usando AST si está disponible."""

    texto = str(sql or "").strip()
    if not texto:
        raise ErrorConsultaManual("Escriba una consulta SELECT.")

    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        return _validar_lexico(texto)

    try:
        sentencias = [arbol for arbol in sqlglot.parse(texto, read="tsql") if arbol]
    except Exception as exc:  # noqa: BLE001 - errores variables por versión
        # sqlglot puede no reconocer alguna extensión válida de T-SQL. En ese
        # caso se usa la validación léxica, que también bloquea escritura y
        # sentencias múltiples, y el error real se mostrará al ejecutar.
        LOGGER.warning("sqlglot no pudo analizar la consulta: %s", exc)
        return _validar_lexico(texto)
    if len(sentencias) != 1:
        raise ErrorConsultaManual("Solo se permite una sentencia SQL.")

    arbol = sentencias[0]
    raiz = arbol.this if isinstance(arbol, exp.With) else arbol
    if not isinstance(raiz, exp.Select):
        raise ErrorConsultaManual(
            "Solo se permiten consultas SELECT o WITH ... SELECT."
        )

    bloqueadas = tuple(
        clase
        for nombre in (
            "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter",
            "Command", "Transaction", "Use", "Set", "Grant", "Revoke", "Into",
            "TruncateTable", "NextValueFor", "Copy", "LoadData",
        )
        if (clase := getattr(exp, nombre, None)) is not None
    )
    if bloqueadas and any(
        isinstance(nodo, bloqueadas) for nodo in arbol.walk()
    ):
        raise ErrorConsultaManual(
            "La consulta contiene una operación que no es de lectura."
        )

    for nodo in arbol.walk():
        tipo_nodo = type(nodo).__name__.upper()
        if any(
            nombre in tipo_nodo
            for nombre in (
                "OPENROWSET", "OPENDATASOURCE", "OPENQUERY", "NEXTVALUE",
                "EXECUTE",
            )
        ):
            raise ErrorConsultaManual(
                "La consulta incluye una operación SQL no permitida."
            )
        if isinstance(nodo, getattr(exp, "Anonymous", ())):
            nombre = str(nodo.name or "").upper()
            if (
                nombre in _FUNCIONES_PELIGROSAS
                or nombre.startswith("XP_")
                or nombre.startswith("SP_")
            ):
                raise ErrorConsultaManual(
                    f"No se permite la función SQL '{nombre}'."
                )
    return texto


# ---------------------------------------------------------------------------
# Ejecución
# ---------------------------------------------------------------------------
def _claves_y_etiquetas(nombres: Iterable[str]) -> tuple[list[str], list[str]]:
    """Genera claves estables y etiquetas legibles para columnas repetidas."""

    usados: dict[str, int] = {}
    claves: list[str] = []
    etiquetas: list[str] = []
    for bruto in nombres:
        nombre = str(bruto or "COLUMNA")
        usados[nombre] = usados.get(nombre, 0) + 1
        repeticion = usados[nombre]
        claves.append(nombre if repeticion == 1 else f"{nombre}#{repeticion}")
        etiquetas.append(
            nombre if repeticion == 1 else f"{nombre} ({repeticion})"
        )
    return claves, etiquetas


def ejecutar_consulta(
    sql: str,
    *,
    timeout_segundos: int = 60,
) -> dict[str, Any]:
    """Ejecuta un SELECT validado y devuelve todas las filas recuperadas."""

    sentencia = validar_consulta_lectura(sql)
    config = cargar_configuracion()
    if not config.configurada:
        raise ErrorConsultaSQL(
            "La conexión SQL Server no está configurada. Use el botón "
            "«Conexión SQL Server» del menú lateral.",
            "configuracion",
        )

    inicio = time.perf_counter()
    columnas: list[dict[str, Any]] = []
    filas: list[dict[str, Any]] = []
    try:
        with conectar(config) as conexion:
            cursor = conexion.cursor()
            try:
                try:
                    cursor.timeout = max(
                        1, min(int(timeout_segundos), TIMEOUT_MAXIMO)
                    )
                except Exception:  # noqa: BLE001 - no todos los drivers lo admiten
                    pass
                try:
                    cursor.execute(sentencia)
                    if cursor.description is None:
                        raise ErrorConsultaSQL(
                            "La consulta no devolvió un conjunto de resultados.",
                            "sql",
                        )
                    nombres = [
                        str(columna[0]) for columna in cursor.description
                    ]
                    claves, etiquetas = _claves_y_etiquetas(nombres)
                    columnas = [
                        {
                            "clave": clave,
                            "nombre": nombre,
                            "etiqueta": etiqueta,
                            "tipo": "Texto",
                        }
                        for clave, nombre, etiqueta in zip(
                            claves, nombres, etiquetas
                        )
                    ]
                    filas = [
                        dict(zip(claves, fila)) for fila in cursor.fetchall()
                    ]
                except ErrorConsultaSQL:
                    raise
                except Exception as exc:  # noqa: BLE001
                    raise ErrorConsultaSQL(
                        f"Error al ejecutar la consulta: {exc}", "consulta"
                    ) from exc
            finally:
                try:
                    cursor.close()
                except Exception:  # noqa: BLE001
                    pass
            try:
                conexion.rollback()
            except Exception:  # noqa: BLE001
                pass
    except ErrorConsultaSQL:
        raise
    except ErrorConexionSQL as exc:
        raise ErrorConsultaSQL(str(exc), exc.categoria) from exc

    for columna in columnas:
        columna["tipo"] = inferir_tipo(
            [fila.get(columna["clave"]) for fila in filas[:200]]
        )

    return {
        "sql": sentencia,
        "columnas": columnas,
        "filas": filas,
        "total": len(filas),
        "duracion": time.perf_counter() - inicio,
    }


# ---------------------------------------------------------------------------
# Configuración de columnas y correspondencia estable de datos
# ---------------------------------------------------------------------------
def inferir_tipo(valores: Iterable[Any]) -> str:
    vistos = [valor for valor in valores if valor is not None]
    if not vistos:
        return "Texto"
    if all(isinstance(valor, bool) for valor in vistos):
        return "Booleano"
    if any(isinstance(valor, (datetime, date)) for valor in vistos):
        return "Fecha"
    if all(
        isinstance(valor, (int, float, Decimal)) and not isinstance(valor, bool)
        for valor in vistos
    ):
        return "Número"
    return "Texto"


def normalizar_columnas(
    columnas: Iterable[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    resultado: list[dict[str, Any]] = []
    for indice, columna in enumerate(columnas or []):
        if not isinstance(columna, dict):
            continue
        origen = (
            "manual"
            if str(columna.get("origen", "")).lower() == "manual"
            else "sql"
        )
        campo = str(columna.get("campo") or "")
        encabezado = str(
            columna.get("encabezado") or campo or f"COLUMNA_{indice + 1}"
        )
        tipo = str(columna.get("tipo") or "Texto")
        if tipo not in TIPOS:
            tipo = "Texto"
        valor = columna.get("valor", "")
        resultado.append(
            {
                "id": str(
                    columna.get("id")
                    or f"{origen}:{campo or uuid.uuid4().hex}"
                ),
                "origen": origen,
                "campo": campo,
                "encabezado": encabezado,
                "tipo": tipo,
                "valor": "" if valor is None else str(valor),
            }
        )
    return resultado


def columnas_desde_resultado(
    columnas: Iterable[dict[str, Any]],
    filas: Iterable[dict[str, Any]] = (),
) -> list[dict[str, Any]]:
    filas = list(filas)
    resultado: list[dict[str, Any]] = []
    for columna in columnas:
        clave = str(columna.get("clave") or columna.get("nombre") or "")
        if not clave:
            continue
        resultado.append(
            {
                "id": f"sql:{clave}",
                "origen": "sql",
                "campo": clave,
                "encabezado": str(
                    columna.get("etiqueta")
                    or columna.get("nombre")
                    or clave
                ),
                "tipo": inferir_tipo(
                    [fila.get(clave) for fila in filas[:200]]
                ),
                "valor": "",
            }
        )
    return resultado


def _convertir_manual(valor: Any, tipo: str) -> Any:
    texto = "" if valor is None else str(valor).strip()
    if tipo == "Texto":
        return texto
    if tipo == "Número":
        if not texto:
            return None
        try:
            return int(texto)
        except ValueError:
            try:
                return float(texto.replace(",", "."))
            except ValueError:
                return texto
    if tipo == "Fecha":
        if not texto:
            return None
        for formato in (
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%d-%m-%Y",
            "%m/%d/%Y",
            "%Y-%m-%d %H:%M:%S",
            "%d/%m/%Y %H:%M:%S",
        ):
            try:
                return datetime.strptime(texto, formato)
            except ValueError:
                continue
        return texto
    if tipo == "Booleano":
        if not texto:
            return None
        normalizado = texto.casefold()
        if normalizado in {"1", "si", "sí", "true", "verdadero", "yes", "y"}:
            return True
        if normalizado in {"0", "no", "false", "falso", "n"}:
            return False
        return texto
    return texto


def construir_tabla(
    columnas: Iterable[dict[str, Any]],
    filas: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Relaciona cada columna con su campo SQL original, nunca con su posición.

    El orden de ``columnas`` define el orden del Excel, pero los valores de las
    columnas SQL se leen siempre con ``campo`` (la clave estable del resultado).
    Las columnas manuales insertan su valor predeterminado en cada fila.
    """

    configuradas = normalizar_columnas(columnas)
    encabezados = [columna["encabezado"] for columna in configuradas]
    tipos = [columna["tipo"] for columna in configuradas]
    salida: list[list[Any]] = []
    for fila in filas:
        valores: list[Any] = []
        for columna in configuradas:
            if columna["origen"] == "manual":
                valores.append(
                    _convertir_manual(columna["valor"], columna["tipo"])
                )
            else:
                valores.append(fila.get(columna["campo"]))
        salida.append(valores)
    return {
        "encabezados": encabezados,
        "filas": salida,
        "tipos": tipos,
        "columnas": configuradas,
    }


# ---------------------------------------------------------------------------
# Exportación Excel
# ---------------------------------------------------------------------------
def _valor_excel(valor: Any) -> Any:
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor.replace(tzinfo=None)
    if isinstance(valor, date):
        return valor
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, (bytes, bytearray)):
        return bytes(valor).hex()
    if isinstance(valor, (str, int, float, bool)):
        return valor
    return str(valor)


def exportar_excel(
    ruta: str | Path,
    encabezados: Iterable[str],
    filas: Iterable[Iterable[Any]],
    tipos: Iterable[str] | None = None,
) -> Path:
    """Genera un .xlsx con encabezados, filtros, panel congelado y formatos."""

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    encabezados = [str(encabezado) for encabezado in encabezados]
    if not encabezados:
        raise ValueError("No hay columnas para exportar.")
    filas = [list(fila) for fila in filas]
    tipos = list(tipos or ["Texto"] * len(encabezados))
    if len(tipos) < len(encabezados):
        tipos.extend(["Texto"] * (len(encabezados) - len(tipos)))

    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    libro = Workbook()
    hoja = libro.active
    hoja.title = "Consulta SQL"

    hoja.append(encabezados)
    for fila in filas:
        hoja.append([_valor_excel(valor) for valor in fila])

    relleno = PatternFill("solid", fgColor="102D68")
    borde = Border(
        left=Side(style="thin", color="DCE5F2"),
        right=Side(style="thin", color="DCE5F2"),
        top=Side(style="thin", color="DCE5F2"),
        bottom=Side(style="thin", color="DCE5F2"),
    )
    for celda in hoja[1]:
        celda.fill = relleno
        celda.font = Font(
            name="Segoe UI", size=10, bold=True, color="FFFFFF"
        )
        celda.alignment = Alignment(
            horizontal="center", vertical="center", wrap_text=True
        )
        celda.border = borde
    hoja.row_dimensions[1].height = 24

    for fila in hoja.iter_rows(min_row=2):
        for celda in fila:
            celda.font = Font(name="Segoe UI", size=10)
            celda.border = borde

    ultima_columna = get_column_letter(len(encabezados))
    hoja.freeze_panes = "A2"
    hoja.auto_filter.ref = (
        f"A1:{ultima_columna}{max(1, len(filas) + 1)}"
    )

    for indice, encabezado in enumerate(encabezados):
        letra = get_column_letter(indice + 1)
        maximo = len(encabezado)
        for fila in filas[:500]:
            valor = fila[indice] if indice < len(fila) else ""
            maximo = max(maximo, len(str(valor if valor is not None else "")))
        hoja.column_dimensions[letra].width = min(max(10, maximo + 2), 60)

        tipo = tipos[indice]
        valores = [
            fila[indice] for fila in filas if indice < len(fila)
        ]
        if tipo == "Fecha":
            con_hora = any(
                isinstance(valor, datetime)
                and (valor.hour or valor.minute or valor.second)
                for valor in valores
            )
            formato = "dd/mm/yyyy hh:mm:ss" if con_hora else "dd/mm/yyyy"
            for celda in hoja[letra][1:]:
                celda.number_format = formato
        elif tipo == "Número":
            decimales = any(
                isinstance(valor, (float, Decimal)) for valor in valores
            )
            formato = "#,##0.00" if decimales else "#,##0"
            for celda in hoja[letra][1:]:
                celda.number_format = formato
                celda.alignment = Alignment(horizontal="right")
        elif tipo == "Booleano":
            for celda in hoja[letra][1:]:
                celda.alignment = Alignment(horizontal="center")

    libro.save(ruta)
    return ruta
