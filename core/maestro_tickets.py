"""Excel maestro histórico de tickets (11 columnas) y su capa de control.

El maestro conserva el histórico completo y nunca se reemplaza por un archivo
nuevo. Cada fila tiene un identificador técnico que vive únicamente en la hoja
auxiliar ``CONTROL``; el detalle visible mantiene exactamente las 11 columnas
oficiales.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

import pandas as pd
from openpyxl import Workbook, load_workbook

from config import configuracion as cfg
from core.archivos import (
    ErrorArchivo,
    ErrorEstructura,
    leer_excel,
    respaldar_archivo,
)
from core.esquema import (
    C_AREA,
    C_FECHA,
    C_HORAS,
    C_SOLICITANTE,
    COLUMNAS_OFICIALES,
    diferencia_campos,
    fila_a_dict,
    huella_fila,
    serializar_valor,
    validar_estructura_exacta,
    valor_canonico,
)
from core.etl import parsear_fecha, parsear_horas, texto_limpio
from core.normalizador import limpiar_area, limpiar_usuario

LOGGER = logging.getLogger("automatizador")

CONTROL_COLUMNAS = (
    "ID_TECNICO",
    "HUELLA",
    "ORIGEN",
    "EJECUCION_ALTA",
    "FECHA_ALTA",
    "EJECUCION_ACTUALIZACION",
    "FECHA_ACTUALIZACION",
    "ESTADO",
)


@dataclass
class ResultadoAplicacion:
    nuevos: int = 0
    actualizados: int = 0
    sin_cambios: int = 0
    duplicados: int = 0
    omitidos: int = 0
    cambios_historial: list[dict[str, str]] = field(default_factory=list)
    total: int = 0


@dataclass
class ResultadoGuardadoTickets:
    ruta_xlsx: Path | None
    ruta_csv: Path | None
    respaldos: list[Path] = field(default_factory=list)


def _ahora() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _normalizar_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza tipos y valores sin cambiar el contenido de negocio."""

    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    validar_estructura_exacta(df.columns, contexto="maestro de tickets")

    datos: dict[str, list[Any]] = {}
    for columna in COLUMNAS_OFICIALES:
        if columna == C_FECHA:
            datos[columna] = [parsear_fecha(valor)[0] for valor in df[columna]]
        elif columna == C_HORAS:
            datos[columna] = [parsear_horas(valor)[0] for valor in df[columna]]
        elif columna == C_SOLICITANTE:
            datos[columna] = [limpiar_usuario(valor) for valor in df[columna]]
        elif columna == C_AREA:
            datos[columna] = [limpiar_area(valor) for valor in df[columna]]
        else:
            datos[columna] = [texto_limpio(valor) for valor in df[columna]]

    return pd.DataFrame(datos, columns=list(COLUMNAS_OFICIALES))


def _canonico_df(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            columna: [valor_canonico(columna, valor) for valor in df[columna]]
            for columna in COLUMNAS_OFICIALES
        }
    )


def _control_vacio() -> pd.DataFrame:
    return pd.DataFrame(columns=list(CONTROL_COLUMNAS))


def _control_sintetico(df: pd.DataFrame) -> pd.DataFrame:
    """Genera control para un maestro heredado sin hoja CONTROL."""

    fecha = _ahora()
    filas = []
    for _, fila in df.iterrows():
        filas.append(
            {
                "ID_TECNICO": uuid.uuid4().hex,
                "HUELLA": huella_fila(fila),
                "ORIGEN": "MIGRADO",
                "EJECUCION_ALTA": "",
                "FECHA_ALTA": fecha,
                "EJECUCION_ACTUALIZACION": "",
                "FECHA_ACTUALIZACION": "",
                "ESTADO": "ACTIVO",
            }
        )
    return pd.DataFrame(filas, columns=list(CONTROL_COLUMNAS))


