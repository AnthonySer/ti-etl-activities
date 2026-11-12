"""Procedencia persistente de las áreas asignadas a cada usuario.

El maestro ``USUARIO -> ÁREA`` se conserva tal como está; este módulo añade una
capa independiente de trazabilidad que responde a la pregunta: ¿de dónde salió
el área que se escribió en el ticket?

Orígenes admitidos:

* ``SQL_SERVER``: obtenida de ``DEPARTAMENTO.DESCRIPCION``.
* ``MANUAL``: asignada por una persona (maestro local o ventana de validación).
* ``LEGACY``: conservada del propio archivo de tickets por compatibilidad.

La protección de asignaciones manuales se apoya en este registro: una consulta
automática nunca reemplaza un área cuyo origen sea ``MANUAL``. El cambio se
reporta como conflicto para revisión.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from config import configuracion as cfg
from core.archivos import ErrorArchivo, escribir_json_atomico

LOGGER = logging.getLogger("automatizador")

SQL_SERVER = "SQL_SERVER"
MANUAL = "MANUAL"
LEGACY = "LEGACY"

ORIGENES = (SQL_SERVER, MANUAL, LEGACY)
MAX_HISTORIAL = 4000


def ruta_procedencia() -> Path:
    return Path(cfg.DATA_DIR) / "procedencia_areas.json"


def _estructura_vacia() -> dict[str, Any]:
    return {"version": 1, "registros": {}, "historial": []}


def cargar_procedencia(ruta: Path | str | None = None) -> dict[str, Any]:
    """Carga el registro; ante un archivo corrupto devuelve una estructura vacía."""

    import json

    archivo = Path(ruta or ruta_procedencia())
    if not archivo.is_file():
        return _estructura_vacia()
    try:
        datos = json.loads(archivo.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("No se pudo leer la procedencia de áreas: %s", exc)
        return _estructura_vacia()
    if not isinstance(datos, dict):
        return _estructura_vacia()
    registros = datos.get("registros")
    historial = datos.get("historial")
    if not isinstance(registros, dict):
        registros = {}
    if not isinstance(historial, list):
        historial = []
    return {"version": 1, "registros": registros, "historial": historial}


def guardar_procedencia(
    datos: dict[str, Any],
    ruta: Path | str | None = None,
) -> Path:
    archivo = Path(ruta or ruta_procedencia())
    historial = datos.get("historial") or []
    if len(historial) > MAX_HISTORIAL:
        datos = dict(datos)
        datos["historial"] = historial[-MAX_HISTORIAL:]
    return escribir_json_atomico(datos, archivo)


def _clave(valor: Any) -> str:
    from database.normalizador import normalizar_nombre

    return normalizar_nombre(valor)


def _trazabilidad(detalle: dict[str, Any] | None) -> dict[str, str]:
    """Normaliza los datos de la decisión sin obligar a quien no los tenga."""

    if not isinstance(detalle, dict):
        return {}
    campos = (
        "empleado",
        "nombre",
        "empresa",
        "fuente",
        "departamento",
        "area",
        "fecha",
        "tipo_resolucion",
    )
    salida: dict[str, str] = {}
    for campo in campos:
        valor = detalle.get(campo)
        if valor not in (None, ""):
            salida[campo] = str(valor)
    return salida


def registrar(
    usuario: Any,
    area: Any,
    origen: str,
    *,
    id_ejecucion: str = "",
    nota: str = "",
    ruta: Path | str | None = None,
    guardar: bool = True,
    detalle: dict[str, Any] | None = None,
    empleado: str = "",
    nombre: str = "",
    empresa: str = "",
    fuente: str = "",
    departamento: str = "",
    tipo_resolucion: str = "",
) -> dict[str, Any] | None:
    """Registra o actualiza la procedencia de un usuario.

    Una asignación ``MANUAL`` siempre prevalece. Solo otra asignación manual
    puede reemplazarla; las de ``SQL_SERVER`` o ``LEGACY`` no la pisan.

    Los parámetros opcionales conservan la trazabilidad de la decisión
    (código, nombre, empresa, fuente, departamento, área y tipo de resolución)
    sin cambiar la firma histórica de tres argumentos.
    """

    clave = _clave(usuario)
    area_texto = str(area or "").strip()
    origen = str(origen or "").upper()
    if not clave or not area_texto or origen not in ORIGENES:
        return None

    datos = cargar_procedencia(ruta)
    registros: dict[str, Any] = datos["registros"]
    previo = registros.get(clave) or {}
    if previo.get("origen") == MANUAL and origen != MANUAL:
        LOGGER.debug(
            "Se conserva la asignación manual de %s frente a %s", clave, origen
        )
        return previo

    ahora = datetime.now().astimezone().isoformat(timespec="seconds")
    registro = {
        "usuario": str(usuario).strip(),
        "clave": clave,
        "area": area_texto,
        "origen": origen,
        "actualizado": ahora,
        "id_ejecucion": str(id_ejecucion or ""),
        "nota": str(nota or "")[:500],
    }
    trazable = _trazabilidad(detalle)
    for campo, valor in {
        "empleado": empleado,
        "nombre": nombre,
        "empresa": empresa,
        "fuente": fuente,
        "departamento": departamento,
        "tipo_resolucion": tipo_resolucion,
    }.items():
        if valor not in (None, ""):
            trazable[campo] = str(valor)
    if trazable:
        trazable.setdefault("area", area_texto)
        trazable.setdefault("fecha", ahora)
        registro["trazabilidad"] = trazable
    registros[clave] = registro
    datos["historial"].append(
        {
            "clave": clave,
            "usuario": registro["usuario"],
            "area_anterior": str(previo.get("area") or ""),
            "area_nueva": area_texto,
            "origen_anterior": str(previo.get("origen") or ""),
            "origen_nuevo": origen,
            "fecha": ahora,
            "id_ejecucion": str(id_ejecucion or ""),
            "trazabilidad": dict(trazable),
        }
    )

    if guardar:
        guardar_procedencia(datos, ruta)
    return registro


def registrar_lote(
    elementos: Iterable[dict[str, Any]],
    *,
    id_ejecucion: str = "",
    ruta: Path | str | None = None,
) -> int:
    """Registra varias asignaciones en una sola escritura."""

    datos = cargar_procedencia(ruta)
    registros: dict[str, Any] = datos["registros"]
    ahora = datetime.now().astimezone().isoformat(timespec="seconds")
    cantidad = 0
    for item in elementos:
        clave = _clave(item.get("usuario"))
        area = str(item.get("area") or "").strip()
        origen = str(item.get("origen") or "").upper()
        if not clave or not area or origen not in ORIGENES:
            continue
        previo = registros.get(clave) or {}
        if previo.get("origen") == MANUAL and origen != MANUAL:
            continue
        registro = {
            "usuario": str(item.get("usuario") or "").strip(),
            "clave": clave,
            "area": area,
            "origen": origen,
            "actualizado": ahora,
            "id_ejecucion": str(item.get("id_ejecucion") or id_ejecucion),
            "nota": str(item.get("nota") or "")[:500],
        }
        trazable = _trazabilidad(item.get("trazabilidad") or item.get("detalle"))
        if trazable:
            trazable.setdefault("area", area)
            trazable.setdefault("fecha", ahora)
            registro["trazabilidad"] = trazable
        registros[clave] = registro
        datos["historial"].append(
            {
                "clave": clave,
                "usuario": registro["usuario"],
                "area_anterior": str(previo.get("area") or ""),
                "area_nueva": area,
                "origen_anterior": str(previo.get("origen") or ""),
                "origen_nuevo": origen,
                "fecha": ahora,
                "id_ejecucion": registro["id_ejecucion"],
                "trazabilidad": dict(trazable),
            }
        )
        cantidad += 1
    if cantidad:
        guardar_procedencia(datos, ruta)
    return cantidad


def obtener(usuario: Any, ruta: Path | str | None = None) -> dict[str, Any]:
    clave = _clave(usuario)
    if not clave:
        return {}
    return dict(cargar_procedencia(ruta)["registros"].get(clave) or {})


def origen_de(usuario: Any, ruta: Path | str | None = None) -> str:
    return str(obtener(usuario, ruta).get("origen") or "")


def area_de(usuario: Any, ruta: Path | str | None = None) -> str:
    return str(obtener(usuario, ruta).get("area") or "")


def es_manual(usuario: Any, ruta: Path | str | None = None) -> bool:
    return origen_de(usuario, ruta) == MANUAL


def como_dataframe(ruta: Path | str | None = None):
    """Vista tabular para reportes y auditoría."""

    import pandas as pd

    datos = cargar_procedencia(ruta)["registros"]
    columnas = [
        "USUARIO",
        "CLAVE",
        "AREA",
        "ORIGEN",
        "ACTUALIZADO",
        "ID_EJECUCION",
        "NOTA",
        "EMPLEADO",
        "NOMBRE",
        "EMPRESA",
        "FUENTE",
        "DEPARTAMENTO",
        "TIPO_RESOLUCION",
    ]
    filas = []
    for item in datos.values():
        trazable = item.get("trazabilidad") or {}
        filas.append(
            {
                "USUARIO": item.get("usuario", ""),
                "CLAVE": item.get("clave", ""),
                "AREA": item.get("area", ""),
                "ORIGEN": item.get("origen", ""),
                "ACTUALIZADO": item.get("actualizado", ""),
                "ID_EJECUCION": item.get("id_ejecucion", ""),
                "NOTA": item.get("nota", ""),
                "EMPLEADO": trazable.get("empleado", ""),
                "NOMBRE": trazable.get("nombre", ""),
                "EMPRESA": trazable.get("empresa", ""),
                "FUENTE": trazable.get("fuente", ""),
                "DEPARTAMENTO": trazable.get("departamento", ""),
                "TIPO_RESOLUCION": trazable.get("tipo_resolucion", ""),
            }
        )
    return pd.DataFrame(filas, columns=columnas)


def resumen(ruta: Path | str | None = None) -> dict[str, int]:
    datos = cargar_procedencia(ruta)["registros"]
    conteo = {origen: 0 for origen in ORIGENES}
    for item in datos.values():
        origen = str(item.get("origen") or "").upper()
        if origen in conteo:
            conteo[origen] += 1
    conteo["TOTAL"] = len(datos)
    return conteo


def restaurar(datos: dict[str, Any], ruta: Path | str | None = None) -> Path:
    """Reemplaza el registro completo (usado por pruebas y recuperación)."""

    if not isinstance(datos, dict) or not isinstance(datos.get("registros"), dict):
        raise ErrorArchivo("La procedencia a restaurar no tiene el formato esperado.")
    return guardar_procedencia(datos, ruta)
