"""Sincronización de empleados corporativos con la caché local.

La caché es independiente del maestro ``USUARIO -> ÁREA``. Sincronizar nunca
sobrescribe correcciones manuales del maestro: solo actualiza la fotografía de
empleados y departamentos que usa la resolución automática.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

import pandas as pd

from config import configuracion as cfg
from config.sqlserver import ConfiguracionSQL, cargar_configuracion
from core.archivos import ErrorArchivo, escribir_dataframe_atomico, leer_excel, respaldar_archivo
from database.normalizador import indice_por_nombre, normalizar_nombre
from database.repository import Empleado, RepositorioEmpleados, ResultadoConsulta

LOGGER = logging.getLogger("automatizador")

COLUMNAS_CACHE = (
    "EMPRESA",
    "EMPLEADO",
    "NOMBRE",
    "PRIMER_APELLIDO",
    "SEGUNDO_APELLIDO",
    "NOMBRE_PILA",
    "DEPARTAMENTO_CODIGO",
    "DEPARTAMENTO",
    "PUESTO_CODIGO",
    "PUESTO",
    "ESTADO_EMPLEADO",
    "ACTIVO",
    "E_MAIL",
    "U_CORREO_CORP",
    "FUENTE",
)


def ruta_cache_xlsx() -> Path:
    return Path(cfg.DATA_DIR) / "cache_empleados.xlsx"


def ruta_cache_csv() -> Path:
    return Path(cfg.DATA_DIR) / "cache_empleados.csv"


def ruta_sincronizacion() -> Path:
    return Path(cfg.DATA_DIR) / "sincronizacion.json"


@dataclass
class ResultadoSincronizacion:
    nuevos: int = 0
    actualizados: int = 0
    departamentos_modificados: int = 0
    sin_departamento: int = 0
    ambiguos: int = 0
    errores: list[dict[str, str]] = field(default_factory=list)
    empresas: list[str] = field(default_factory=list)
    total_empleados: int = 0
    fecha: str = ""
    cache_xlsx: str = ""
    cache_csv: str = ""
    departamentos_modificados_detalle: list[dict[str, str]] = field(
        default_factory=list
    )
    alertas: list[dict[str, str]] = field(default_factory=list)
    empresas_sin_datos: list[str] = field(default_factory=list)
    empresas_sin_esquema: list[str] = field(default_factory=list)
    cache_conservada: bool = False
    mensaje_cache: str = ""

    @property
    def cantidad_errores(self) -> int:
        return len(self.errores)

    def como_dict(self) -> dict[str, Any]:
        return {
            "nuevos": self.nuevos,
            "actualizados": self.actualizados,
            "departamentos_modificados": self.departamentos_modificados,
            "departamentos_modificados_detalle": self.departamentos_modificados_detalle,
            "alertas": self.alertas,
            "sin_departamento": self.sin_departamento,
            "ambiguos": self.ambiguos,
            "errores": self.errores,
            "empresas": self.empresas,
            "total_empleados": self.total_empleados,
            "fecha": self.fecha,
            "cache_xlsx": self.cache_xlsx,
            "cache_csv": self.cache_csv,
            "empresas_sin_datos": self.empresas_sin_datos,
            "empresas_sin_esquema": self.empresas_sin_esquema,
            "cache_conservada": self.cache_conservada,
            "mensaje_cache": self.mensaje_cache,
        }


def _fila_desde_dict(item: dict[str, Any]) -> Empleado:
    return Empleado(
        empresa=str(item.get("EMPRESA") or ""),
        codigo=str(item.get("EMPLEADO") or ""),
        nombre=str(item.get("NOMBRE") or ""),
        primer_apellido=str(item.get("PRIMER_APELLIDO") or ""),
        segundo_apellido=str(item.get("SEGUNDO_APELLIDO") or ""),
        nombre_pila=str(item.get("NOMBRE_PILA") or ""),
        departamento_codigo=str(item.get("DEPARTAMENTO_CODIGO") or ""),
        departamento=str(item.get("DEPARTAMENTO") or ""),
        puesto_codigo=str(item.get("PUESTO_CODIGO") or ""),
        puesto=str(item.get("PUESTO") or ""),
        estado_empleado=str(item.get("ESTADO_EMPLEADO") or ""),
        activo=(
            None
            if str(item.get("ACTIVO") or "").strip() == ""
            else str(item.get("ACTIVO")).strip() in {"1", "True", "true"}
        ),
        email=str(item.get("E_MAIL") or ""),
        correo_corp=str(item.get("U_CORREO_CORP") or ""),
        fuente=str(item.get("FUENTE") or ""),
    )


def cargar_empleados() -> list[Empleado]:
    """Carga la caché desde Excel o, si no existe, desde el CSV espejo."""

    ruta = ruta_cache_xlsx()
    if ruta.is_file():
        try:
            df = leer_excel(ruta, sheet_name="EMPLEADOS")
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("No se pudo leer la caché de empleados: %s", exc)
            df = pd.DataFrame()
    else:
        ruta_csv = ruta_cache_csv()
        if not ruta_csv.is_file():
            return []
        try:
            df = pd.read_csv(ruta_csv, encoding="utf-8-sig")
        except OSError as exc:
            LOGGER.warning("No se pudo leer el CSV de empleados: %s", exc)
            return []

    if df is None or df.empty:
        return []
    for columna in COLUMNAS_CACHE:
        if columna not in df.columns:
            df[columna] = ""
    return [
        _fila_desde_dict(fila)
        for fila in df.loc[:, list(COLUMNAS_CACHE)].to_dict(orient="records")
    ]


def cargar_dataframe() -> pd.DataFrame:
    empleados = cargar_empleados()
    if not empleados:
        return pd.DataFrame(columns=list(COLUMNAS_CACHE))
    return pd.DataFrame(
        [empleado.como_dict() for empleado in empleados],
        columns=list(COLUMNAS_CACHE),
    )


def _rotar_respaldos_cache(limite: int = 10) -> None:
    carpeta = Path(cfg.BACKUPS_DIR)
    if not carpeta.is_dir():
        return
    copias = sorted(
        carpeta.glob("cache_empleados_*.xlsx"),
        key=lambda ruta: ruta.stat().st_mtime,
        reverse=True,
    )
    for copia in copias[limite:]:
        try:
            copia.unlink()
        except OSError:
            LOGGER.debug("No se pudo eliminar el respaldo antiguo %s", copia)


def guardar_cache(
    empleados: Iterable[Empleado],
    *,
    crear_backup: bool = True,
) -> tuple[Path, Path]:
    """Guarda la caché en Excel y CSV con respaldo previo."""

    lista = list(empleados)
    df = pd.DataFrame(
        [empleado.como_dict() for empleado in lista], columns=list(COLUMNAS_CACHE)
    )
    xlsx = ruta_cache_xlsx()
    csv = ruta_cache_csv()
    xlsx.parent.mkdir(parents=True, exist_ok=True)

    if crear_backup and xlsx.is_file():
        try:
            respaldar_archivo(xlsx, cfg.BACKUPS_DIR)
            _rotar_respaldos_cache(10)
        except ErrorArchivo:
            # Si no se puede respaldar, no se destruye la caché existente.
            raise

    escribir_dataframe_atomico(df, xlsx, hoja="EMPLEADOS")
    escribir_dataframe_atomico(df, csv)
    LOGGER.info("Caché de empleados actualizada: %s empleados", len(lista))
    return xlsx, csv


def _clave(item: Empleado) -> tuple[str, str, str]:
    return (
        item.empresa,
        item.fuente or item.tabla or "EMPLEADO",
        item.codigo,
    )


def _calcular_ambiguos(empleados: Sequence[Empleado]) -> int:
    indice = indice_por_nombre(empleados)
    ambiguous = 0
    vistos: set[tuple[str, str, str]] = set()
    for lista in indice.values():
        claves = {
            (
                getattr(item, "empresa", ""),
                getattr(item, "fuente", "") or getattr(item, "tabla", ""),
                getattr(item, "codigo", ""),
            )
            for item in lista
        }
        if len(claves) > 1:
            for clave in claves:
                if clave not in vistos:
                    vistos.add(clave)
            ambiguous += 1
    return ambiguous


def _alertas_contra_maestro(
    detalles: Sequence[dict[str, str]],
) -> list[dict[str, str]]:
    """Marca los cambios corporativos que difieren del área local asignada."""

    if not detalles:
        return []
    try:
        from core.asignaciones import cargar_maestro

        maestro = cargar_maestro()
    except Exception as exc:  # noqa: BLE001 - la alerta no bloquea la sincronización
        LOGGER.warning("No se pudo comparar la sincronización con el maestro: %s", exc)
        return []

    alertas: list[dict[str, str]] = []
    for item in detalles:
        usuario = str(item.get("usuario") or "")
        if not usuario:
            continue
        try:
            area_local = maestro.buscar(usuario)
        except Exception:  # noqa: BLE001
            area_local = None
        if not area_local:
            continue
        if normalizar_nombre(area_local) == normalizar_nombre(
            item.get("nuevo") or ""
        ):
            continue
        alertas.append(
            {
                "usuario": usuario,
                "empresa": str(item.get("empresa") or ""),
                "empleado": str(item.get("empleado") or ""),
                "area_local": str(area_local),
                "departamento_corporativo": str(item.get("nuevo") or ""),
                "motivo": (
                    "El departamento corporativo cambió y difiere del área "
                    "asignada localmente. Revise antes de reasignar."
                ),
            }
        )
    return alertas


class Sincronizador:
    """Orquesta la sincronización completa y el volcado a la caché."""

    def __init__(
        self,
        config: ConfiguracionSQL | None = None,
        *,
        repositorio: RepositorioEmpleados | None = None,
    ) -> None:
        self.config = config or cargar_configuracion()
        self.repositorio = repositorio or RepositorioEmpleados(self.config)

    def sincronizar_completo(
        self,
        empresas: Sequence[str] | None = None,
    ) -> ResultadoSincronizacion:
        anteriores = {_clave(item): item for item in cargar_empleados()}
        consulta: ResultadoConsulta = self.repositorio.obtener_todos(empresas)
        actuales = {_clave(item): item for item in consulta.empleados}

        # Si una empresa falló, se conservan sus empleados de la caché anterior
        # en lugar de eliminarlos por no haber podido consultarlos.
        if consulta.errores and anteriores:
            fallidas = set(consulta.empresas_con_error)
            for clave, empleado in anteriores.items():
                if empleado.empresa in fallidas and clave not in actuales:
                    actuales[clave] = empleado

        ahora = datetime.now().astimezone().isoformat(timespec="seconds")
        if not actuales and (anteriores or consulta.errores):
            # Nunca se reemplaza una caché válida por una consulta vacía o
            # fallida. Se informa el motivo y se conservan los archivos.
            mensaje = (
                "No se actualizó la caché: la consulta no devolvió empleados"
                + (
                    f" y {len(consulta.errores)} empresa(s) reportaron error."
                    if consulta.errores
                    else "."
                )
            )
            resultado = ResultadoSincronizacion(
                errores=consulta.errores,
                empresas=consulta.empresas_consultadas,
                total_empleados=len(anteriores),
                fecha=ahora,
                cache_xlsx=str(ruta_cache_xlsx()) if ruta_cache_xlsx().is_file() else "",
                cache_csv=str(ruta_cache_csv()) if ruta_cache_csv().is_file() else "",
                empresas_sin_datos=list(consulta.empresas_sin_datos),
                empresas_sin_esquema=list(consulta.empresas_sin_esquema),
                cache_conservada=bool(anteriores),
                mensaje_cache=mensaje,
            )
            _escribir_metadatos(resultado)
            LOGGER.warning(mensaje)
            return resultado

        nuevos = 0
        actualizados = 0
        departamentos_modificados = 0
        detalles_departamentos: list[dict[str, str]] = []
        campos_relevantes = (
            "nombre",
            "primer_apellido",
            "segundo_apellido",
            "nombre_pila",
            "departamento",
            "puesto",
            "activo",
            "estado_empleado",
            "email",
            "correo_corp",
        )
        for clave, empleado in actuales.items():
            previo = anteriores.get(clave)
            if previo is None:
                nuevos += 1
                continue
            if any(
                getattr(previo, campo) != getattr(empleado, campo)
                for campo in campos_relevantes
            ):
                actualizados += 1
            if (
                previo.departamento
                and empleado.departamento
                and normalizar_nombre(previo.departamento)
                != normalizar_nombre(empleado.departamento)
            ):
                departamentos_modificados += 1
                detalles_departamentos.append(
                    {
                        "empresa": empleado.empresa,
                        "empleado": empleado.codigo,
                        "usuario": empleado.nombre_completo,
                        "anterior": previo.departamento,
                        "nuevo": empleado.departamento,
                    }
                )

        lista = list(actuales.values())
        xlsx, csv = guardar_cache(lista, crear_backup=bool(anteriores))
        alertas = _alertas_contra_maestro(detalles_departamentos)
        mensaje_cache = ""
        if consulta.errores and anteriores:
            mensaje_cache = (
                "Se conservaron los empleados de las empresas que fallaron; "
                "no se eliminaron de la caché."
            )
        resultado = ResultadoSincronizacion(
            nuevos=nuevos,
            actualizados=actualizados,
            departamentos_modificados=departamentos_modificados,
            sin_departamento=sum(1 for item in lista if not item.tiene_departamento),
            ambiguos=_calcular_ambiguos(lista),
            errores=consulta.errores,
            empresas=consulta.empresas_consultadas,
            total_empleados=len(lista),
            fecha=ahora,
            cache_xlsx=str(xlsx),
            cache_csv=str(csv),
            departamentos_modificados_detalle=detalles_departamentos,
            alertas=alertas,
            empresas_sin_datos=list(consulta.empresas_sin_datos),
            empresas_sin_esquema=list(consulta.empresas_sin_esquema),
            cache_conservada=bool(mensaje_cache),
            mensaje_cache=mensaje_cache,
        )
        _escribir_metadatos(resultado)
        return resultado

    def actualizar_desde_busqueda(self, empleados: Iterable[Empleado]) -> int:
        """Incorpora a la caché los empleados encontrados por búsqueda puntual."""

        actuales = {_clave(item): item for item in cargar_empleados()}
        agregados = 0
        for empleado in empleados:
            clave = _clave(empleado)
            if clave not in actuales or actuales[clave] != empleado:
                actuales[clave] = empleado
                agregados += 1
        if agregados:
            guardar_cache(list(actuales.values()), crear_backup=True)
        return agregados


def _escribir_metadatos(resultado: ResultadoSincronizacion) -> None:
    datos = resultado.como_dict()
    datos["version"] = 1
    ruta = ruta_sincronizacion()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_name(f".{ruta.name}.tmp")
    temporal.write_text(
        json.dumps(datos, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temporal.replace(ruta)


def resumen_sincronizacion() -> dict[str, Any]:
    """Información para el panel de configuración y la ventana de validación."""

    resumen: dict[str, Any] = {
        "fecha": "",
        "total_empleados": 0,
        "errores": 0,
        "empresas": 0,
        "nuevos": 0,
        "actualizados": 0,
        "sin_departamento": 0,
        "ambiguos": 0,
        "alertas": 0,
    }
    ruta = ruta_sincronizacion()
    if ruta.is_file():
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
            if isinstance(datos, dict):
                resumen.update(
                    {
                        "fecha": str(datos.get("fecha") or ""),
                        "total_empleados": int(datos.get("total_empleados") or 0),
                        "errores": len(datos.get("errores") or []),
                        "empresas": len(datos.get("empresas") or []),
                        "nuevos": int(datos.get("nuevos") or 0),
                        "actualizados": int(datos.get("actualizados") or 0),
                        "sin_departamento": int(
                            datos.get("sin_departamento") or 0
                        ),
                        "ambiguos": int(datos.get("ambiguos") or 0),
                        "alertas": len(datos.get("alertas") or []),
                    }
                )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            LOGGER.warning("No se pudo leer el resumen de sincronización")
    if not resumen["total_empleados"]:
        resumen["total_empleados"] = len(cargar_empleados())
    return resumen
