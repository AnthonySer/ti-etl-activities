"""Protección del Excel maestro frente a cambios externos.

El objetivo es no sobrescribir un archivo que otra persona modificó mientras la
aplicación lo tenía cargado. Se calcula una huella estable (tamaño, fecha y
SHA-256) y se compara con la última escritura confirmada.

También se comprueba que el archivo no esté abierto en Excel y que la escritura
termine con una verificación real. Cada operación queda en el diario de
``core.operaciones`` con estado PENDIENTE/COMPLETADO/ERROR.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from config import configuracion as cfg
from core.archivos import ErrorArchivo, escribir_json_atomico

LOGGER = logging.getLogger("automatizador")


class ErrorIntegridad(ErrorArchivo):
    """El archivo no puede escribirse con seguridad."""


class ErrorCambioExterno(ErrorIntegridad):
    """El Excel cambió fuera de la aplicación y requiere confirmación."""


def _ahora() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def ruta_huellas() -> Path:
    ruta = getattr(cfg, "HUELLAS_EXCEL_JSON", None)
    if ruta:
        return Path(ruta)
    return Path(getattr(cfg, "DATA_DIR", Path("data"))) / "huellas_excel.json"


def huella_archivo(ruta: Path | str, *, calcular_hash: bool = True) -> dict[str, Any]:
    """Huella del archivo: existencia, tamaño, mtime y SHA-256."""

    camino = Path(ruta)
    if not camino.is_file():
        return {
            "existe": False,
            "ruta": str(camino),
            "tamano": 0,
            "mtime_ns": 0,
            "sha256": "",
        }
    stats = camino.stat()
    hash_archivo = ""
    if calcular_hash:
        digest = hashlib.sha256()
        with camino.open("rb") as archivo:
            for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
                digest.update(bloque)
        hash_archivo = digest.hexdigest()
    return {
        "existe": True,
        "ruta": str(camino),
        "tamano": int(stats.st_size),
        "mtime_ns": int(stats.st_mtime_ns),
        "sha256": hash_archivo,
    }


def archivo_bloqueado(ruta: Path | str) -> bool:
    """Intenta abrir el archivo para escritura sin modificarlo.

    En Windows un libro abierto en Excel suele impedir ``r+b``. Si el archivo
    no existe se considera no bloqueado.
    """

    camino = Path(ruta)
    if not camino.exists():
        return False
    try:
        with camino.open("r+b"):
            return False
    except PermissionError:
        return True
    except OSError as exc:
        if getattr(exc, "winerror", None) in (32, 33):
            return True
        return False


def _normalizar_ruta(ruta: Path | str) -> str:
    return str(Path(ruta).resolve()).lower()


class RegistroHuellas:
    """Persistencia de la última huella confirmada de cada Excel."""

    def __init__(self, ruta: Path | str | None = None) -> None:
        self.ruta = Path(ruta) if ruta is not None else ruta_huellas()
        self._datos: dict[str, Any] | None = None

    def _cargar(self) -> dict[str, Any]:
        if self._datos is not None:
            return self._datos
        if not self.ruta.is_file():
            self._datos = {}
            return self._datos
        try:
            datos = json.loads(self.ruta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.error("No se pudieron leer las huellas de Excel: %s", exc)
            self._datos = {}
            return self._datos
        self._datos = datos if isinstance(datos, dict) else {}
        return self._datos

    def _guardar(self) -> None:
        escribir_json_atomico(self._cargar(), self.ruta)

    def obtener(self, ruta: Path | str) -> dict[str, Any] | None:
        registro = self._cargar().get(_normalizar_ruta(ruta))
        return dict(registro) if isinstance(registro, dict) else None

    def guardar(
        self,
        ruta: Path | str,
        huella: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        camino = Path(ruta)
        actual = huella or huella_archivo(camino)
        actual = {**actual, "confirmado": _ahora()}
        self._cargar()[_normalizar_ruta(camino)] = actual
        self._guardar()
        return actual

    def comparar(self, ruta: Path | str) -> dict[str, Any]:
        """Compara el archivo actual con la última huella confirmada."""

        camino = Path(ruta)
        actual = huella_archivo(camino)
        anterior = self.obtener(camino)
        resultado = {
            "ruta": str(camino),
            "existe": actual["existe"],
            "primera_vez": anterior is None,
            "cambio_externo": False,
            "huella_actual": actual,
            "huella_guardada": anterior,
            "bloqueado": archivo_bloqueado(camino),
        }
        if anterior is None or not actual["existe"]:
            return resultado

        mismo_hash = bool(
            anterior.get("sha256")
            and actual.get("sha256")
            and anterior.get("sha256") == actual.get("sha256")
        )
        mismo_tamano = int(anterior.get("tamano") or -1) == int(actual.get("tamano") or 0)
        mismo_mtime = int(anterior.get("mtime_ns") or -1) == int(
            actual.get("mtime_ns") or 0
        )
        resultado["cambio_externo"] = not (
            mismo_hash or (mismo_tamano and mismo_mtime)
        )
        return resultado


def detectar_cambio_externo(
    ruta: Path | str,
    *,
    registro: RegistroHuellas | None = None,
) -> dict[str, Any]:
    """Atajo para comprobar si un Excel cambió desde la última escritura."""

    return (registro or RegistroHuellas()).comparar(ruta)


def verificar_excel_legible(ruta: Path | str) -> Path:
    """Verifica que el archivo exista y pueda abrirse como Excel."""

    camino = Path(ruta)
    if not camino.is_file():
        raise ErrorIntegridad(f"No existe el Excel maestro: {camino}")
    try:
        import pandas as pd

        pd.read_excel(camino, nrows=1, engine="openpyxl")
    except Exception as exc:  # noqa: BLE001 - se informa como error controlado
        raise ErrorIntegridad(
            f"El Excel maestro '{camino.name}' no se puede abrir: {exc}"
        ) from exc
    return camino


def _huella_datos(maestro: Any) -> str:
    """Huella del contenido en memoria para no confundir dos guardados."""

    try:
        df = maestro.como_dataframe()
    except Exception:  # noqa: BLE001 - el maestro puede no exponer el dataframe
        return ""
    try:
        import pandas as pd

        valores = pd.util.hash_pandas_object(df, index=True).values.tobytes()
        return hashlib.sha256(valores).hexdigest()[:24]
    except Exception:  # noqa: BLE001
        return ""


def guardar_maestro_seguro(
    maestro: Any,
    ruta_xlsx: Path | str | None = None,
    *,
    confirmar_cambio_externo: bool = False,
    registro: RegistroHuellas | None = None,
    diario: Any = None,
    usuario_ejecutor: str = "",
    detalle: str = "",
    tipo_operacion: str = "GUARDAR_MAESTRO",
) -> Any:
    """Guarda el maestro con detección de cambios externos y diario.

    ``maestro`` debe exponer ``guardar(ruta_xlsx=..., crear_backup=True)`` y
    ``validar()``, como ``core.maestro.Maestro``.
    """

    from core import operaciones as svc_operaciones

    camino = Path(ruta_xlsx or getattr(cfg, "MAESTRO_XLSX"))
    diario = diario or svc_operaciones.DiarioOperaciones()
    registro = registro or RegistroHuellas()

    if archivo_bloqueado(camino):
        raise ErrorIntegridad(
            f"El Excel maestro '{camino.name}' está abierto en Excel. "
            "Cierre el archivo antes de guardar."
        )

    comparacion = registro.comparar(camino)
    if comparacion.get("cambio_externo") and not confirmar_cambio_externo:
        raise ErrorCambioExterno(
            f"El Excel maestro '{camino.name}' cambió fuera de la aplicación. "
            "Revise el archivo y confirme si desea sobrescribirlo."
        )

    def _guardar() -> Any:
        resultado = maestro.guardar(ruta_xlsx=camino, crear_backup=True)
        verificar_excel_legible(camino)
        registro.guardar(camino)
        return resultado

    extra = _huella_datos(maestro)
    if comparacion.get("cambio_externo") and confirmar_cambio_externo:
        # Si se confirmó sobrescribir un cambio externo, el guardado debe
        # ejecutarse aunque el contenido en memoria coincida con uno anterior.
        sha_actual = (comparacion.get("huella_actual") or {}).get("sha256", "")
        extra = f"{extra}|externo|{sha_actual}"

    operacion, resultado, ejecutada = diario.ejecutar(
        tipo_operacion,
        _guardar,
        usuario="",
        archivo=str(camino),
        detalle=detalle or "Guardado del maestro de áreas",
        origen="EXCEL",
        area_nueva="",
        extra=extra,
    )
    if usuario_ejecutor:
        operacion.detalle = (
            f"{operacion.detalle} | ejecutor: {usuario_ejecutor}".strip()
        )
    return resultado


def escribir_protegido(
    ruta: Path | str,
    funcion_escritura: Any,
    *,
    confirmar_cambio_externo: bool = False,
    registro: RegistroHuellas | None = None,
    diario: Any = None,
    tipo_operacion: str = "ESCRITURA_EXCEL",
    extra: Any = "",
) -> Any:
    """Ejecuta una escritura arbitraria con las mismas protecciones."""

    from core import operaciones as svc_operaciones

    camino = Path(ruta)
    registro = registro or RegistroHuellas()
    diario = diario or svc_operaciones.DiarioOperaciones()

    if archivo_bloqueado(camino):
        raise ErrorIntegridad(
            f"El archivo '{camino.name}' está abierto. Ciérrelo antes de guardar."
        )
    comparacion = registro.comparar(camino)
    if comparacion.get("cambio_externo") and not confirmar_cambio_externo:
        raise ErrorCambioExterno(
            f"El archivo '{camino.name}' cambió fuera de la aplicación."
        )

    def _escribir() -> Any:
        resultado = funcion_escritura()
        if camino.exists():
            registro.guardar(camino)
        return resultado

    _, resultado, _ = diario.ejecutar(
        tipo_operacion,
        _escribir,
        archivo=str(camino),
        origen="EXCEL",
        extra=extra,
    )
    return resultado
