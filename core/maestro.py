"""Gestión de la base maestra USUARIO -> AREA_USUARIO."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from config import configuracion as cfg
from core.archivos import (
    COLUMNA_AREA,
    COLUMNA_USUARIO,
    ErrorArchivo,
    comparar_dataframes,
    leer_excel,
    mapear_columnas,
    respaldar_archivo,
    validar_columnas,
)
from core.normalizador import (
    es_area_valida,
    limpiar_area,
    limpiar_usuario,
    normalizar_area,
    normalizar_usuario,
)
from core.retroalimentacion import Manifiesto

LOGGER = logging.getLogger("automatizador")


class ErrorMaestro(ErrorArchivo):
    """Error controlado de la base maestra."""


@dataclass
class ResultadoActualizacion:
    registros_analizados: int = 0
    usuarios_nuevos: int = 0
    correspondencias_actualizadas: int = 0
    sin_cambios: int = 0
    registros_omitidos: int = 0
    duplicados_detectados: int = 0
    conflictos_detectados: int = 0
    asignaciones_automaticas_ignoradas: int = 0
    correcciones_manuales: int = 0
    registros_antiguos_ignorados: int = 0
    areas_vacias_ignoradas: int = 0
    detalles_nuevos: list[str] = field(default_factory=list)
    detalles_actualizados: list[str] = field(default_factory=list)
    detalles_conflictos: list[str] = field(default_factory=list)
    detalles_automaticos: list[str] = field(default_factory=list)
    detalles_antiguos: list[str] = field(default_factory=list)
    cambios: list[dict[str, str]] = field(default_factory=list)
    manifiesto_usado: str | None = None
    fecha_archivo_ns: int | None = None


@dataclass
class ResultadoGuardado:
    """Rutas y copias de seguridad generadas al guardar el maestro."""

    ruta_xlsx: Path | None
    ruta_csv: Path | None
    respaldos: list[Path] = field(default_factory=list)


class Maestro:
    """Base maestra con una única correspondencia válida por usuario."""

    COLUMNAS = (COLUMNA_USUARIO, COLUMNA_AREA)

    def __init__(self, df: pd.DataFrame | None = None):
        self._df = self._preparar(df)
        self._mapa: dict[str, str] = {}
        self._reconstruir_indice()

    # ------------------------------------------------------------------
    # Construcción interna
    # ------------------------------------------------------------------
    def _preparar(self, df: pd.DataFrame | None) -> pd.DataFrame:
        if df is None or len(df) == 0:
            return pd.DataFrame(columns=list(self.COLUMNAS))

        df = mapear_columnas(df)
        validar_columnas(df, self.COLUMNAS)

        filas: list[dict[str, str]] = []
        vistos: set[str] = set()

        for _, fila in df.iterrows():
            usuario = limpiar_usuario(fila[COLUMNA_USUARIO])
            clave = normalizar_usuario(fila[COLUMNA_USUARIO])
            area = limpiar_area(fila[COLUMNA_AREA])

            # Usuarios sin identificador válido se excluyen.
            if clave is None:
                continue

            # Nunca se incorpora una correspondencia sin área válida.
            if area is None:
                continue

            if clave in vistos:
                LOGGER.warning(
                    "Usuario duplicado en la base maestra; se conserva la primera "
                    "correspondencia: %s",
                    usuario or clave,
                )
                continue

            vistos.add(clave)
            filas.append({COLUMNA_USUARIO: usuario or clave, COLUMNA_AREA: area})

        return pd.DataFrame(filas, columns=list(self.COLUMNAS))

    def _reconstruir_indice(self) -> None:
        self._mapa = {}
        for usuario, area in zip(
            self._df[COLUMNA_USUARIO].tolist(),
            self._df[COLUMNA_AREA].tolist(),
        ):
            clave = normalizar_usuario(usuario)
            if clave:
                self._mapa[clave] = area

    # ------------------------------------------------------------------
    # Carga y guardado
    # ------------------------------------------------------------------
    @classmethod
    def cargar(
        cls,
        ruta_xlsx: Path | str | None = None,
        ruta_csv: Path | str | None = None,
        verificar_consistencia: bool = True,
    ) -> "Maestro":
        """Carga el maestro desde .xlsx o, si no existe, desde .csv.

        Si ambos archivos existen, se verifica que contengan exactamente la
        misma información. Una discrepancia detiene la carga para evitar que
        una copia obsoleta sobrescriba la base válida.
        """

        ruta_xlsx = Path(ruta_xlsx or cfg.MAESTRO_XLSX)
        ruta_csv = Path(ruta_csv or ruta_xlsx.with_suffix(".csv"))

        df_xlsx: pd.DataFrame | None = None
        df_csv: pd.DataFrame | None = None

        if ruta_xlsx.exists():
            try:
                df_xlsx = leer_excel(ruta_xlsx)
            except OSError as exc:
                raise ErrorMaestro(
                    f"No se pudo leer la base maestra '{ruta_xlsx}': {exc}"
                ) from exc

        if ruta_csv.exists():
            try:
                df_csv = pd.read_csv(ruta_csv, encoding="utf-8-sig")
            except OSError as exc:
                raise ErrorMaestro(
                    f"No se pudo leer la base maestra '{ruta_csv}': {exc}"
                ) from exc

        if df_xlsx is not None and df_csv is not None and verificar_consistencia:
            problemas = comparar_dataframes(
                mapear_columnas(df_xlsx),
                mapear_columnas(df_csv),
            )
            if problemas:
                raise ErrorMaestro(
                    "Discrepancia entre la base maestra Excel y su espejo CSV: "
                    + " ".join(problemas)
                    + " Corrija o reconstruya la base antes de continuar."
                )

        df = df_xlsx if df_xlsx is not None else df_csv
        if df is None:
            raise ErrorMaestro(
                "No existe la base maestra. Ejecute primero 'construir_maestro.py'."
            )

        return cls(df)

    def guardar(
        self,
        ruta_xlsx: Path | str | None = None,
        ruta_csv: Path | str | None = None,
        crear_backup: bool = True,
    ) -> ResultadoGuardado:
        """Guarda Excel y CSV de forma transaccional.

        Primero se escriben y validan archivos temporales; luego se reemplazan
        ambos destinos. Si falla el segundo reemplazo, el primero se restaura
        desde una copia de rollback. Al final se vuelve a comprobar que Excel y
        CSV contengan la misma información.
        """

        ruta = Path(ruta_xlsx or cfg.MAESTRO_XLSX)

        problemas = self.validar()
        if problemas:
            raise ErrorMaestro(
                "La base maestra no superó la validación: " + " ".join(problemas)
            )

        df = self._df.copy()

        if ruta.suffix.lower() == ".csv":
            ruta_xlsx_final: Path | None = None
            ruta_csv_final: Path | None = ruta
            objetivos = [ruta]
        else:
            ruta_xlsx_final = ruta
            ruta_csv_final = (
                Path(ruta_csv or ruta.with_suffix(".csv"))
                if cfg.ESCRIBIR_CSV_ESPEJO
                else None
            )
            objetivos = [ruta_xlsx_final]
            if ruta_csv_final is not None:
                objetivos.append(ruta_csv_final)

        temporales: dict[Path, Path] = {}
        reemplazados: list[Path] = []
        respaldos: list[Path] = []
        carpeta_rollback = Path(tempfile.mkdtemp(prefix="automatizador_rollback_"))
        rollback: dict[Path, Path] = {}

        try:
            # 1) Escribir y validar los temporales antes de tocar los destinos.
            for destino in objetivos:
                temporal = self._escribir_temporal(df, destino)
                temporales[destino] = temporal
                self._validar_temporal(df, temporal)

            # 2) Copias de seguridad visibles y copias internas de rollback.
            for destino in objetivos:
                if not destino.exists():
                    continue
                if crear_backup:
                    respaldo = respaldar_archivo(destino)
                    if respaldo is not None:
                        respaldos.append(respaldo)
                copia_rollback = carpeta_rollback / destino.name
                shutil.copy2(destino, copia_rollback)
                rollback[destino] = copia_rollback

            # 3) Reemplazo controlado.
            for destino in objetivos:
                os.replace(temporales[destino], destino)
                reemplazados.append(destino)

            # 4) Verificación posterior de la consistencia Excel/CSV.
            if len(objetivos) == 2:
                problemas_finales = self._verificar_destinos(objetivos[0], objetivos[1])
                if problemas_finales:
                    raise ErrorMaestro(
                        "Inconsistencia detectada después de guardar: "
                        + " ".join(problemas_finales)
                    )

        except Exception as exc:
            self._restaurar_destinos(reemplazados, rollback)
            if isinstance(exc, (ErrorArchivo, ErrorMaestro)):
                raise
            if isinstance(exc, PermissionError) or getattr(
                exc, "winerror", None
            ) in (32, 33):
                raise ErrorArchivo(
                    "No se pudo guardar la base maestra. "
                    "Verifique que los archivos no estén abiertos en Excel."
                ) from exc
            raise ErrorArchivo(
                f"No se pudo guardar la base maestra de forma transaccional: {exc}"
            ) from exc
        finally:
            for temporal in temporales.values():
                try:
                    temporal.unlink(missing_ok=True)
                except OSError:
                    pass
            shutil.rmtree(carpeta_rollback, ignore_errors=True)

        return ResultadoGuardado(
            ruta_xlsx=ruta_xlsx_final,
            ruta_csv=ruta_csv_final,
            respaldos=respaldos,
        )

    # ------------------------------------------------------------------
    # Apoyo al guardado transaccional
    # ------------------------------------------------------------------
    def _escribir_temporal(self, df: pd.DataFrame, destino: Path) -> Path:
        destino.parent.mkdir(parents=True, exist_ok=True)
        descriptor, nombre = tempfile.mkstemp(
            prefix=f".{destino.stem}_",
            suffix=destino.suffix,
            dir=str(destino.parent),
        )
        os.close(descriptor)
        temporal = Path(nombre)

        try:
            if destino.suffix.lower() == ".csv":
                df.to_csv(temporal, index=False, encoding="utf-8-sig")
            else:
                df.to_excel(
                    temporal,
                    index=False,
                    sheet_name="MAESTRO",
                    engine="openpyxl",
                )
        except Exception:
            temporal.unlink(missing_ok=True)
            raise

        return temporal

    @staticmethod
    def _leer_destino(ruta: Path) -> pd.DataFrame:
        if ruta.suffix.lower() == ".csv":
            return pd.read_csv(ruta, encoding="utf-8-sig")
        return leer_excel(ruta)

    def _validar_temporal(self, df: pd.DataFrame, temporal: Path) -> None:
        leido = self._leer_destino(temporal)
        problemas = comparar_dataframes(df, mapear_columnas(leido))
        if problemas:
            raise ErrorMaestro(
                f"El archivo temporal '{temporal.name}' no coincide con el "
                "maestro en memoria: " + " ".join(problemas)
            )

    def _verificar_destinos(self, ruta_xlsx: Path, ruta_csv: Path) -> list[str]:
        try:
            df_xlsx = self._leer_destino(ruta_xlsx)
            df_csv = self._leer_destino(ruta_csv)
        except OSError as exc:
            LOGGER.warning(
                "No se pudo verificar la consistencia final Excel/CSV: %s", exc
            )
            return []
        return comparar_dataframes(
            mapear_columnas(df_xlsx),
            mapear_columnas(df_csv),
        )

    @staticmethod
    def _restaurar_destinos(
        reemplazados: list[Path],
        rollback: dict[Path, Path],
    ) -> None:
        errores: list[str] = []
        for destino in reversed(reemplazados):
            try:
                if destino in rollback:
                    shutil.copy2(rollback[destino], destino)
                else:
                    destino.unlink(missing_ok=True)
            except OSError as exc:
                errores.append(f"{destino}: {exc}")

        if errores:
            LOGGER.critical(
                "No se pudieron restaurar todos los archivos tras un fallo: %s",
                " | ".join(errores),
            )

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------
    def buscar(self, usuario: object) -> str | None:
        """Devuelve el área del maestro para un usuario, o None."""

        clave = normalizar_usuario(usuario)
        if clave is None:
            return None
        return self._mapa.get(clave)

    def contiene(self, usuario: object) -> bool:
        return self.buscar(usuario) is not None

    @property
    def usuarios(self) -> set[str]:
        return set(self._mapa)

    def como_dataframe(self) -> pd.DataFrame:
        return self._df.copy()

    def __len__(self) -> int:
        return len(self._df)

    # ------------------------------------------------------------------
    # Actualización por retroalimentación
    # ------------------------------------------------------------------
    def actualizar(
        self,
        registros: pd.DataFrame,
        manifiesto: Manifiesto | None = None,
        fecha_archivo_ns: int | None = None,
        ultimas_manuales: dict[str, int] | None = None,
    ) -> ResultadoActualizacion:
        """Incorpora correcciones manuales respetando las reglas del maestro.

        Reglas de protección:

        - Un área vacía nunca reemplaza un área válida.
        - Un valor idéntico a la asignación automática registrada en el
          manifiesto no se considera corrección manual.
        - Un archivo cuya fecha sea anterior a la última corrección registrada
          para ese usuario no puede sobrescribirla.
        - Entre varias filas del mismo usuario, las correcciones manuales
          prevalecen sobre las asignaciones automáticas y gana la última
          corrección válida.
        """

        total = 0 if registros is None else len(registros)
        resultado = ResultadoActualizacion(registros_analizados=total)
        resultado.fecha_archivo_ns = fecha_archivo_ns
        if manifiesto is not None and manifiesto.ruta is not None:
            resultado.manifiesto_usado = str(manifiesto.ruta)

        if registros is None or len(registros) == 0:
            return resultado

        registros = mapear_columnas(registros)
        validar_columnas(registros, self.COLUMNAS)

        automaticas = manifiesto.asignaciones if manifiesto else {}
        preexistentes = manifiesto.preexistentes if manifiesto else {}
        ultimas = ultimas_manuales or {}

        # clave -> usuario, filas válidas y candidatos a corrección manual.
        vistos: dict[str, dict[str, object]] = {}

        for _, fila in registros.iterrows():
            usuario = limpiar_usuario(fila[COLUMNA_USUARIO])
            clave = normalizar_usuario(fila[COLUMNA_USUARIO])
            area = limpiar_area(fila[COLUMNA_AREA])

            if clave is None:
                resultado.registros_omitidos += 1
                continue

            # Una celda vacía nunca sobrescribe un área válida.
            if area is None:
                resultado.registros_omitidos += 1
                resultado.areas_vacias_ignoradas += 1
                continue

            item = vistos.setdefault(
                clave,
                {
                    "usuario": usuario or clave,
                    "filas": 0,
                    "automaticas": 0,
                    "candidatos": [],
                },
            )
            item["filas"] = int(item["filas"]) + 1
            if usuario:
                item["usuario"] = usuario

            origen = "manual" if manifiesto is not None else "sin_manifiesto"

            if clave in automaticas:
                if normalizar_area(area) == normalizar_area(automaticas[clave]):
                    # Es exactamente lo que el sistema asignó: no es una
                    # corrección y no debe reaplicarse sobre el maestro.
                    resultado.asignaciones_automaticas_ignoradas += 1
                    item["automaticas"] = int(item["automaticas"]) + 1
                    if int(item["automaticas"]) == 1:
                        resultado.detalles_automaticos.append(
                            f"{usuario or clave}: '{area}' "
                            "(asignación automática, se conserva el maestro)"
                        )
                    continue
                origen = "manual"
            elif (
                clave in preexistentes
                and normalizar_area(area)
                == normalizar_area(preexistentes[clave])
            ):
                origen = "preexistente"

            ultima = ultimas.get(clave)
            if (
                ultima is not None
                and fecha_archivo_ns is not None
                and fecha_archivo_ns < ultima
            ):
                resultado.registros_antiguos_ignorados += 1
                resultado.detalles_antiguos.append(
                    f"{usuario or clave}: se ignoró '{area}' porque el archivo "
                    "es anterior a una corrección más reciente."
                )
                continue

            candidatos = item["candidatos"]
            assert isinstance(candidatos, list)
            candidatos.append({"area": area, "origen": origen})

        # Duplicados: filas válidas adicionales del mismo usuario.
        for item in vistos.values():
            filas = int(item["filas"])
            if filas > 1:
                resultado.duplicados_detectados += filas - 1

        df = self._df.copy()
        indices = {
            normalizar_usuario(usuario): indice
            for indice, usuario in enumerate(df[COLUMNA_USUARIO].tolist())
        }

        nuevas: list[dict[str, str]] = []
        for clave, item in vistos.items():
            candidatos = item["candidatos"]
            assert isinstance(candidatos, list)
            if not candidatos:
                # Solo asignaciones automáticas, celdas antiguas o vacías.
                continue

            # Conflictos entre correcciones manuales del mismo usuario.
            for anterior, actual in zip(candidatos, candidatos[1:]):
                if normalizar_area(anterior["area"]) != normalizar_area(
                    actual["area"]
                ):
                    resultado.conflictos_detectados += 1
                    resultado.detalles_conflictos.append(
                        f"{item['usuario']}: '{anterior['area']}' -> "
                        f"'{actual['area']}' (se aplica la última área válida)"
                    )

            elegido = candidatos[-1]
            area = str(elegido["area"])
            origen = str(elegido["origen"])
            area_actual = self._mapa.get(clave)
            usuario_visible = str(item["usuario"])

            if area_actual is None:
                nuevas.append(
                    {COLUMNA_USUARIO: usuario_visible, COLUMNA_AREA: area}
                )
                resultado.usuarios_nuevos += 1
                resultado.detalles_nuevos.append(
                    f"{usuario_visible} -> {area} ({origen})"
                )
                resultado.cambios.append(
                    {
                        "USUARIO": usuario_visible,
                        "AREA_ANTERIOR": "",
                        "AREA_NUEVA": area,
                        "ORIGEN": origen,
                    }
                )
                resultado.correcciones_manuales += 1
            elif normalizar_area(area_actual) == normalizar_area(area):
                resultado.sin_cambios += 1
            else:
                indice = indices[clave]
                df.at[indice, COLUMNA_AREA] = area
                resultado.correspondencias_actualizadas += 1
                resultado.detalles_actualizados.append(
                    f"{usuario_visible}: '{area_actual}' -> '{area}' ({origen})"
                )
                resultado.cambios.append(
                    {
                        "USUARIO": usuario_visible,
                        "AREA_ANTERIOR": str(area_actual),
                        "AREA_NUEVA": area,
                        "ORIGEN": origen,
                    }
                )
                resultado.correcciones_manuales += 1

        if nuevas:
            df = pd.concat(
                [df, pd.DataFrame(nuevas, columns=list(self.COLUMNAS))],
                ignore_index=True,
            )

        self._df = self._preparar(df)
        self._reconstruir_indice()
        return resultado

    # ------------------------------------------------------------------
    # Validación
    # ------------------------------------------------------------------
    def validar(self) -> list[str]:
        """Devuelve una lista de problemas de integridad (vacía si todo está bien)."""

        problemas: list[str] = []

        if list(self._df.columns) != list(self.COLUMNAS):
            problemas.append(
                f"Columnas incorrectas: {list(self._df.columns)}. "
                f"Se esperaba {list(self.COLUMNAS)}."
            )
            return problemas

        claves = [normalizar_usuario(u) for u in self._df[COLUMNA_USUARIO].tolist()]
        if any(clave is None for clave in claves):
            problemas.append("Existen usuarios sin identificador válido.")
        if len(claves) != len(set(claves)):
            problemas.append("Existen usuarios duplicados en la base maestra.")
        if not self._df[COLUMNA_AREA].map(es_area_valida).all():
            problemas.append("Existen áreas vacías o inválidas en la base maestra.")

        return problemas
