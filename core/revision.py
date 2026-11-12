"""Persistencia del trabajo de validación de usuarios y áreas.

Cada procesamiento puede generar una revisión independiente. El estado se
guarda en ``output/revision/revision_<id>.json`` (ruta configurable) y nunca se
escribe sobre el Excel original ni sobre el archivo canónico.

El usuario puede cerrar la ventana o la aplicación y reanudar después: la
revisión conserva pendientes, áreas encontradas, correcciones manuales,
coincidencias seleccionadas, estados, fecha e historial de cambios.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from config import configuracion as cfg
from core import seleccion_empleados as sel
from core.archivos import ErrorArchivo, escribir_json_atomico
from database.normalizador import normalizar_nombre

LOGGER = logging.getLogger("automatizador")

ESTADO_VALIDADO = "Validado"
ESTADO_EN_REVISION = "En revisión"


def carpeta_revisiones() -> Path:
    return Path(cfg.REVISION_DIR)


def _limpiar_id(valor: str) -> str:
    texto = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(valor or "").strip())
    return texto or f"REV_{datetime.now():%Y%m%d_%H%M%S}"


def _ahora() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _area(valor: Any) -> str:
    return str(valor or "").strip()


def _fila_de(item: dict[str, Any]) -> str:
    filas = item.get("filas") or []
    if isinstance(filas, list) and filas:
        return f"Fila {filas[0]}"
    return ""


@dataclass
class Revision:
    """Estado persistente de una sesión de validación."""

    id_ejecucion: str
    archivo_original: str = ""
    nombre_original: str = ""
    archivo_procesado: str = ""
    archivo_manifiesto: str = ""
    fecha_creacion: str = field(default_factory=_ahora)
    actualizado: str = field(default_factory=_ahora)
    total_registros: int = 0
    areas_validadas: int = 0
    registros: list[dict[str, Any]] = field(default_factory=list)
    historial: list[dict[str, Any]] = field(default_factory=list)
    ruta: Path | None = None
    generacion: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Construcción
    # ------------------------------------------------------------------
    @classmethod
    def desde_manifiesto(
        cls,
        datos_manifiesto: dict[str, Any] | Path | str,
        *,
        resoluciones: dict[str, Any] | None = None,
        archivo_procesado: str = "",
        archivo_manifiesto: str = "",
        conflictos: Iterable[dict[str, Any]] = (),
    ) -> "Revision":
        if isinstance(datos_manifiesto, (str, Path)):
            ruta = Path(datos_manifiesto)
            try:
                datos_manifiesto = json.loads(ruta.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ErrorArchivo(
                    f"No se pudo leer el manifiesto para crear la revisión: {exc}"
                ) from exc

        if not isinstance(datos_manifiesto, dict):
            raise ErrorArchivo("El manifiesto no tiene el formato esperado.")

        resoluciones = resoluciones or {}
        revision = cls(
            id_ejecucion=str(datos_manifiesto.get("id_ejecucion") or ""),
            archivo_original=str(datos_manifiesto.get("archivo_entrada") or ""),
            nombre_original=str(datos_manifiesto.get("nombre_entrada") or ""),
            archivo_procesado=str(archivo_procesado or ""),
            archivo_manifiesto=str(archivo_manifiesto or ""),
            total_registros=int(datos_manifiesto.get("resumen", {}).get("Total de registros") or 0),
        )

        pendientes = datos_manifiesto.get("usuarios_pendientes") or []
        for item in pendientes:
            if not isinstance(item, dict):
                continue
            usuario = str(item.get("usuario") or item.get("clave") or "").strip()
            clave = str(item.get("clave") or normalizar_nombre(usuario) or "")
            resolucion = resoluciones.get(clave) or {}
            estado = str(resolucion.get("estado") or "Área vacía")
            revision.registros.append(
                {
                    "usuario": usuario,
                    "clave": clave,
                    "empresa": str(resolucion.get("empleado", {}).get("EMPRESA") or ""),
                    "ticket": _fila_de(item),
                    "area_actual": _area(
                        resolucion.get("area") or item.get("area_preexistente")
                    ),
                    "area_sugerida": _area(resolucion.get("area")),
                    "estado": estado,
                    "caso": str(resolucion.get("caso") or ""),
                    "motivo": str(resolucion.get("motivo") or ""),
                    "candidatos": list(resolucion.get("candidatos") or []),
                    "seleccionado": {},
                    "seleccion": {},
                    "trazabilidad": {},
                    "origen": str(resolucion.get("origen_anterior") or ""),
                    "validado": estado == ESTADO_VALIDADO,
                    "filas": list(item.get("filas") or []),
                    "historial": [],
                }
            )

        for conflicto in conflictos:
            if not isinstance(conflicto, dict):
                continue
            usuario = str(conflicto.get("usuario") or "").strip()
            clave = str(conflicto.get("clave") or normalizar_nombre(usuario) or "")
            if not clave:
                continue
            existente = revision.buscar(clave)
            if existente is not None:
                existente["estado"] = str(conflicto.get("estado") or "Conflicto con maestro")
                existente["motivo"] = str(conflicto.get("motivo") or existente["motivo"])
                existente["area_sugerida"] = _area(conflicto.get("area"))
                existente["origen"] = str(conflicto.get("origen_anterior") or "")
                continue
            revision.registros.append(
                {
                    "usuario": usuario,
                    "clave": clave,
                    "empresa": str(conflicto.get("empresa") or ""),
                    "ticket": _fila_de(conflicto),
                    "area_actual": _area(conflicto.get("area_anterior")),
                    "area_sugerida": _area(conflicto.get("area")),
                    "estado": str(conflicto.get("estado") or "Conflicto con maestro"),
                    "caso": str(conflicto.get("caso") or ""),
                    "motivo": str(conflicto.get("motivo") or ""),
                    "candidatos": list(conflicto.get("candidatos") or []),
                    "seleccionado": {},
                    "seleccion": {},
                    "trazabilidad": {},
                    "origen": str(conflicto.get("origen_anterior") or ""),
                    "validado": False,
                    "filas": list(conflicto.get("filas") or []),
                    "historial": [],
                }
            )

        revision._recalcular()
        return revision

    # ------------------------------------------------------------------
    def _recalcular(self) -> None:
        self.areas_validadas = sum(
            1
            for registro in self.registros
            if registro.get("validado")
            or (
                _area(registro.get("area_actual"))
                and registro.get("estado") == ESTADO_VALIDADO
            )
        )
        self.actualizado = _ahora()

    def _anotar(self, clave: str, accion: str, detalle: str = "") -> None:
        self.historial.append(
            {
                "fecha": _ahora(),
                "clave": clave,
                "accion": accion,
                "detalle": detalle,
            }
        )

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------
    def buscar(self, usuario: Any) -> dict[str, Any] | None:
        clave = normalizar_nombre(usuario)
        if not clave:
            return None
        for registro in self.registros:
            if registro.get("clave") == clave or (
                normalizar_nombre(registro.get("usuario")) == clave
            ):
                return registro
        return None

    @property
    def pendientes(self) -> list[dict[str, Any]]:
        return [registro for registro in self.registros if not self.validado(registro)]

    @property
    def cantidad_pendientes(self) -> int:
        return len(self.pendientes)

    def validado(self, registro: dict[str, Any]) -> bool:
        if registro.get("validado"):
            return True
        estado = str(registro.get("estado") or "")
        area = _area(registro.get("area_actual"))
        return estado == ESTADO_VALIDADO and bool(area)

    def resumen(self) -> dict[str, Any]:
        self._recalcular()
        return {
            "id_ejecucion": self.id_ejecucion,
            "archivo_original": self.archivo_original,
            "nombre_original": self.nombre_original,
            "total_registros": self.total_registros,
            "areas_validadas": self.areas_validadas,
            "por_revisar": self.cantidad_pendientes,
            "total_usuarios": len(self.registros),
        }

    # ------------------------------------------------------------------
    # Acciones de validación
    # ------------------------------------------------------------------
    def actualizar(
        self,
        usuario: Any,
        *,
        area: str | None = None,
        estado: str | None = None,
        seleccionado: dict[str, Any] | None = None,
        candidatos: Iterable[dict[str, Any]] | None = None,
        validado: bool | None = None,
        origen: str | None = None,
        motivo: str | None = None,
        tipo_resolucion: str = "manual",
        accion: str = "actualizacion",
    ) -> dict[str, Any]:
        registro = self.buscar(usuario)
        if registro is None:
            raise ErrorArchivo(
                f"El usuario '{usuario}' no forma parte de esta revisión."
            )

        if area is not None:
            registro["area_actual"] = _area(area)
        if estado is not None:
            registro["estado"] = str(estado)
            registro["validado"] = str(estado) == ESTADO_VALIDADO
        if candidatos is not None:
            registro["candidatos"] = [dict(candidato) for candidato in candidatos]
        if validado is not None:
            registro["validado"] = bool(validado)
        if seleccionado is not None:
            registro["seleccionado"] = dict(seleccionado)
            if seleccionado:
                # El departamento del empleado NO es el área. El área se pasa
                # explícitamente después de aplicar las equivalencias.
                registro["empresa"] = str(
                    seleccionado.get("empresa")
                    or seleccionado.get("EMPRESA")
                    or ""
                )
                registro["seleccion"] = sel.registro_trazable(
                    seleccionado,
                    area=_area(registro.get("area_actual")),
                    tipo_resolucion=tipo_resolucion,
                )
                registro["trazabilidad"] = {
                    "empleado": registro["seleccion"].get("empleado", ""),
                    "nombre": registro["seleccion"].get("nombre", ""),
                    "empresa": registro["seleccion"].get("empresa", ""),
                    "fuente": registro["seleccion"].get("fuente", ""),
                    "departamento": registro["seleccion"].get("departamento", ""),
                    "area": _area(registro.get("area_actual")),
                    "fecha": registro["seleccion"].get("fecha", ""),
                    "tipo_resolucion": registro["seleccion"].get(
                        "tipo_resolucion", "manual"
                    ),
                }
                if area is None:
                    # Sin área resuelta no se valida ni se inventa un área.
                    registro["validado"] = False
                else:
                    registro["validado"] = bool(_area(registro.get("area_actual")))
                    if registro["validado"]:
                        registro["estado"] = ESTADO_VALIDADO
        if origen is not None:
            registro["origen"] = str(origen)
        if motivo is not None:
            registro["motivo"] = str(motivo)

        registro.setdefault("historial", []).append(
            {
                "fecha": _ahora(),
                "accion": accion,
                "area": registro.get("area_actual", ""),
                "estado": registro.get("estado", ""),
            }
        )
        self._anotar(
            registro.get("clave", ""),
            accion,
            f"{registro.get('usuario', '')}: {registro.get('area_actual', '')}",
        )
        return registro

    def asignar_manual(
        self,
        usuario: Any,
        area: str,
        *,
        origen: str = "MANUAL",
        motivo: str = "Asignación manual en la ventana de validación.",
    ) -> dict[str, Any]:
        return self.actualizar(
            usuario,
            area=area,
            estado=ESTADO_VALIDADO,
            validado=True,
            origen=origen,
            motivo=motivo,
            accion="asignacion_manual",
        )

    def registrar_seleccion(
        self,
        usuario: Any,
        candidato: dict[str, Any],
        *,
        area: str = "",
        estado: str | None = None,
        motivo: str = "",
        tipo_resolucion: str = "manual",
        origen: str = "SQL_SERVER",
    ) -> dict[str, Any]:
        """Conserva la coincidencia elegida y su trazabilidad.

        ``area`` debe ser el área ya resuelta por las equivalencias. El
        departamento del candidato es solo el origen de esa resolución y nunca
        se escribe como si fuera un área.
        """

        trazable = sel.registro_trazable(
            candidato,
            area=area,
            tipo_resolucion=tipo_resolucion,
        )
        registro = self.actualizar(
            usuario,
            area=str(area or "").strip() or None,
            estado=estado,
            seleccionado=dict(candidato),
            origen=origen,
            motivo=motivo or "Coincidencia seleccionada manualmente entre varias.",
            tipo_resolucion=tipo_resolucion,
            accion="seleccion_candidato",
        )
        registro["seleccion"] = trazable
        trazabilidad = registro.setdefault("trazabilidad", {})
        trazabilidad.update(
            {
                "empleado": trazable.get("empleado", ""),
                "nombre": trazable.get("nombre", ""),
                "empresa": trazable.get("empresa", ""),
                "fuente": trazable.get("fuente", ""),
                "departamento": trazable.get("departamento", ""),
                "area": _area(registro.get("area_actual")),
                "fecha": trazable.get("fecha", ""),
                "tipo_resolucion": trazable.get("tipo_resolucion", "manual"),
            }
        )
        return registro

    def seleccionar_candidato(
        self,
        usuario: Any,
        candidato: dict[str, Any],
        *,
        area: str = "",
        estado: str | None = None,
        motivo: str = "",
        tipo_resolucion: str = "manual",
    ) -> dict[str, Any]:
        """Compatibilidad: registra una selección manual del candidato."""

        return self.registrar_seleccion(
            usuario,
            candidato,
            area=area,
            estado=estado,
            motivo=motivo,
            tipo_resolucion=tipo_resolucion,
        )

    def seleccion_guardada_valida(
        self,
        usuario: Any,
        actual: dict[str, Any] | None = None,
    ) -> bool:
        """Indica si la selección previa sigue siendo coherente."""

        registro = self.buscar(usuario)
        if not registro:
            return False
        guardada = registro.get("seleccion") or registro.get("seleccionado") or {}
        return sel.seleccion_valida(guardada, actual)

    def validar_todos(self) -> int:
        """Marca como validados los registros que ya tienen un área."""

        cantidad = 0
        for registro in self.registros:
            if _area(registro.get("area_actual")) and not registro.get("validado"):
                registro["validado"] = True
                registro["estado"] = ESTADO_VALIDADO
                cantidad += 1
        if cantidad:
            self._anotar("", "validacion_masiva", f"{cantidad} registros")
        return cantidad

    def reabrir(self) -> "Revision":
        self._anotar("", "reapertura", self.archivo_original)
        return self

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------
    def como_dict(self) -> dict[str, Any]:
        self._recalcular()
        return {
            "version": 1,
            "id_ejecucion": self.id_ejecucion,
            "archivo_original": self.archivo_original,
            "nombre_original": self.nombre_original,
            "archivo_procesado": self.archivo_procesado,
            "archivo_manifiesto": self.archivo_manifiesto,
            "fecha_creacion": self.fecha_creacion,
            "actualizado": self.actualizado,
            "total_registros": self.total_registros,
            "areas_validadas": self.areas_validadas,
            "registros": self.registros,
            "historial": self.historial,
            "generacion": self.generacion,
            "resumen": self.resumen(),
        }

    def guardar(self, ruta: Path | str | None = None) -> Path:
        destino = Path(ruta) if ruta else (
            carpeta_revisiones() / f"revision_{_limpiar_id(self.id_ejecucion)}.json"
        )
        escribir_json_atomico(self.como_dict(), destino)
        self.ruta = destino
        return destino

    @classmethod
    def cargar(cls, ruta: Path | str) -> "Revision":
        archivo = Path(ruta)
        try:
            datos = json.loads(archivo.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ErrorArchivo(f"No se pudo leer la revisión '{archivo}': {exc}") from exc
        if not isinstance(datos, dict):
            raise ErrorArchivo(f"La revisión '{archivo}' no tiene formato válido.")

        permitidos = set(cls.__dataclass_fields__) - {"ruta"}
        limpio = {
            clave: valor
            for clave, valor in datos.items()
            if clave in permitidos
        }
        limpio.setdefault(
            "id_ejecucion", archivo.stem.replace("revision_", "")
        )
        revision = cls(**limpio)
        revision.ruta = archivo
        if not isinstance(revision.registros, list):
            revision.registros = []
        if not isinstance(revision.historial, list):
            revision.historial = []
        return revision.reabrir()

    def eliminar(self) -> bool:
        if self.ruta and self.ruta.is_file():
            self.ruta.unlink()
            return True
        return False


def listar_revisiones(carpeta: Path | str | None = None) -> list[dict[str, Any]]:
    """Lista las revisiones disponibles, más recientes primero."""

    directorio = Path(carpeta or carpeta_revisiones())
    if not directorio.is_dir():
        return []
    salida: list[dict[str, Any]] = []
    for archivo in directorio.glob("revision_*.json"):
        try:
            datos = json.loads(archivo.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(datos, dict):
            continue
        resumen = datos.get("resumen") if isinstance(datos.get("resumen"), dict) else {}
        salida.append(
            {
                "ruta": str(archivo),
                "id_ejecucion": str(datos.get("id_ejecucion") or ""),
                "nombre_original": str(
                    datos.get("nombre_original")
                    or datos.get("archivo_original")
                    or ""
                ),
                "actualizado": str(datos.get("actualizado") or ""),
                "total_registros": int(
                    resumen.get("total_registros")
                    or datos.get("total_registros")
                    or 0
                ),
                "areas_validadas": int(
                    resumen.get("areas_validadas")
                    or datos.get("areas_validadas")
                    or 0
                ),
                "por_revisar": int(resumen.get("por_revisar") or 0),
            }
        )
    salida.sort(key=lambda item: item["actualizado"], reverse=True)
    return salida


def buscar_revision_reciente(nombre_original: str) -> Path | None:
    """Busca la revisión más reciente de un archivo original."""

    objetivo = Path(str(nombre_original or "")).name.lower()
    for item in listar_revisiones():
        if Path(str(item.get("nombre_original") or "")).name.lower() == objetivo:
            return Path(str(item["ruta"]))
    return None
