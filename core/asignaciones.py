"""Servicio de administración de usuarios y áreas.

Mantiene la base persistente ``USUARIO -> AREA_USUARIO`` usada para completar
la columna ``Area`` de los tickets. No crea ni modifica tickets: solo gestiona
la correspondencia y deja registro en la auditoría.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from config import configuracion as cfg
from core import auditoria, eventos, similitud
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
    """Se conserva por compatibilidad; ahora usa el motor de similitud."""

    return similitud.comparar(a, b)


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
        puntaje = similitud.comparar(clave, otra)
        if puntaje >= umbral:
            resultado.append(
                {
                    "usuario": nombre,
                    "area": str(fila[cfg.COLUMNA_AREA]),
                    "similitud": round(puntaje, 3),
                    "tipo": similitud.clasificar(puntaje),
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


def _registrar_en_diario(
    diario: Any,
    detalle: Sequence[Asignacion],
    *,
    ruta: Path | str | None,
    usuario_ejecutor: str,
    estado: str,
    error: str = "",
) -> None:
    """Registra cada cambio en el diario de operaciones sin bloquear el flujo."""

    from core import operaciones as svc_operaciones

    diario = diario or svc_operaciones.DiarioOperaciones()
    for asignacion in detalle:
        if asignacion.accion == "sin_cambios":
            continue
        try:
            operacion = diario.registrar(
                "ASIGNAR_AREA",
                usuario=asignacion.usuario,
                area_anterior=asignacion.area_anterior,
                area_nueva=asignacion.area,
                origen="maestro",
                archivo=str(ruta or cfg.MAESTRO_XLSX),
                detalle=(
                    f"{asignacion.usuario}: '{asignacion.area_anterior}' -> "
                    f"'{asignacion.area}' ({asignacion.accion})"
                ),
                estado=estado,
                extra=asignacion.accion,
            )
            if error and operacion is not None:
                diario.marcar(operacion.clave, estado, error=error)
        except Exception as exc:  # noqa: BLE001 - el diario no debe tumbar la asignación
            LOGGER.error("No se pudo registrar la operación en el diario: %s", exc)


def _preparar_guardado_protegido(
    ruta: Path | str | None,
    *,
    registro: Any = None,
    confirmar_cambio_externo: bool,
) -> Any:
    """Comprueba bloqueo y cambios externos antes de modificar el maestro."""

    from core import integridad_excel as integridad

    camino = Path(ruta or cfg.MAESTRO_XLSX)
    registro = registro or integridad.RegistroHuellas()
    if integridad.archivo_bloqueado(camino):
        raise integridad.ErrorIntegridad(
            f"El Excel maestro '{camino.name}' está abierto en Excel. "
            "Cierre el archivo antes de guardar."
        )
    comparacion = registro.comparar(camino)
    if comparacion.get("cambio_externo") and not confirmar_cambio_externo:
        raise integridad.ErrorCambioExterno(
            f"El Excel maestro '{camino.name}' cambió fuera de la aplicación. "
            "Revise el archivo y confirme si desea sobrescribirlo."
        )
    return registro


def asignar(
    maestro: Maestro,
    asignaciones: Sequence[tuple[str, str] | Mapping[str, Any]],
    *,
    guardar: bool = True,
    ruta_xlsx: Path | str | None = None,
    usuario_ejecutor: str = "",
    proteger: bool = True,
    confirmar_cambio_externo: bool = False,
    diario: Any = None,
    registro: Any = None,
) -> ResultadoAsignaciones:
    """Registra o actualiza una o varias correspondencias y las persiste.

    Con ``proteger=True`` (predeterminado) se detectan cambios externos, se
    evita escribir sobre un Excel abierto y cada cambio queda en el diario de
    operaciones. Los reintentos de la misma asignación no se duplican.
    """

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

    registro_huellas = None
    if guardar and proteger:
        # Se comprueba antes de modificar el maestro en memoria: si el archivo
        # cambió fuera de la aplicación, no queda una asignación fantasma.
        registro_huellas = _preparar_guardado_protegido(
            ruta_xlsx,
            registro=registro,
            confirmar_cambio_externo=confirmar_cambio_externo,
        )

    df = pd.DataFrame(normalizadas, columns=list(COLUMNAS_VISTA))
    guardado = None
    try:
        resultado = maestro.actualizar(df, manifiesto=None)
        if guardar:
            if proteger:
                from core import integridad_excel as integridad

                guardado = integridad.guardar_maestro_seguro(
                    maestro,
                    ruta_xlsx,
                    confirmar_cambio_externo=confirmar_cambio_externo,
                    registro=registro_huellas,
                    diario=diario,
                    usuario_ejecutor=usuario_ejecutor,
                    detalle=(
                        f"Persistencia de {len(detalle)} asignación(es) de áreas"
                    ),
                    tipo_operacion="ASIGNAR_AREA",
                )
            else:
                guardado = maestro.guardar(ruta_xlsx=ruta_xlsx, crear_backup=True)
    except Exception as exc:
        if guardar:
            _registrar_en_diario(
                diario,
                detalle,
                ruta=ruta_xlsx,
                usuario_ejecutor=usuario_ejecutor,
                estado="ERROR",
                error=f"{type(exc).__name__}: {exc}",
            )
        raise

    if guardar:
        _registrar_en_diario(
            diario,
            detalle,
            ruta=ruta_xlsx,
            usuario_ejecutor=usuario_ejecutor,
            estado="COMPLETADO",
        )

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

    # Solo se notifican los cambios reales. La interfaz refresca la fila
    # afectada sin reprocesar el archivo ni repetir consultas SQL.
    for asignacion in detalle:
        if asignacion.accion == "sin_cambios":
            continue
        eventos.publicar_asignacion(
            asignacion.usuario,
            asignacion.area,
            area_anterior=asignacion.area_anterior,
            origen="maestro",
            detalle=asignacion.accion,
            datos={"clave": asignacion.clave, "accion": asignacion.accion},
        )
        eventos.publicar(
            eventos.USUARIO_ACTUALIZADO,
            usuario=asignacion.usuario,
            area=asignacion.area,
            area_anterior=asignacion.area_anterior,
            origen="maestro",
            detalle=asignacion.accion,
        )
    if guardado is not None and any(a.accion != "sin_cambios" for a in detalle):
        eventos.publicar_maestro_actualizado(
            detalle="Asignación de áreas persistida",
            datos={"registros": len(detalle)},
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
    proteger: bool = True,
    confirmar_cambio_externo: bool = False,
    diario: Any = None,
    registro: Any = None,
) -> ResultadoAsignaciones:
    return asignar(
        maestro,
        [{"usuario": usuario, "area": area}],
        guardar=guardar,
        ruta_xlsx=ruta_xlsx,
        usuario_ejecutor=usuario_ejecutor,
        proteger=proteger,
        confirmar_cambio_externo=confirmar_cambio_externo,
        diario=diario,
        registro=registro,
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
