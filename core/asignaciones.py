"""Servicio de administración de usuarios y áreas.

Mantiene la base persistente ``USUARIO -> AREA_USUARIO`` usada para completar
la columna ``Area`` de los tickets. No crea ni modifica tickets: solo gestiona
la correspondencia y deja registro en la auditoría.
"""

from __future__ import annotations

import difflib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from config import configuracion as cfg
from core import auditoria
from core.archivos import ErrorArchivo, ErrorEstructura
from core.maestro import Maestro, ResultadoActualizacion
from core.normalizador import (
    limpiar_area,
    limpiar_usuario,
    normalizar_area,
    normalizar_usuario,
)

LOGGER = logging.getLogger("automatizador")

COLUMNAS_VISTA = ("USUARIO", "AREA_USUARIO")


@dataclass
class Asignacion:
    usuario: str
    area: str
    clave: str = ""
    accion: str = "sin_cambios"
    area_anterior: str = ""
    filas: int = 0

    def como_dict(self) -> dict[str, Any]:
        return {
            "usuario": self.usuario,
            "area": self.area,
            "clave": self.clave,
            "accion": self.accion,
            "area_anterior": self.area_anterior,
            "filas": self.filas,
        }


@dataclass
class ResultadoAsignaciones:
    asignaciones: list[Asignacion] = field(default_factory=list)
    resultado: ResultadoActualizacion | None = None
    guardado: Any = None

    @property
    def registradas(self) -> int:
        return sum(1 for item in self.asignaciones if item.accion == "nueva")

    @property
    def actualizadas(self) -> int:
        return sum(1 for item in self.asignaciones if item.accion == "actualizada")

    @property
    def sin_cambios(self) -> int:
        return sum(1 for item in self.asignaciones if item.accion == "sin_cambios")


def cargar_maestro(ruta: Path | str | None = None) -> Maestro:
    """Carga la base de correspondencias, creando una vacía si no existe."""

    if ruta is not None:
        ruta = Path(ruta)
        if not ruta.exists():
            return Maestro()
        if ruta.suffix.lower() == ".csv":
            return Maestro.cargar(ruta.with_suffix(".xlsx"), ruta)
        return Maestro.cargar(ruta, ruta.with_suffix(".csv"))

    if not Path(cfg.MAESTRO_XLSX).exists() and not Path(cfg.MAESTRO_CSV).exists():
        return Maestro()
    return Maestro.cargar(cfg.MAESTRO_XLSX, cfg.MAESTRO_CSV)


def tabla_usuarios(
    maestro: Maestro,
    filtro: str = "",
) -> pd.DataFrame:
    """Devuelve la lista de usuarios y áreas, filtrada por texto libre."""

    df = maestro.como_dataframe()
    if df.empty:
        return pd.DataFrame(columns=list(COLUMNAS_VISTA))
    if filtro.strip():
        patron = filtro.strip()
        mascara = (
            df[cfg.COLUMNA_USUARIO]
            .astype(str)
            .str.contains(patron, case=False, na=False, regex=False)
            | df[cfg.COLUMNA_AREA]
            .astype(str)
            .str.contains(patron, case=False, na=False, regex=False)
        )
        df = df[mascara]
    return df.sort_values(
        [cfg.COLUMNA_USUARIO, cfg.COLUMNA_AREA], kind="stable"
    ).reset_index(drop=True)


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def sugerencias_usuario(
    usuario: object,
    maestro: Maestro,
    *,
    limite: int = 5,
    umbral: float = 0.72,
) -> list[dict[str, Any]]:
    """Propone usuarios parecidos sin fusionar identidades automáticamente."""

    clave = normalizar_usuario(usuario)
    if clave is None:
        return []

    conocidos = maestro.como_dataframe()
    if conocidos.empty:
        return []

    resultado: list[dict[str, Any]] = []
    for _, fila in conocidos.iterrows():
        nombre = str(fila[cfg.COLUMNA_USUARIO])
        otra = normalizar_usuario(nombre)
        if otra is None or otra == clave:
            continue
        puntaje = _ratio(clave, otra)
        # Coincidencia por contención (nombres incompletos, orden distinto).
        if clave in otra or otra in clave:
            puntaje = max(puntaje, 0.85)
        if puntaje >= umbral:
            resultado.append(
                {
                    "usuario": nombre,
                    "area": str(fila[cfg.COLUMNA_AREA]),
                    "similitud": round(puntaje, 3),
                }
            )

    resultado.sort(key=lambda item: item["similitud"], reverse=True)
    return resultado[:limite]


def leer_manifiesto(ruta: Path | str) -> dict[str, Any]:
    ruta = Path(ruta)
    if not ruta.is_file():
        raise ErrorArchivo(f"No existe el manifiesto: {ruta}")
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ErrorArchivo(f"No se pudo leer el manifiesto '{ruta}': {exc}") from exc
    if not isinstance(datos, dict):
        raise ErrorArchivo(f"El manifiesto '{ruta}' no tiene formato de objeto JSON.")
    return datos


