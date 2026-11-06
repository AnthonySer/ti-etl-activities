"""Utilidades de archivos: localización, lectura, validación y escritura segura."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd

from config import configuracion as cfg

LOGGER = logging.getLogger("automatizador")

COLUMNA_USUARIO = cfg.COLUMNA_USUARIO
COLUMNA_AREA = cfg.COLUMNA_AREA
COLUMNAS_OBLIGATORIAS = (COLUMNA_USUARIO, COLUMNA_AREA)


class ErrorArchivo(Exception):
    """Error controlado de lectura/escritura de archivos."""


class ErrorEstructura(ErrorArchivo):
    """El archivo no tiene la estructura mínima esperada."""


# ---------------------------------------------------------------------------
# Columnas
# ---------------------------------------------------------------------------
def clave_columna(nombre: object) -> str:
    """Clave tolerante para comparar encabezados (tildes, espacios, mayúsculas)."""

    texto = unicodedata.normalize("NFKD", "" if nombre is None else str(nombre))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^A-Z0-9]+", "_", texto.strip().upper()).strip("_")
    return texto


def clave_esperada(nombre: object) -> str:
    """Normaliza alias de usuario y área hacia sus nombres canónicos."""

    clave = clave_columna(nombre)
    if clave in {"AREA", "AREA_USUARIO"}:
        return COLUMNA_AREA
    if clave in {"USUARIO", "SOLICITANTE"}:
        return COLUMNA_USUARIO
    return clave


def normalizar_columnas(df: pd.DataFrame) -> pd.DataFrame:
    """Quita espacios de los nombres de columnas sin tocar los datos."""

    df = df.copy()
    df.columns = ["" if c is None else str(c).strip() for c in df.columns]
    return df


def _renombrar_primera(
    df: pd.DataFrame,
    canonica: str,
    alias: Sequence[str],
) -> pd.DataFrame:
    """Renombra la primera columna que coincida, respetando el orden de alias."""

    if canonica in df.columns:
        return df

    for clave_alias in alias:
        for columna in df.columns:
            if clave_columna(columna) == clave_alias:
                return df.rename(columns={columna: canonica})
    return df


def mapear_columnas(df: pd.DataFrame) -> pd.DataFrame:
    """Renombra USUARIO/SOLICITANTE y AREA/ÁREA a sus nombres canónicos.

    Se conservan todas las demás columnas tal como vienen en el archivo.
    Cuando existe más de un alias, tiene prioridad el nombre canónico:
    ``USUARIO`` sobre ``SOLICITANTE`` y ``AREA_USUARIO`` sobre ``AREA``.
    """

    df = normalizar_columnas(df)
    df = _renombrar_primera(df, COLUMNA_USUARIO, cfg.ALIAS_USUARIO)
    df = _renombrar_primera(df, COLUMNA_AREA, cfg.ALIAS_AREA)
    return df


def validar_columnas(
    df: pd.DataFrame,
    requeridas: Sequence[str] = COLUMNAS_OBLIGATORIAS,
) -> None:
    """Lanza ErrorEstructura si falta alguna columna obligatoria."""

    faltantes = [c for c in requeridas if c not in df.columns]
    if faltantes:
        raise ErrorEstructura(
            "Faltan columnas obligatorias: "
            + ", ".join(faltantes)
            + ". Columnas encontradas: "
            + ", ".join(map(str, df.columns))
        )


def _valores_comparables(df: pd.DataFrame) -> pd.DataFrame:
    """Convierte todo a texto para comparar sin depender del dtype de pandas."""

    return df.apply(
        lambda columna: columna.map(
            lambda valor: "" if pd.isna(valor) else str(valor)
        )
    )


def comparar_dataframes(
    izquierda: pd.DataFrame,
    derecha: pd.DataFrame,
) -> list[str]:
    """Devuelve una lista de diferencias entre dos dataframes (vacía si iguales)."""

    problemas: list[str] = []

    columnas_izq = [str(c).strip() for c in izquierda.columns]
    columnas_der = [str(c).strip() for c in derecha.columns]
    if columnas_izq != columnas_der:
        problemas.append(
            f"Columnas distintas: {columnas_izq} vs {columnas_der}."
        )
        return problemas

    if len(izquierda) != len(derecha):
        problemas.append(
            f"Cantidad de filas distinta: {len(izquierda)} vs {len(derecha)}."
        )
        return problemas

    izq = _valores_comparables(izquierda.reset_index(drop=True))
    der = _valores_comparables(derecha.reset_index(drop=True))

    if not izq.equals(der):
        diferencias = izq.ne(der)
        filas = int(diferencias.any(axis=1).sum())
        columnas = [
            str(columna)
            for columna in diferencias.columns
            if bool(diferencias[columna].any())
        ]
        problemas.append(
            f"Contenido distinto en {filas} fila(s); columnas afectadas: "
            f"{', '.join(columnas)}."
        )

    return problemas


# ---------------------------------------------------------------------------
# Localización de archivos
# ---------------------------------------------------------------------------
def localizar_archivo(
    nombre: str,
    raices: Iterable[Path] | None = None,
) -> Path | None:
    """Busca un archivo por nombre (sin distinguir mayúsculas) en las raíces dadas."""

    raices = list(raices or cfg.RUTAS_BUSQUEDA)
    objetivo = nombre.lower()

    # Búsqueda directa (rápida) en cada raíz.
    for raiz in raices:
        raiz = Path(raiz)
        if not raiz.exists():
            continue

        directo = raiz / nombre
        if directo.is_file():
            return directo

        try:
            for hijo in raiz.iterdir():
                if hijo.is_file() and hijo.name.lower() == objetivo:
                    return hijo
        except OSError:
            continue

    # Búsqueda recursiva como último recurso.
    vistas: set[Path] = set()
    for raiz in raices:
        raiz = Path(raiz)
        if not raiz.exists() or raiz in vistas:
            continue
        vistas.add(raiz)
        try:
            for candidato in raiz.rglob("*"):
                if candidato.is_file() and candidato.name.lower() == objetivo:
                    return candidato
        except OSError:
            continue

    return None


# ---------------------------------------------------------------------------
# Lectura tolerante a bloqueos de Excel/OneDrive
# ---------------------------------------------------------------------------
def _copia_temporal(ruta: Path) -> tuple[Path, Path]:
    """Copia el archivo a una carpeta temporal y devuelve (copia, carpeta)."""

    carpeta = Path(tempfile.mkdtemp(prefix="automatizador_"))
    destino = carpeta / ruta.name
    try:
        shutil.copy2(ruta, destino)
    except Exception:
        shutil.rmtree(carpeta, ignore_errors=True)
        raise
    return destino, carpeta


def _es_bloqueo(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    return getattr(exc, "winerror", None) in (32, 33)


def _leer_excel(ruta: Path, **kwargs) -> pd.DataFrame:
    """Lee un Excel con openpyxl; si está bloqueado, usa una copia temporal."""

    try:
        return pd.read_excel(ruta, engine="openpyxl", **kwargs)
    except OSError as exc:
        if not _es_bloqueo(exc):
            raise
        LOGGER.warning("El archivo %s está bloqueado; se usará una copia temporal.", ruta)
        copia, carpeta = _copia_temporal(ruta)
        try:
            return pd.read_excel(copia, engine="openpyxl", **kwargs)
        finally:
            shutil.rmtree(carpeta, ignore_errors=True)


def leer_excel(ruta: Path | str, **kwargs) -> pd.DataFrame:
    """Lectura pública de Excel tolerante a archivos bloqueados."""

    return _leer_excel(Path(ruta), **kwargs)


def listar_hojas(ruta: Path) -> list[str]:
    """Devuelve las hojas del libro, tolerando archivos bloqueados."""

    try:
        with pd.ExcelFile(ruta, engine="openpyxl") as libro:
            return list(libro.sheet_names)
    except OSError as exc:
        if not _es_bloqueo(exc):
            raise
        copia, carpeta = _copia_temporal(ruta)
        try:
            with pd.ExcelFile(copia, engine="openpyxl") as libro:
                return list(libro.sheet_names)
        finally:
            shutil.rmtree(carpeta, ignore_errors=True)


def analizar_encabezado(
    ruta: Path,
    hoja: str,
    requeridas: Sequence[str] = COLUMNAS_OBLIGATORIAS,
) -> tuple[int | None, int]:
    """Devuelve (índice del encabezado, cantidad de columnas con dato)."""

    vista = _leer_excel(
        ruta,
        sheet_name=hoja,
        header=None,
        nrows=cfg.FILA_ENCABEZADO_MAX,
    )
    requeridas_clave = {clave_esperada(c) for c in requeridas}

    for indice in range(len(vista)):
        claves: set[str] = set()
        columnas = 0
        for valor in vista.iloc[indice].tolist():
            try:
                if pd.isna(valor):
                    continue
            except (TypeError, ValueError):
                pass
            columnas += 1
            claves.add(clave_esperada(valor))
        if requeridas_clave.issubset(claves):
            return indice, columnas

    return None, 0


def detectar_encabezado(
    ruta: Path,
    hoja: str,
    requeridas: Sequence[str] = COLUMNAS_OBLIGATORIAS,
) -> int | None:
    """Devuelve el índice (base 0) de la fila de encabezado, o None."""

    indice, _ = analizar_encabezado(ruta, hoja, requeridas)
    return indice


def seleccionar_hoja(
    ruta: Path,
    requeridas: Sequence[str] = COLUMNAS_OBLIGATORIAS,
    hoja: str | None = None,
) -> str:
    """Selecciona la hoja de datos principal de forma determinista."""

    hojas = listar_hojas(ruta)
    if not hojas:
        raise ErrorEstructura(f"El archivo '{ruta.name}' no contiene hojas.")

    if hoja:
        for nombre in hojas:
            if nombre == hoja:
                return nombre
        for nombre in hojas:
            if nombre.lower() == hoja.lower():
                return nombre
        raise ErrorEstructura(
            f"La hoja '{hoja}' no existe en '{ruta.name}'. Hojas: {', '.join(hojas)}"
        )

    candidatas: list[tuple[int, int, str, bool]] = []
    for nombre in hojas:
        indice, columnas = analizar_encabezado(ruta, nombre, requeridas)
        if indice is None:
            continue

        stem = ruta.stem.lower()
        nombre_lower = nombre.lower()
        fuerte = False
        puntaje = columnas * 10

        if nombre_lower == stem:
            puntaje += 10_000
            fuerte = True
        elif stem in nombre_lower or nombre_lower in stem:
            puntaje += 5_000
            fuerte = True
        elif nombre_lower.startswith("consulta"):
            puntaje += 1_000
            fuerte = True

        # Las hojas auxiliares no deben ganarle a la hoja principal de datos.
        if clave_columna(nombre) in {
            "DETALLE",
            "RESUMEN",
            "AREA",
            "CONSULTAR",
            "AUXILIAR",
            "HOJA_AUXILIAR",
        }:
            puntaje -= 200

        candidatas.append((puntaje, -indice, nombre, fuerte))

    if not candidatas:
        con_usuario = [
            n for n in hojas if detectar_encabezado(ruta, n, (COLUMNA_USUARIO,)) is not None
        ]
        detalle = (
            " Hojas con USUARIO pero sin AREA_USUARIO/ÁREA: " + ", ".join(con_usuario)
            if con_usuario
            else ""
        )
        raise ErrorEstructura(
            f"El archivo '{ruta.name}' no contiene una hoja con las columnas "
            f"USUARIO y AREA_USUARIO. Hojas disponibles: {', '.join(hojas)}.{detalle}"
        )

    candidatas.sort(key=lambda item: (item[0], item[1]), reverse=True)
    fuertes = [c for c in candidatas if c[3]]

    # Si no hay una hoja claramente principal y hay varias candidatas, no se adivina.
    if not fuertes and len(candidatas) > 1:
        raise ErrorEstructura(
            f"El archivo '{ruta.name}' tiene varias hojas con USUARIO y "
            f"AREA_USUARIO ({', '.join(c[2] for c in candidatas)}). "
            "Indique la hoja con --hoja."
        )

    elegida = max(fuertes, key=lambda item: (item[0], item[1])) if fuertes else candidatas[0]
    return elegida[2]


def leer_hoja(
    ruta: Path,
    hoja: str | None = None,
    requeridas: Sequence[str] = COLUMNAS_OBLIGATORIAS,
) -> tuple[pd.DataFrame, str, int]:
    """Lee la hoja de datos y devuelve (dataframe, hoja, índice_encabezado)."""

    ruta = Path(ruta)
    if not ruta.exists():
        raise ErrorArchivo(f"No existe el archivo: {ruta}")

    hoja_usada = seleccionar_hoja(ruta, requeridas=requeridas, hoja=hoja)
    indice = detectar_encabezado(ruta, hoja_usada, requeridas=requeridas)
    if indice is None:
        raise ErrorEstructura(
            f"No se encontró el encabezado en la hoja '{hoja_usada}' de '{ruta.name}'."
        )

    df = _leer_excel(ruta, sheet_name=hoja_usada, header=indice)
    df = mapear_columnas(df)
    validar_columnas(df, requeridas)
    return df, hoja_usada, indice


# ---------------------------------------------------------------------------
# Escritura segura
# ---------------------------------------------------------------------------
def respaldar_archivo(ruta: Path, carpeta_backup: Path | None = None) -> Path | None:
    """Copia un archivo a data/backups con marca de tiempo."""

    ruta = Path(ruta)
    if not ruta.exists():
        return None

    carpeta_backup = Path(carpeta_backup or cfg.BACKUPS_DIR)
    carpeta_backup.mkdir(parents=True, exist_ok=True)

    marca = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    destino = carpeta_backup / f"{ruta.stem}_{marca}{ruta.suffix}"
    try:
        shutil.copy2(ruta, destino)
    except OSError as exc:
        raise ErrorArchivo(
            f"No se pudo crear la copia de seguridad de '{ruta}': {exc}"
        ) from exc

    LOGGER.info("Copia de seguridad creada: %s", destino)
    return destino


def escribir_dataframe_atomico(
    df: pd.DataFrame,
    ruta: Path,
    hoja: str = "MAESTRO",
) -> Path:
    """Escribe .xlsx o .csv mediante archivo temporal y reemplazo controlado."""

    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_name(f".{ruta.stem}.{os.getpid()}.tmp{ruta.suffix}")

    try:
        if ruta.suffix.lower() == ".csv":
            df.to_csv(temporal, index=False, encoding="utf-8-sig")
            pd.read_csv(temporal, encoding="utf-8-sig")
        else:
            df.to_excel(temporal, index=False, sheet_name=hoja, engine="openpyxl")
            pd.read_excel(temporal, engine="openpyxl")

        os.replace(temporal, ruta)
    except PermissionError as exc:
        raise ErrorArchivo(
            f"No se pudo escribir '{ruta}'. "
            "Verifique que el archivo no esté abierto en Excel."
        ) from exc
    except OSError as exc:
        if getattr(exc, "winerror", None) in (32, 33):
            raise ErrorArchivo(
                f"No se pudo escribir '{ruta}'. "
                "Verifique que el archivo no esté abierto en Excel."
            ) from exc
        raise
    finally:
        try:
            temporal.unlink(missing_ok=True)
        except OSError:
            pass

    return ruta


def escribir_json_atomico(datos: object, ruta: Path) -> Path:
    """Escribe JSON UTF-8 mediante archivo temporal y reemplazo atómico."""

    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_name(f".{ruta.stem}.{os.getpid()}.tmp{ruta.suffix}")

    try:
        with temporal.open("w", encoding="utf-8", newline="\n") as archivo:
            json.dump(datos, archivo, ensure_ascii=False, indent=2)
            archivo.write("\n")
        with temporal.open("r", encoding="utf-8") as archivo:
            json.load(archivo)
        os.replace(temporal, ruta)
    except PermissionError as exc:
        raise ErrorArchivo(
            f"No se pudo escribir '{ruta}'. "
            "Verifique que el archivo no esté abierto en Excel."
        ) from exc
    except OSError as exc:
        if getattr(exc, "winerror", None) in (32, 33):
            raise ErrorArchivo(
                f"No se pudo escribir '{ruta}'. "
                "Verifique que el archivo no esté abierto en Excel."
            ) from exc
        raise
    finally:
        try:
            temporal.unlink(missing_ok=True)
        except OSError:
            pass

    return ruta


def copiar_archivo(origen: Path, destino: Path) -> Path:
    """Copia un archivo preservando metadatos, con mensaje de error claro."""

    origen = Path(origen)
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)

    try:
        shutil.copy2(origen, destino)
    except OSError as exc:
        raise ErrorArchivo(
            f"No se pudo copiar '{origen}' hacia '{destino}': {exc}"
        ) from exc
    return destino
