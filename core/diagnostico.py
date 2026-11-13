"""Diagnóstico y recuperación de operaciones.

Reúne en un solo lugar lo necesario para supervisar el sistema sin abrir la
interfaz: historial de operaciones, operaciones pendientes, última
sincronización y un resumen legible. No expone credenciales ni contraseñas.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from config import configuracion as cfg
from core import auditoria, operaciones

LOGGER = logging.getLogger("automatizador")


@dataclass
class DiagnosticoSistema:
    fecha: str = ""
    operaciones: dict[str, int] = field(default_factory=dict)
    pendientes: list[dict[str, Any]] = field(default_factory=list)
    ultimas: list[dict[str, Any]] = field(default_factory=list)
    auditoria: dict[str, Any] = field(default_factory=dict)
    maestro: dict[str, Any] = field(default_factory=dict)
    cache: dict[str, Any] = field(default_factory=dict)
    alertas: list[str] = field(default_factory=list)

    def como_dict(self) -> dict[str, Any]:
        return {
            "fecha": self.fecha,
            "operaciones": dict(self.operaciones),
            "pendientes": list(self.pendientes),
            "ultimas": list(self.ultimas),
            "auditoria": dict(self.auditoria),
            "maestro": dict(self.maestro),
            "cache": dict(self.cache),
            "alertas": list(self.alertas),
        }

    def como_markdown(self) -> str:
        lineas = [
            "# Diagnóstico del sistema",
            "",
            f"Generado: {self.fecha}",
            "",
            "## Operaciones",
            "",
        ]
        for estado, cantidad in self.operaciones.items():
            lineas.append(f"- {estado}: {cantidad}")
        lineas.extend(["", "## Alertas", ""])
        if self.alertas:
            lineas.extend(f"- {alerta}" for alerta in self.alertas)
        else:
            lineas.append("- Sin alertas.")
        lineas.extend(["", "## Últimas operaciones", ""])
        if self.ultimas:
            for item in self.ultimas:
                lineas.append(
                    f"- {item.get('actualizada', '')} | {item.get('tipo', '')} | "
                    f"{item.get('usuario', '')} | {item.get('area_nueva', '')} | "
                    f"{item.get('estado', '')}"
                )
        else:
            lineas.append("- Sin operaciones registradas.")
        return "\n".join(lineas) + "\n"


def _ultima_auditoria() -> dict[str, Any]:
    try:
        df = auditoria.cargar_auditoria()
    except Exception as exc:  # noqa: BLE001 - el diagnóstico no debe fallar
        LOGGER.warning("No se pudo leer la auditoría: %s", exc)
        return {}
    if df is None or getattr(df, "empty", True):
        return {}
    ultima = df.iloc[-1].to_dict()
    return {
        "tipo": str(ultima.get("TIPO") or ""),
        "fecha": str(ultima.get("FECHA_HORA") or ""),
        "estado": str(ultima.get("ESTADO") or ""),
        "pendientes": ultima.get("PENDIENTES", ""),
        "errores": ultima.get("ERRORES", ""),
    }


def _resumen_maestro() -> dict[str, Any]:
    try:
        from core.asignaciones import cargar_maestro

        maestro = cargar_maestro()
        return {"usuarios": len(maestro), "ruta": str(cfg.MAESTRO_XLSX)}
    except Exception as exc:  # noqa: BLE001
        return {"usuarios": 0, "error": str(exc)}


def _resumen_cache() -> dict[str, Any]:
    try:
        from database.synchronizer import resumen_sincronizacion

        datos = resumen_sincronizacion()
        return {
            "actualizado": str(datos.get("actualizado") or ""),
            "empleados": int(datos.get("empleados") or 0),
            "empresas": len(datos.get("empresas") or []),
        }
    except Exception as exc:  # noqa: BLE001
        return {"empleados": 0, "error": str(exc)}


def diagnosticar(
    *,
    diario: Any = None,
    limite: int = 20,
) -> DiagnosticoSistema:
    """Construye un diagnóstico legible y sin datos sensibles."""

    diario = diario or operaciones.DiarioOperaciones()
    resumen = diario.resumen()
    pendientes = [item.como_dict() for item in diario.pendientes(limite=limite)]
    ultimas = diario.historial(limite=limite)
    alertas: list[str] = []
    if resumen.get(operaciones.ERROR):
        alertas.append(
            f"{resumen[operaciones.ERROR]} operación(es) con error pendientes de reintento."
        )
    if resumen.get(operaciones.REQUIERE_REVISION):
        alertas.append(
            f"{resumen[operaciones.REQUIERE_REVISION]} operación(es) requieren revisión manual."
        )
    if resumen.get(operaciones.PENDIENTE):
        alertas.append(
            f"{resumen[operaciones.PENDIENTE]} operación(es) quedaron pendientes."
        )

    return DiagnosticoSistema(
        fecha=datetime.now().astimezone().isoformat(timespec="seconds"),
        operaciones=resumen,
        pendientes=pendientes,
        ultimas=ultimas,
        auditoria=_ultima_auditoria(),
        maestro=_resumen_maestro(),
        cache=_resumen_cache(),
        alertas=alertas,
    )


def guardar_diagnostico(
    ruta: Path | str | None = None,
    *,
    diario: Any = None,
) -> Path:
    """Escribe el diagnóstico en Markdown (por defecto en ``reportes``)."""

    destino = Path(ruta) if ruta is not None else Path(cfg.REPORTES_DIR) / "diagnostico_sistema.md"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(diagnosticar(diario=diario).como_markdown(), encoding="utf-8")
    return destino


def reintentar(
    clave: str,
    funcion: Callable[[], Any],
    *,
    diario: Any = None,
) -> tuple[Any, Any, bool]:
    """Reintenta una operación pendiente o fallida."""

    diario = diario or operaciones.DiarioOperaciones()
    return diario.reintentar(clave, funcion)


def historial_reciente(limite: int = 50, *, diario: Any = None) -> list[dict[str, Any]]:
    diario = diario or operaciones.DiarioOperaciones()
    return diario.historial(limite=limite)


def operaciones_por_usuario(
    usuario: Any,
    *,
    limite: int = 50,
    diario: Any = None,
) -> list[dict[str, Any]]:
    """Filtra el historial por usuario normalizado (sin consultar SQL)."""

    from core.normalizador import normalizar_usuario

    clave = normalizar_usuario(usuario)
    if not clave:
        return []
    diario = diario or operaciones.DiarioOperaciones()
    return [
        item
        for item in diario.historial(limite=limite)
        if normalizar_usuario(item.get("usuario")) == clave
    ]
