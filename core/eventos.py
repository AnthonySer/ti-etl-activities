"""Bus de eventos interno para actualizaciones inmediatas de la interfaz.

La interfaz no necesita volver a procesar todos los archivos cuando cambia un
solo usuario: publica un evento con el componente afectado y cada pantalla
suscrita refresca únicamente su región.

El bus es síncrono, seguro entre hilos y tolerante a fallos: un suscriptor con
errores nunca interrumpe la operación que originó el evento.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable

LOGGER = logging.getLogger("automatizador")

# ---------------------------------------------------------------------------
# Temas estables (no cambiar sin actualizar la interfaz)
# ---------------------------------------------------------------------------
ASIGNACION_REALIZADA = "asignacion_realizada"
AREA_CREADA = "area_creada"
USUARIO_ACTUALIZADO = "usuario_actualizado"
MAESTRO_ACTUALIZADO = "maestro_actualizado"
PROCESAMIENTO_FINALIZADO = "procesamiento_finalizado"
CONSULTA_SQL_FINALIZADA = "consulta_sql_finalizada"
CATALOGO_AREAS_ACTUALIZADO = "catalogo_areas_actualizado"
SINCRONIZACION_INCOMPLETA = "sincronizacion_incompleta"

TEMAS = (
    ASIGNACION_REALIZADA,
    AREA_CREADA,
    USUARIO_ACTUALIZADO,
    MAESTRO_ACTUALIZADO,
    PROCESAMIENTO_FINALIZADO,
    CONSULTA_SQL_FINALIZADA,
    CATALOGO_AREAS_ACTUALIZADO,
    SINCRONIZACION_INCOMPLETA,
)

Suscriptor = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class Evento:
    """Datos mínimos de un cambio confirmado."""

    tema: str
    usuario: str = ""
    area: str = ""
    area_anterior: str = ""
    origen: str = ""
    detalle: str = ""
    datos: dict[str, Any] = field(default_factory=dict)
    fecha: str = field(
        default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds")
    )

    def como_dict(self) -> dict[str, Any]:
        return {
            "tema": self.tema,
            "usuario": self.usuario,
            "area": self.area,
            "area_anterior": self.area_anterior,
            "origen": self.origen,
            "detalle": self.detalle,
            "datos": dict(self.datos),
            "fecha": self.fecha,
        }


class BusEventos:
    """Registro de suscriptores por tema."""

    def __init__(self) -> None:
        self._suscriptores: dict[str, list[Suscriptor]] = {}
        self._candado = threading.RLock()

    def suscribir(self, tema: str, suscriptor: Suscriptor) -> Callable[[], None]:
        """Registra un suscriptor y devuelve la función para desuscribirlo."""

        if tema not in TEMAS:
            raise ValueError(f"Tema de evento desconocido: {tema}")
        with self._candado:
            self._suscriptores.setdefault(tema, []).append(suscriptor)

        def cancelar() -> None:
            self.desuscribir(tema, suscriptor)

        return cancelar

    def desuscribir(self, tema: str, suscriptor: Suscriptor) -> None:
        with self._candado:
            lista = self._suscriptores.get(tema) or []
            if suscriptor in lista:
                lista.remove(suscriptor)

    def limpiar(self, tema: str | None = None) -> None:
        with self._candado:
            if tema is None:
                self._suscriptores.clear()
            else:
                self._suscriptores.pop(tema, None)

    def suscriptores(self, tema: str) -> tuple[Suscriptor, ...]:
        with self._candado:
            return tuple(self._suscriptores.get(tema) or ())

    def publicar(
        self,
        tema: str,
        *,
        evento: Evento | dict[str, Any] | None = None,
        **datos: Any,
    ) -> Evento:
        """Publica un evento y notifica a los suscriptores del tema.

        Los errores de los suscriptores se registran en el log y se aíslan.
        """

        if tema not in TEMAS:
            raise ValueError(f"Tema de evento desconocido: {tema}")

        if isinstance(evento, Evento):
            actual = evento
        elif isinstance(evento, dict):
            actual = Evento(tema=tema, **evento)
        else:
            actual = Evento(tema=tema, **datos)

        for suscriptor in self.suscriptores(tema):
            try:
                suscriptor(actual.como_dict())
            except Exception as exc:  # noqa: BLE001 - un refresco no rompe la operación
                LOGGER.error(
                    "El suscriptor '%s' falló al procesar el evento '%s': %s",
                    getattr(suscriptor, "__name__", repr(suscriptor)),
                    tema,
                    exc,
                )
        return actual


BUS = BusEventos()


def suscribir(tema: str, suscriptor: Suscriptor) -> Callable[[], None]:
    return BUS.suscribir(tema, suscriptor)


def publicar(
    tema: str,
    *,
    evento: Evento | dict[str, Any] | None = None,
    **datos: Any,
) -> Evento:
    return BUS.publicar(tema, evento=evento, **datos)


def publicar_asignacion(
    usuario: str,
    area: str,
    *,
    area_anterior: str = "",
    origen: str = "",
    detalle: str = "",
    datos: dict[str, Any] | None = None,
) -> Evento:
    """Atajo para las asignaciones confirmadas."""

    return publicar(
        ASIGNACION_REALIZADA,
        usuario=usuario,
        area=area,
        area_anterior=area_anterior,
        origen=origen,
        detalle=detalle,
        datos=datos or {},
    )


def publicar_area_creada(
    area: str,
    *,
    usuario: str = "",
    origen: str = "",
    detalle: str = "",
) -> Evento:
    return publicar(
        AREA_CREADA,
        usuario=usuario,
        area=area,
        origen=origen,
        detalle=detalle,
    )


def publicar_maestro_actualizado(
    *,
    detalle: str = "",
    datos: dict[str, Any] | None = None,
) -> Evento:
    return publicar(
        MAESTRO_ACTUALIZADO,
        detalle=detalle,
        datos=datos or {},
    )


def publicar_procesamiento(
    *,
    detalle: str = "",
    datos: dict[str, Any] | None = None,
) -> Evento:
    return publicar(
        PROCESAMIENTO_FINALIZADO,
        detalle=detalle,
        datos=datos or {},
    )


def notificar_varios(temas: Iterable[str], **datos: Any) -> None:
    """Publica el mismo evento en varios temas (útil para cambios compuestos)."""

    for tema in temas:
        publicar(tema, **datos)
