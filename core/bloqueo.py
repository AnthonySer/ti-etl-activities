"""Bloqueo cooperativo para evitar escrituras simultáneas.

El sistema puede ejecutarse desde la interfaz y desde la consola al mismo
tiempo. Para que dos procesos no escriban el maestro o el historial a la vez se
usa un archivo de bloqueo creado de forma atómica (``O_CREAT | O_EXCL``).

El bloqueo se mantiene solo durante la operación de escritura; no se deja
tomado mientras la interfaz espera una acción del operador. Si un proceso
termina de forma anómala, el bloqueo se considera obsoleto cuando el proceso
ya no existe o cuando supera el tiempo máximo configurado.
"""

from __future__ import annotations

import atexit
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from config import configuracion as cfg

LOGGER = logging.getLogger("automatizador")

# Un bloqueo de más de 30 minutos se considera abandonado.
EDAD_MAXIMA_SEGUNDOS = 30 * 60


class ErrorBloqueo(RuntimeError):
    """Otro proceso mantiene el bloqueo del sistema."""


def _ruta(valor: Path | str | None = None) -> Path:
    return Path(valor or cfg.LOCK_SISTEMA)


def _leer_pid(ruta: Path) -> int | None:
    try:
        contenido = ruta.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        return None
    for linea in contenido:
        if linea.upper().startswith("PID="):
            try:
                return int(linea.split("=", 1)[1].strip())
            except ValueError:
                return None
    return None


def _proceso_vivo(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Existe, pero pertenece a otro usuario.
        return True
    except OSError:
        return False
    return True


def _obsoleto(ruta: Path) -> bool:
    try:
        edad = time.time() - ruta.stat().st_mtime
    except OSError:
        return True
    if edad > EDAD_MAXIMA_SEGUNDOS:
        return True
    pid = _leer_pid(ruta)
    if pid is not None and not _proceso_vivo(pid):
        return True
    return False


def esta_bloqueado(ruta: Path | str | None = None) -> bool:
    """Indica si existe un bloqueo vigente."""

    archivo = _ruta(ruta)
    if not archivo.exists():
        return False
    if _obsoleto(archivo):
        return False
    return True


class BloqueoSistema:
    """Context manager que toma y libera el bloqueo de escritura."""

    def __init__(
        self,
        ruta: Path | str | None = None,
        descripcion: str = "operación",
        esperar: float = 0.0,
    ):
        self.ruta = _ruta(ruta)
        self.descripcion = descripcion
        self.esperar = max(0.0, esperar)
        self._tomado = False

    def _intentar(self) -> None:
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(
                self.ruta,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError:
            if _obsoleto(self.ruta):
                LOGGER.warning(
                    "Se elimina un bloqueo obsoleto: %s", self.ruta
                )
                try:
                    self.ruta.unlink()
                except OSError:
                    pass
                return self._intentar()
            raise ErrorBloqueo(
                "El sistema está siendo usado por otro proceso. "
                f"Espere a que termine la {self.descripcion} e intente de nuevo. "
                f"Archivo de bloqueo: {self.ruta}"
            )
        except OSError as exc:
            raise ErrorBloqueo(
                f"No se pudo crear el bloqueo '{self.ruta}': {exc}"
            ) from exc

        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as archivo:
                archivo.write(f"PID={os.getpid()}\n")
                archivo.write(f"DESCRIPCION={self.descripcion}\n")
                archivo.write(f"FECHA={time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
        except OSError:
            self.ruta.unlink(missing_ok=True)
            raise

        self._tomado = True

    def tomar(self) -> "BloqueoSistema":
        limite = time.monotonic() + self.esperar
        while True:
            try:
                self._intentar()
                return self
            except ErrorBloqueo:
                if time.monotonic() >= limite:
                    raise
                time.sleep(0.25)

    def liberar(self) -> None:
        if not self._tomado:
            return
        try:
            self.ruta.unlink(missing_ok=True)
        except OSError as exc:
            LOGGER.warning("No se pudo liberar el bloqueo '%s': %s", self.ruta, exc)
        finally:
            self._tomado = False

    def __enter__(self) -> "BloqueoSistema":
        return self.tomar()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.liberar()

    def __del__(self) -> None:  # pragma: no cover - red de seguridad
        try:
            self.liberar()
        except Exception:
            pass


@contextmanager
def bloqueo(
    descripcion: str = "operación",
    ruta: Path | str | None = None,
    esperar: float = 0.0,
) -> Iterator[BloqueoSistema]:
    """Atajo para usar el bloqueo con ``with``."""

    gestor = BloqueoSistema(ruta=ruta, descripcion=descripcion, esperar=esperar)
    with gestor:
        yield gestor


def _liberar_al_salir() -> None:  # pragma: no cover - solo proceso actual
    if not cfg.LOCK_SISTEMA.exists():
        return
    pid = _leer_pid(cfg.LOCK_SISTEMA)
    if pid == os.getpid():
        try:
            cfg.LOCK_SISTEMA.unlink(missing_ok=True)
        except OSError:
            pass


atexit.register(_liberar_al_salir)
