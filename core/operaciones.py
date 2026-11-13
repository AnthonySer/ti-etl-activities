"""Registro de operaciones y reintentos idempotentes.

Excel y SQL no comparten una única transacción. Cada operación que involucra
ambos destinos se registra aquí con uno de estos estados:

* ``PENDIENTE``: iniciada y aún no confirmada.
* ``COMPLETADO``: ambos destinos quedaron consistentes.
* ``ERROR``: falló y puede reintentarse.
* ``REQUIERE_REVISION``: el resultado es ambiguo; no debe reintentarse solo.

La clave de idempotencia evita duplicados: repetir la misma operación devuelve
el registro existente en lugar de crear otro.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from config import configuracion as cfg
from core.archivos import ErrorArchivo, escribir_json_atomico
from core.normalizador import normalizar_area, normalizar_usuario

LOGGER = logging.getLogger("automatizador")

PENDIENTE = "PENDIENTE"
COMPLETADO = "COMPLETADO"
ERROR = "ERROR"
REQUIERE_REVISION = "REQUIERE_REVISION"

ESTADOS = (PENDIENTE, COMPLETADO, ERROR, REQUIERE_REVISION)
ESTADOS_REINTENTABLES = (PENDIENTE, ERROR)

_CANDADO = threading.RLock()


def ruta_operaciones() -> Path:
    """Ruta configurable del diario de operaciones."""

    ruta = getattr(cfg, "OPERACIONES_JSON", None)
    if ruta:
        return Path(ruta)
    return Path(getattr(cfg, "DATA_DIR", Path("data"))) / "operaciones.json"


def _ahora() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def clave_idempotencia(
    tipo: Any,
    usuario: Any = "",
    area_nueva: Any = "",
    *,
    archivo: Any = "",
    extra: Any = "",
) -> str:
    """Huella estable de una operación repetible."""

    partes = [
        str(tipo or "").strip().upper(),
        normalizar_usuario(usuario) or "",
        normalizar_area(area_nueva) or "",
        str(archivo or "").strip().lower(),
        str(extra or "").strip().upper(),
    ]
    texto = "\x1f".join(partes)
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:32]


@dataclass
class Operacion:
    clave: str
    tipo: str
    estado: str = PENDIENTE
    usuario: str = ""
    identificador: str = ""
    empresa: str = ""
    area_anterior: str = ""
    area_nueva: str = ""
    origen: str = ""
    archivo: str = ""
    detalle: str = ""
    errores: list[str] = field(default_factory=list)
    intentos: int = 1
    creada: str = field(default_factory=_ahora)
    actualizada: str = field(default_factory=_ahora)

    @property
    def id(self) -> str:
        return self.clave

    @property
    def reintentable(self) -> bool:
        return self.estado in ESTADOS_REINTENTABLES

    def como_dict(self) -> dict[str, Any]:
        return {
            "clave": self.clave,
            "tipo": self.tipo,
            "estado": self.estado,
            "usuario": self.usuario,
            "identificador": self.identificador,
            "empresa": self.empresa,
            "area_anterior": self.area_anterior,
            "area_nueva": self.area_nueva,
            "origen": self.origen,
            "archivo": self.archivo,
            "detalle": self.detalle,
            "errores": list(self.errores),
            "intentos": self.intentos,
            "creada": self.creada,
            "actualizada": self.actualizada,
        }

    @classmethod
    def desde_dict(cls, datos: Mapping[str, Any]) -> "Operacion":
        return cls(
            clave=str(datos.get("clave") or ""),
            tipo=str(datos.get("tipo") or ""),
            estado=str(datos.get("estado") or PENDIENTE),
            usuario=str(datos.get("usuario") or ""),
            identificador=str(datos.get("identificador") or ""),
            empresa=str(datos.get("empresa") or ""),
            area_anterior=str(datos.get("area_anterior") or ""),
            area_nueva=str(datos.get("area_nueva") or ""),
            origen=str(datos.get("origen") or ""),
            archivo=str(datos.get("archivo") or ""),
            detalle=str(datos.get("detalle") or ""),
            errores=[str(item) for item in datos.get("errores") or []],
            intentos=int(datos.get("intentos") or 1),
            creada=str(datos.get("creada") or _ahora()),
            actualizada=str(datos.get("actualizada") or _ahora()),
        )


class DiarioOperaciones:
    """Persistencia JSON del historial de operaciones."""

    def __init__(self, ruta: Path | str | None = None) -> None:
        self.ruta = Path(ruta) if ruta is not None else ruta_operaciones()
        self._operaciones: dict[str, Operacion] | None = None

    # ------------------------------------------------------------------
    def _cargar(self) -> dict[str, Operacion]:
        if self._operaciones is not None:
            return self._operaciones
        if not self.ruta.is_file():
            self._operaciones = {}
            return self._operaciones
        try:
            datos = json.loads(self.ruta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.error(
                "No se pudo leer el diario de operaciones '%s': %s", self.ruta, exc
            )
            self._operaciones = {}
            return self._operaciones
        lista = datos.get("operaciones") if isinstance(datos, dict) else datos
        operaciones: dict[str, Operacion] = {}
        if isinstance(lista, list):
            for item in lista:
                if not isinstance(item, Mapping):
                    continue
                operacion = Operacion.desde_dict(item)
                if operacion.clave:
                    operaciones[operacion.clave] = operacion
        self._operaciones = operaciones
        return operaciones

    def _guardar(self) -> None:
        with _CANDADO:
            operaciones = self._cargar()
            escribir_json_atomico(
                {
                    "actualizado": _ahora(),
                    "operaciones": [
                        item.como_dict() for item in operaciones.values()
                    ],
                },
                self.ruta,
            )

    def recargar(self) -> None:
        with _CANDADO:
            self._operaciones = None

    # ------------------------------------------------------------------
    def registrar(
        self,
        tipo: Any,
        *,
        usuario: Any = "",
        identificador: Any = "",
        empresa: Any = "",
        area_anterior: Any = "",
        area_nueva: Any = "",
        origen: Any = "",
        archivo: Any = "",
        detalle: Any = "",
        estado: str = PENDIENTE,
        clave: str | None = None,
        extra: Any = "",
        forzar_nuevo: bool = False,
    ) -> Operacion:
        """Crea o reutiliza una operación por su clave de idempotencia."""

        if estado not in ESTADOS:
            raise ValueError(f"Estado de operación desconocido: {estado}")
        huella = clave or clave_idempotencia(
            tipo,
            usuario,
            area_nueva,
            archivo=archivo,
            extra=extra,
        )
        with _CANDADO:
            operaciones = self._cargar()
            existente = operaciones.get(huella)
            if existente is not None and not forzar_nuevo:
                # Reintento idempotente: no se duplica el registro.
                existente.intentos += 0 if existente.estado == COMPLETADO else 1
                existente.actualizada = _ahora()
                if existente.estado == COMPLETADO:
                    existente.detalle = existente.detalle or str(detalle or "")
                else:
                    existente.estado = estado
                    existente.detalle = str(detalle or existente.detalle)
                self._guardar()
                return existente

            operacion = Operacion(
                clave=huella,
                tipo=str(tipo or "").strip().upper(),
                estado=estado,
                usuario=str(usuario or "").strip(),
                identificador=str(identificador or "").strip(),
                empresa=str(empresa or "").strip(),
                area_anterior=str(area_anterior or "").strip(),
                area_nueva=str(area_nueva or "").strip(),
                origen=str(origen or "").strip(),
                archivo=str(archivo or "").strip(),
                detalle=str(detalle or "").strip(),
            )
            operaciones[huella] = operacion
            self._guardar()
            return operacion

    def marcar(
        self,
        clave: Any,
        estado: str,
        *,
        error: Any = "",
        detalle: Any = "",
        area_nueva: Any = "",
        identificador: Any = "",
    ) -> Operacion | None:
        if estado not in ESTADOS:
            raise ValueError(f"Estado de operación desconocido: {estado}")
        with _CANDADO:
            operaciones = self._cargar()
            operacion = operaciones.get(str(clave))
            if operacion is None:
                return None
            operacion.estado = estado
            operacion.actualizada = _ahora()
            if detalle:
                operacion.detalle = str(detalle)
            if area_nueva:
                operacion.area_nueva = str(area_nueva).strip()
            if identificador:
                operacion.identificador = str(identificador).strip()
            if error:
                mensaje = str(error)
                if mensaje not in operacion.errores:
                    operacion.errores.append(mensaje)
            self._guardar()
            return operacion

    # ------------------------------------------------------------------
    def obtener(self, clave: Any) -> Operacion | None:
        with _CANDADO:
            return self._cargar().get(str(clave))

    def listar(
        self,
        *,
        estados: Iterable[str] | None = None,
        limite: int = 50,
    ) -> list[Operacion]:
        with _CANDADO:
            operaciones = list(self._cargar().values())
        if estados is not None:
            permitidos = {str(estado) for estado in estados}
            operaciones = [item for item in operaciones if item.estado in permitidos]
        operaciones.sort(key=lambda item: item.actualizada, reverse=True)
        return operaciones[: max(0, int(limite))]

    def pendientes(self, limite: int = 50) -> list[Operacion]:
        return self.listar(estados=ESTADOS_REINTENTABLES, limite=limite)

    def historial(self, limite: int = 100) -> list[dict[str, Any]]:
        return [item.como_dict() for item in self.listar(limite=limite)]

    def resumen(self) -> dict[str, int]:
        with _CANDADO:
            operaciones = list(self._cargar().values())
        resumen = {estado: 0 for estado in ESTADOS}
        for item in operaciones:
            resumen[item.estado] = resumen.get(item.estado, 0) + 1
        resumen["TOTAL"] = len(operaciones)
        return resumen

    # ------------------------------------------------------------------
    def ejecutar(
        self,
        tipo: Any,
        funcion: Callable[[], Any],
        *,
        usuario: Any = "",
        identificador: Any = "",
        empresa: Any = "",
        area_anterior: Any = "",
        area_nueva: Any = "",
        origen: Any = "",
        archivo: Any = "",
        detalle: Any = "",
        clave: str | None = None,
        extra: Any = "",
        estado_error: str = ERROR,
    ) -> tuple[Operacion, Any, bool]:
        """Ejecuta ``funcion`` una sola vez por clave.

        Devuelve ``(operacion, resultado, ejecutada)``. Si la operación ya está
        ``COMPLETADO`` no se vuelve a ejecutar (reintento idempotente).
        """

        operacion = self.registrar(
            tipo,
            usuario=usuario,
            identificador=identificador,
            empresa=empresa,
            area_anterior=area_anterior,
            area_nueva=area_nueva,
            origen=origen,
            archivo=archivo,
            detalle=detalle,
            clave=clave,
            extra=extra,
        )
        if operacion.estado == COMPLETADO:
            return operacion, None, False

        try:
            resultado = funcion()
        except Exception as exc:
            self.marcar(
                operacion.clave,
                estado_error,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

        self.marcar(
            operacion.clave,
            COMPLETADO,
            detalle=detalle or "Operación completada",
        )
        return self.obtener(operacion.clave) or operacion, resultado, True

    def reintentar(
        self,
        clave: Any,
        funcion: Callable[[], Any],
        *,
        estado_error: str = ERROR,
    ) -> tuple[Operacion | None, Any, bool]:
        """Reintenta una operación pendiente o fallida de forma segura."""

        operacion = self.obtener(clave)
        if operacion is None:
            return None, None, False
        if operacion.estado == COMPLETADO:
            return operacion, None, False
        if operacion.estado == REQUIERE_REVISION:
            return operacion, None, False
        operacion.intentos += 1
        operacion.actualizada = _ahora()
        self._guardar()
        try:
            resultado = funcion()
        except Exception as exc:
            self.marcar(
                operacion.clave,
                estado_error,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        self.marcar(operacion.clave, COMPLETADO, detalle="Reintento completado")
        return self.obtener(operacion.clave), resultado, True


DIARIO = DiarioOperaciones()


def registrar_operacion(**datos: Any) -> Operacion:
    return DIARIO.registrar(**datos)


def ejecutar_operacion(
    tipo: Any,
    funcion: Callable[[], Any],
    **datos: Any,
) -> tuple[Operacion, Any, bool]:
    return DIARIO.ejecutar(tipo, funcion, **datos)