class MaestroTickets:
    """Maestro histórico con detalle oficial + control técnico."""

    def __init__(
        self,
        df: pd.DataFrame | None = None,
        control: pd.DataFrame | None = None,
    ):
        if df is None:
            df = pd.DataFrame(columns=list(COLUMNAS_OFICIALES))
        self._df = _normalizar_df(df) if len(df) else pd.DataFrame(
            columns=list(COLUMNAS_OFICIALES)
        )

        if control is None:
            self._control = _control_sintetico(self._df)
        else:
            self._control = self._normalizar_control(control, len(self._df))

    # ------------------------------------------------------------------
    # Carga y validación
    # ------------------------------------------------------------------
    @staticmethod
    def _normalizar_control(control: pd.DataFrame, filas: int) -> pd.DataFrame:
        control = control.copy()
        control.columns = [str(c).strip() for c in control.columns]
        for columna in CONTROL_COLUMNAS:
            if columna not in control.columns:
                control[columna] = ""
        control = control.loc[:, list(CONTROL_COLUMNAS)].reset_index(drop=True)

        if len(control) < filas:
            raise ErrorEstructura(
                f"La hoja CONTROL tiene {len(control)} filas y el detalle {filas}. "
                "El maestro está incompleto."
            )
        if len(control) > filas:
            control = control.iloc[:filas].copy()

        for posicion in range(filas):
            if not str(control.at[posicion, "ID_TECNICO"]).strip():
                control.at[posicion, "ID_TECNICO"] = uuid.uuid4().hex
        return control

    @classmethod
    def cargar(
        cls,
        ruta_xlsx: Path | str | None = None,
        ruta_csv: Path | str | None = None,
        verificar_consistencia: bool = True,
    ) -> "MaestroTickets":
        ruta_xlsx = Path(ruta_xlsx or cfg.MAESTRO_TICKETS_XLSX)
        ruta_csv = Path(ruta_csv or ruta_xlsx.with_suffix(".csv"))

        df_xlsx: pd.DataFrame | None = None
        df_csv: pd.DataFrame | None = None
        control: pd.DataFrame | None = None

        if ruta_xlsx.exists():
            try:
                libro = load_workbook(ruta_xlsx, read_only=True, data_only=True)
                try:
                    hojas = list(libro.sheetnames)
                finally:
                    libro.close()
                hoja = cfg.HOJA_MAESTRO if cfg.HOJA_MAESTRO in hojas else hojas[0]
                df_xlsx = leer_excel(ruta_xlsx, sheet_name=hoja)
                if cfg.HOJA_CONTROL in hojas:
                    control = leer_excel(
                        ruta_xlsx, sheet_name=cfg.HOJA_CONTROL
                    )
            except (OSError, ValueError) as exc:
                raise ErrorArchivo(
                    f"No se pudo leer el maestro de tickets '{ruta_xlsx}': {exc}"
                ) from exc

        if ruta_csv.exists():
            try:
                df_csv = pd.read_csv(ruta_csv, encoding="utf-8-sig")
            except OSError as exc:
                raise ErrorArchivo(
                    f"No se pudo leer el espejo CSV '{ruta_csv}': {exc}"
                ) from exc

        if (
            df_xlsx is not None
            and df_csv is not None
            and verificar_consistencia
        ):
            normalizado_xlsx = _normalizar_df(df_xlsx)
            normalizado_csv = _normalizar_df(df_csv)
            if not _canonico_df(normalizado_xlsx).equals(
                _canonico_df(normalizado_csv)
            ):
                raise ErrorEstructura(
                    "Discrepancia entre el maestro de tickets Excel y su espejo "
                    "CSV. Corrija o reconstruya el maestro antes de continuar."
                )

        df = df_xlsx if df_xlsx is not None else df_csv
        if df is None:
            return cls()

        return cls(df, control)

    # ------------------------------------------------------------------
    # Inspección previa de hojas (error 'Worksheet named Tickets not found')
    # ------------------------------------------------------------------
    @staticmethod
    def inspeccionar_hojas(
        ruta_xlsx: Path | str,
        hoja_esperada: str | None = None,
    ) -> dict[str, Any]:
        """Informa qué archivo se leyó, sus hojas y la hoja esperada.

        No crea hojas ni oculta el problema: devuelve un diagnóstico que la
        interfaz puede mostrar cuando la auditoría de solicitantes no puede
        ejecutarse.
        """

        ruta = Path(ruta_xlsx)
        esperada = str(hoja_esperada or cfg.HOJA_DETALLE)
        resultado: dict[str, Any] = {
            "ruta": str(ruta),
            "archivo": ruta.name,
            "existe": ruta.is_file(),
            "hojas": [],
            "hoja_esperada": esperada,
            "hoja_encontrada": "",
            "problema": "",
        }
        if not ruta.is_file():
            resultado["problema"] = f"No existe el archivo '{ruta}'."
            return resultado
        try:
            libro = load_workbook(ruta, read_only=True, data_only=True)
            try:
                resultado["hojas"] = list(libro.sheetnames)
            finally:
                libro.close()
        except (OSError, ValueError) as exc:
            resultado["problema"] = (
                f"No se pudo abrir '{ruta.name}': {exc}"
            )
            return resultado

        for nombre in resultado["hojas"]:
            if str(nombre).strip().casefold() == esperada.strip().casefold():
                resultado["hoja_encontrada"] = str(nombre)
                break
        if not resultado["hoja_encontrada"]:
            resultado["problema"] = (
                f"El archivo '{ruta.name}' no contiene la hoja '{esperada}'. "
                f"Hojas encontradas: {', '.join(resultado['hojas']) or 'ninguna'}."
            )
        return resultado

    @classmethod
    def leer_hoja_detalle(
        cls,
        ruta_xlsx: Path | str,
        hoja_esperada: str | None = None,
    ) -> tuple[pd.DataFrame, str, str]:
        """Lee la hoja de detalle con un diagnóstico comprensible.

        Si la hoja esperada no existe pero otra hoja contiene la columna
        obligatoria ``USUARIO``, se usa esa hoja y se devuelve una advertencia.
        Si ninguna sirve, se lanza ``ErrorEstructura`` con archivo, hojas y
        hoja esperada. Nunca se crea una hoja vacía.
        """

        ruta = Path(ruta_xlsx)
        diagnostico = cls.inspeccionar_hojas(ruta, hoja_esperada)
        if not diagnostico["existe"] or "No se pudo abrir" in diagnostico["problema"]:
            raise ErrorEstructura(diagnostico["problema"] or f"No existe '{ruta}'.")

        encontrada = str(diagnostico["hoja_encontrada"])
        if encontrada:
            try:
                df = leer_excel(ruta, sheet_name=encontrada)
            except (OSError, ValueError) as exc:
                raise ErrorArchivo(
                    f"No se pudo leer la hoja '{encontrada}' de "
                    f"'{ruta.name}': {exc}"
                ) from exc
            if C_SOLICITANTE in df.columns:
                return df, encontrada, ""

        # La hoja esperada no existe, no se pudo leer o no tiene la columna
        # obligatoria. Se busca una alternativa segura antes de fallar.
        for nombre in diagnostico["hojas"]:
            if nombre == encontrada:
                continue
            try:
                alternativa = leer_excel(ruta, sheet_name=nombre)
            except (OSError, ValueError):
                continue
            if C_SOLICITANTE in alternativa.columns:
                if encontrada:
                    aviso = (
                        f"La hoja '{encontrada}' no contiene la columna "
                        f"'{C_SOLICITANTE}'; se auditó la hoja '{nombre}'."
                    )
                else:
                    aviso = (
                        f"La hoja esperada '{diagnostico['hoja_esperada']}' no "
                        f"existe; se auditó la hoja '{nombre}' por contener la "
                        f"columna '{C_SOLICITANTE}'."
                    )
                return alternativa, str(nombre), aviso
        raise ErrorEstructura(
            f"El archivo '{ruta.name}' no tiene una hoja utilizable para "
            f"solicitantes. Hoja esperada: '{diagnostico['hoja_esperada']}'. "
            f"Hojas encontradas: {', '.join(diagnostico['hojas']) or 'ninguna'}. "
            f"Se requiere la columna '{C_SOLICITANTE}'."
        )

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------
    def a_dataframe(self) -> pd.DataFrame:
        return self._df.copy()

    def control(self) -> pd.DataFrame:
        return self._control.copy()

    def __len__(self) -> int:
        return len(self._df)

    @property
    def ids(self) -> list[str]:
        return self._control["ID_TECNICO"].astype(str).tolist()

    def estado(self) -> dict[str, Any]:
        fechas = [
            valor
            for valor in self._df[C_FECHA].tolist()
            if isinstance(valor, (date, datetime))
        ]
        actualizaciones = [
            str(valor)
            for valor in self._control["FECHA_ACTUALIZACION"].tolist()
            if str(valor).strip()
        ]
        return {
            "registros": len(self._df),
            "ids_unicos": int(self._control["ID_TECNICO"].nunique()),
            "areas": int(self._df[C_AREA].dropna().nunique()),
            "responsables": int(self._df["Responsable"].dropna().nunique()),
            "periodo": (
                f"{min(fechas):%Y-%m-%d}/{max(fechas):%Y-%m-%d}"
                if fechas
                else ""
            ),
            "ultima_actualizacion": max(actualizaciones) if actualizaciones else "",
        }

    # ------------------------------------------------------------------
    # Aplicación de filas
    # ------------------------------------------------------------------
    def aplicar(
        self,
        filas: pd.DataFrame,
        *,
        ids: Sequence[str | None] | None = None,
        id_ejecucion: str,
        tipo_operacion: str,
        archivo_fuente: str = "",
        permitir_duplicados: bool = False,
    ) -> ResultadoAplicacion:
        """Inserta o actualiza filas sin eliminar históricos ni duplicar."""

        filas = _normalizar_df(filas)
        validar_estructura_exacta(filas.columns, contexto="filas a consolidar")

        if ids is not None and len(ids) != len(filas):
            raise ErrorArchivo(
                "La cantidad de identificadores no coincide con las filas a aplicar."
            )

        ids = list(ids) if ids is not None else [None] * len(filas)

        resultado = ResultadoAplicacion(total=len(filas))
        indice_por_id = {
            str(id_tecnico): posicion
            for posicion, id_tecnico in enumerate(
                self._control["ID_TECNICO"].astype(str).tolist()
            )
        }
        huellas_existentes = set(
            self._control["HUELLA"].astype(str).tolist()
        )
        ahora = _ahora()

        nuevas_filas: list[dict[str, Any]] = []
        nuevas_control: list[dict[str, Any]] = []
        ids_actualizados: set[str] = set()

        for posicion, (_, fila) in enumerate(filas.iterrows()):
            id_tecnico = ids[posicion]
            id_tecnico = str(id_tecnico) if id_tecnico else None
            datos = fila_a_dict(fila)
            huella = huella_fila(datos)

            if id_tecnico and id_tecnico in indice_por_id:
                indice = indice_por_id[id_tecnico]
                antes = self._df.iloc[indice].to_dict()
                cambios = diferencia_campos(antes, datos)

                if not cambios:
                    resultado.sin_cambios += 1
                    self._control.at[indice, "HUELLA"] = huella
                    self._control.at[indice, "ESTADO"] = "ACTIVO"
                    continue

                for columna in COLUMNAS_OFICIALES:
                    self._df.at[indice, columna] = datos[columna]
                self._control.at[indice, "HUELLA"] = huella
                self._control.at[indice, "EJECUCION_ACTUALIZACION"] = id_ejecucion
                self._control.at[indice, "FECHA_ACTUALIZACION"] = ahora
                self._control.at[indice, "ESTADO"] = "ACTIVO"
                ids_actualizados.add(id_tecnico)
                resultado.actualizados += 1

                for campo, (anterior, nuevo) in cambios.items():
                    resultado.cambios_historial.append(
                        {
                            "ID_TECNICO": id_tecnico,
                            "CAMPO": campo,
                            "VALOR_ANTERIOR": anterior,
                            "VALOR_NUEVO": nuevo,
                        }
                    )
                continue

            # Fila nueva.
            if (
                huella in huellas_existentes
                and id_tecnico is None
                and not permitir_duplicados
            ):
                resultado.duplicados += 1
                continue

            nuevo_id = id_tecnico or uuid.uuid4().hex
            if nuevo_id in indice_por_id:
                # El id existe pero no se encontró antes: no debería ocurrir.
                resultado.omitidos += 1
                continue

            nuevas_filas.append(datos)
            nuevas_control.append(
                {
                    "ID_TECNICO": nuevo_id,
                    "HUELLA": huella,
                    "ORIGEN": archivo_fuente or tipo_operacion,
                    "EJECUCION_ALTA": id_ejecucion,
                    "FECHA_ALTA": ahora,
                    "EJECUCION_ACTUALIZACION": id_ejecucion,
                    "FECHA_ACTUALIZACION": ahora,
                    "ESTADO": "ACTIVO",
                }
            )
            huellas_existentes.add(huella)
            resultado.nuevos += 1

        if nuevas_filas:
            self._df = pd.concat(
                [
                    self._df,
                    pd.DataFrame(nuevas_filas, columns=list(COLUMNAS_OFICIALES)),
                ],
                ignore_index=True,
            )
            self._control = pd.concat(
                [
                    self._control,
                    pd.DataFrame(nuevas_control, columns=list(CONTROL_COLUMNAS)),
                ],
                ignore_index=True,
            )
            self._df = self._df.reset_index(drop=True)
            self._control = self._control.reset_index(drop=True)

        if len(self._df):
            self._df = _normalizar_df(self._df)

        return resultado

    # ------------------------------------------------------------------
    # Guardado transaccional
    # ------------------------------------------------------------------
    def validar(self) -> list[str]:
        problemas: list[str] = []

        if list(self._df.columns) != list(COLUMNAS_OFICIALES):
            return [
                f"Columnas del detalle incorrectas: {list(self._df.columns)}."
            ]

        if len(self._df) != len(self._control):
            problemas.append(
                f"Detalle ({len(self._df)}) y control ({len(self._control)}) "
                "tienen distinta cantidad de filas."
            )
            return problemas

        ids = self._control["ID_TECNICO"].astype(str)
        if ids.duplicated().any():
            problemas.append("Existen identificadores técnicos duplicados.")
        if (ids.str.strip() == "").any():
            problemas.append("Existen identificadores técnicos vacíos.")

        huellas = [
            huella_fila(fila)
            for _, fila in self._df.iterrows()
        ]
        registradas = self._control["HUELLA"].astype(str).tolist()
        if huellas != registradas:
            problemas.append(
                "La huella de control no coincide con el contenido del detalle. "
                "El maestro fue editado fuera del sistema."
            )

        return problemas

    def _escribir_temporal(self, destino: Path) -> Path:
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
                tabla = self._df.copy()
                tabla[C_FECHA] = [
                    valor.strftime("%Y-%m-%d")
                    if isinstance(valor, (date, datetime))
                    else ("" if valor is None else str(valor))
                    for valor in tabla[C_FECHA]
                ]
                tabla.to_csv(temporal, index=False, encoding="utf-8-sig")
            else:
                libro = Workbook()
                hoja = libro.active
                hoja.title = cfg.HOJA_MAESTRO
                hoja.append(list(COLUMNAS_OFICIALES))
                for _, fila in self._df.iterrows():
                    hoja.append([fila[columna] for columna in COLUMNAS_OFICIALES])

                control = libro.create_sheet(cfg.HOJA_CONTROL)
                control.append(list(CONTROL_COLUMNAS))
                for _, fila in self._control.iterrows():
                    control.append(
                        [serializar_valor(fila[columna]) for columna in CONTROL_COLUMNAS]
                    )

                libro.save(temporal)
                libro.close()
        except Exception:
            temporal.unlink(missing_ok=True)
            raise

        return temporal

    def _leer_y_comparar(self, temporal: Path) -> None:
        if temporal.suffix.lower() == ".csv":
            leido = pd.read_csv(temporal, encoding="utf-8-sig")
        else:
            leido = leer_excel(temporal, sheet_name=cfg.HOJA_MAESTRO)

        normalizado = _normalizar_df(leido)
        if not _canonico_df(normalizado).equals(_canonico_df(self._df)):
            raise ErrorEstructura(
                f"El archivo temporal '{temporal.name}' no coincide con el "
                "maestro en memoria."
            )

        if temporal.suffix.lower() != ".csv":
            control = leer_excel(temporal, sheet_name=cfg.HOJA_CONTROL)
            if len(control) != len(self._control):
                raise ErrorEstructura(
                    f"La hoja CONTROL temporal tiene {len(control)} filas y se "
                    f"esperaban {len(self._control)}."
                )

    def guardar(
        self,
        ruta_xlsx: Path | str | None = None,
        ruta_csv: Path | str | None = None,
        crear_backup: bool = True,
    ) -> ResultadoGuardadoTickets:
        """Guarda Excel + CSV de forma transaccional con rollback."""

        problemas = self.validar()
        if problemas:
            raise ErrorEstructura(
                "El maestro de tickets no superó la validación: "
                + " ".join(problemas)
            )

        ruta_xlsx = Path(ruta_xlsx or cfg.MAESTRO_TICKETS_XLSX)
        ruta_csv = (
            Path(ruta_csv or ruta_xlsx.with_suffix(".csv"))
            if cfg.ESCRIBIR_CSV_ESPEJO
            else None
        )
        objetivos = [ruta_xlsx] + ([ruta_csv] if ruta_csv else [])

        temporales: dict[Path, Path] = {}
        reemplazados: list[Path] = []
        respaldos: list[Path] = []
        carpeta_rollback = Path(tempfile.mkdtemp(prefix="automatizador_tickets_"))
        rollback: dict[Path, Path] = {}

        try:
            for destino in objetivos:
                temporal = self._escribir_temporal(destino)
                temporales[destino] = temporal
                self._leer_y_comparar(temporal)

            for destino in objetivos:
                if not destino.exists():
                    continue
                if crear_backup:
                    respaldo = respaldar_archivo(destino)
                    if respaldo is not None:
                        respaldos.append(respaldo)
                copia = carpeta_rollback / destino.name
                shutil.copy2(destino, copia)
                rollback[destino] = copia

            for destino in objetivos:
                os.replace(temporales[destino], destino)
                reemplazados.append(destino)

        except Exception as exc:
            for destino in reversed(reemplazados):
                try:
                    if destino in rollback:
                        shutil.copy2(rollback[destino], destino)
                    else:
                        destino.unlink(missing_ok=True)
                except OSError:
                    LOGGER.critical("No se pudo restaurar %s", destino)

            if isinstance(exc, (ErrorArchivo, ErrorEstructura)):
                raise
            if isinstance(exc, PermissionError) or getattr(
                exc, "winerror", None
            ) in (32, 33):
                raise ErrorArchivo(
                    "No se pudo guardar el maestro de tickets. Verifique que "
                    "los archivos no estén abiertos en Excel."
                ) from exc
            raise ErrorArchivo(
                f"No se pudo guardar el maestro de tickets: {exc}"
            ) from exc
        finally:
            for temporal in temporales.values():
                temporal.unlink(missing_ok=True)
            shutil.rmtree(carpeta_rollback, ignore_errors=True)

        return ResultadoGuardadoTickets(
            ruta_xlsx=ruta_xlsx,
            ruta_csv=ruta_csv,
            respaldos=respaldos,
        )

    # ------------------------------------------------------------------
    # Restauración
    # ------------------------------------------------------------------
    @classmethod
    def restaurar_backup(
        cls,
        ruta_backup: Path | str,
        ruta_destino: Path | str | None = None,
    ) -> ResultadoGuardadoTickets:
        ruta_backup = Path(ruta_backup)
        if not ruta_backup.is_file():
            raise ErrorArchivo(f"No existe la copia de seguridad: {ruta_backup}")

        restaurado = cls.cargar(ruta_backup, verificar_consistencia=False)
        return restaurado.guardar(ruta_destino or cfg.MAESTRO_TICKETS_XLSX, crear_backup=True)