def usuarios_pendientes(
    ruta_manifiesto: Path | str | None,
    maestro: Maestro | None = None,
) -> list[dict[str, Any]]:
    """Devuelve los usuarios sin área detectados en el último procesamiento."""

    if ruta_manifiesto is None:
        return []
    datos = leer_manifiesto(ruta_manifiesto)
    pendientes = datos.get("usuarios_pendientes") or []
    if not isinstance(pendientes, list):
        return []

    resultado: list[dict[str, Any]] = []
    for item in pendientes:
        if not isinstance(item, dict):
            continue
        usuario = str(item.get("usuario") or item.get("clave") or "").strip()
        if not usuario:
            continue
        filas = item.get("filas") or []
        resultado.append(
            {
                "usuario": usuario,
                "clave": str(item.get("clave") or normalizar_usuario(usuario) or ""),
                "area_preexistente": str(item.get("area_preexistente") or ""),
                "filas": len(filas) if isinstance(filas, list) else 0,
                "sugerencias": sugerencias_usuario(usuario, maestro)
                if maestro is not None
                else [],
            }
        )
    return resultado


def _clasificar(
    maestro: Maestro,
    usuario: str,
    area: str,
) -> Asignacion:
    clave = normalizar_usuario(usuario) or ""
    anterior = maestro.buscar(usuario) or ""
    if not anterior:
        accion = "nueva"
    elif normalizar_area(anterior) == normalizar_area(area):
        accion = "sin_cambios"
    else:
        accion = "actualizada"
    return Asignacion(
        usuario=limpiar_usuario(usuario) or usuario,
        area=limpiar_area(area) or area,
        clave=clave,
        accion=accion,
        area_anterior=anterior,
    )


def asignar(
    maestro: Maestro,
    asignaciones: Sequence[tuple[str, str] | Mapping[str, Any]],
    *,
    guardar: bool = True,
    ruta_xlsx: Path | str | None = None,
    usuario_ejecutor: str = "",
) -> ResultadoAsignaciones:
    """Registra o actualiza una o varias correspondencias y las persiste."""

    normalizadas: list[dict[str, str]] = []
    detalle: list[Asignacion] = []

    for item in asignaciones:
        if isinstance(item, Mapping):
            usuario = item.get("usuario") or item.get("USUARIO") or ""
            area = item.get("area") or item.get("AREA_USUARIO") or ""
        else:
            usuario, area = item

        usuario_limpio = limpiar_usuario(usuario)
        area_limpia = limpiar_area(area)

        if normalizar_usuario(usuario_limpio) is None:
            raise ErrorEstructura(
                f"El usuario '{usuario}' no es válido: no puede estar vacío."
            )
        if area_limpia is None:
            raise ErrorEstructura(
                f"El área para '{usuario_limpio}' no es válida: no puede estar vacía."
            )

        normalizadas.append(
            {
                cfg.COLUMNA_USUARIO: usuario_limpio or "",
                cfg.COLUMNA_AREA: area_limpia,
            }
        )
        detalle.append(_clasificar(maestro, usuario_limpio or "", area_limpia))

    if not normalizadas:
        return ResultadoAsignaciones()

    df = pd.DataFrame(normalizadas, columns=list(COLUMNAS_VISTA))
    resultado = maestro.actualizar(df, manifiesto=None)

    guardado = None
    if guardar:
        guardado = maestro.guardar(ruta_xlsx=ruta_xlsx, crear_backup=True)

    for asignacion in detalle:
        try:
            auditoria.registrar_evento(
                "ASIGNACION_AREA",
                archivo_salida=str((ruta_xlsx or cfg.MAESTRO_XLSX)),
                usuario_ejecutor=usuario_ejecutor,
                mensajes=(
                    f"{asignacion.usuario}: "
                    f"'{asignacion.area_anterior}' -> '{asignacion.area}' "
                    f"({asignacion.accion})"
                ),
            )
        except Exception as exc:  # noqa: BLE001 - la auditoría no debe tumbar la operación
            LOGGER.error("No se pudo registrar la asignación en auditoría: %s", exc)

    LOGGER.info(
        "Asignaciones: %s nuevas, %s actualizadas, %s sin cambios",
        sum(1 for a in detalle if a.accion == "nueva"),
        sum(1 for a in detalle if a.accion == "actualizada"),
        sum(1 for a in detalle if a.accion == "sin_cambios"),
    )
    return ResultadoAsignaciones(
        asignaciones=detalle,
        resultado=resultado,
        guardado=guardado,
    )


def asignar_una(
    maestro: Maestro,
    usuario: str,
    area: str,
    *,
    guardar: bool = True,
    ruta_xlsx: Path | str | None = None,
    usuario_ejecutor: str = "",
) -> ResultadoAsignaciones:
    return asignar(
        maestro,
        [{"usuario": usuario, "area": area}],
        guardar=guardar,
        ruta_xlsx=ruta_xlsx,
        usuario_ejecutor=usuario_ejecutor,
    )


def area_de(maestro: Maestro, usuario: object) -> str:
    """Devuelve el área registrada o cadena vacía."""

    return maestro.buscar(usuario) or ""


def usuarios_sin_area(maestro: Maestro) -> list[str]:
    """La base nunca guarda áreas vacías; se expone para completar la vista."""

    df = maestro.como_dataframe()
    if df.empty:
        return []
    return [
        str(usuario)
        for usuario, area in zip(df[cfg.COLUMNA_USUARIO], df[cfg.COLUMNA_AREA])
        if limpiar_area(area) is None
    ]
