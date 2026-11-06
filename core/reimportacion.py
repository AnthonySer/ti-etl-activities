"""Reimportación segura de un Excel procesado y corregido manualmente.

Flujo:

1. Leer el archivo corregido exigiendo exactamente las 11 columnas oficiales.
2. Recuperar el manifiesto del procesamiento original (identificadores técnicos).
3. Comparar fila a fila y clasificar: sin cambios, modificada, nueva, duplicada,
   ambigua o ausente.
4. Devolver un resumen para confirmación. La aplicación de cambios se realiza
   con ``aplicar_reimportacion`` y nunca elimina registros ausentes.
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from config import configuracion as cfg
from core.archivos import ErrorArchivo, ErrorEstructura, leer_excel
from core.esquema import (
    C_ACTIVIDAD,
    C_AREA,
    C_ATENCION,
    C_DETALLE,
    C_FECHA,
    C_HORAS,
    C_PROYECTO,
    C_REQUERIMIENTO,
    C_RESPONSABLE,
    C_SOLICITANTE,
    C_SUBTIPO,
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

TIPO_NUEVO = "NUEVO"
TIPO_MODIFICADO = "MODIFICADO"
TIPO_SIN_CAMBIOS = "SIN_CAMBIOS"
TIPO_DUPLICADO = "DUPLICADO"
TIPO_AMBIGUO = "AMBIGUO"
TIPO_AUSENTE = "AUSENTE"
TIPO_ERROR = "ERROR"

_PESOS = {
    C_DETALLE: 0.40,
    C_FECHA: 0.16,
    C_SOLICITANTE: 0.16,
    C_HORAS: 0.06,
    C_REQUERIMIENTO: 0.05,
    C_RESPONSABLE: 0.05,
    C_AREA: 0.04,
    C_ATENCION: 0.03,
    C_SUBTIPO: 0.02,
    C_PROYECTO: 0.02,
    C_ACTIVIDAD: 0.01,
}


@dataclass
class FilaOriginal:
    id_tecnico: str
    fila: int
    huella: str
    datos: dict[str, Any]
    canonicos: dict[str, str]


@dataclass
class CambioReimportacion:
    tipo: str
    fila_corregida: int | None = None
    fila_original: int | None = None
    id_tecnico: str | None = None
    similitud: float = 0.0
    campos_cambiados: dict[str, tuple[str, str]] = field(default_factory=dict)
    antes: dict[str, Any] = field(default_factory=dict)
    despues: dict[str, Any] = field(default_factory=dict)
    observacion: str = ""
    es_duplicado: bool = False

    @property
    def aplicable(self) -> bool:
        return self.tipo in (TIPO_NUEVO, TIPO_MODIFICADO, TIPO_SIN_CAMBIOS)

    @property
    def campos(self) -> str:
        return ", ".join(self.campos_cambiados) if self.campos_cambiados else ""


@dataclass
class ResultadoReimportacion:
    ruta_corregido: Path
    ruta_manifiesto: Path
    df_corregido: pd.DataFrame
    cambios: list[CambioReimportacion]
    resumen: dict[str, int]
    periodo: str = ""
    id_ejecucion_original: str = ""

    @property
    def aplicables(self) -> list[CambioReimportacion]:
        return [cambio for cambio in self.cambios if cambio.aplicable]


# ---------------------------------------------------------------------------
# Manifiesto
# ---------------------------------------------------------------------------
def _candidatos_manifiesto(ruta_corregido: Path) -> list[Path]:
    candidatos: list[Path] = []

    def agregar(ruta: Path) -> None:
        if ruta not in candidatos:
            candidatos.append(ruta)

    agregar(ruta_corregido.with_name(f"{ruta_corregido.stem}{cfg.SUFIJO_MANIFIESTO}"))
    agregar(
        ruta_corregido.with_name(f"{ruta_corregido.name}{cfg.SUFIJO_MANIFIESTO}")
    )

    stem = ruta_corregido.stem
    base = re.sub(r"(_revisado|_corregido|_revision|_manual)+$", "", stem, flags=re.I)

    for carpeta in (ruta_corregido.parent, cfg.OUTPUT_DIR):
        if not carpeta.exists():
            continue
        for candidato in carpeta.glob(f"*{cfg.SUFIJO_MANIFIESTO}"):
            nombre = candidato.name[: -len(cfg.SUFIJO_MANIFIESTO)]
            if not nombre:
                continue
            if (
                base == nombre
                or base.startswith(nombre)
                or nombre.startswith(base)
            ):
                agregar(candidato)

    return [ruta for ruta in candidatos if ruta.is_file()]


def buscar_manifiesto_corregido(ruta_corregido: Path | str) -> Path:
    """Localiza el manifiesto del procesamiento que originó el archivo corregido."""

    ruta_corregido = Path(ruta_corregido)
    candidatos = _candidatos_manifiesto(ruta_corregido)

    if not candidatos:
        raise ErrorArchivo(
            f"No se encontró el manifiesto de control para '{ruta_corregido.name}'. "
            "La reimportación necesita el manifiesto generado al procesar el "
            "archivo original para identificar cada ticket sin duplicarlo. "
            "Vuelva a procesar el archivo y corrija la nueva salida."
        )

    if len(candidatos) > 1:
        # Preferir coincidencia exacta de nombre.
        exactos = [
            ruta
            for ruta in candidatos
            if ruta.name
            in {
                f"{ruta_corregido.stem}{cfg.SUFIJO_MANIFIESTO}",
                f"{ruta_corregido.name}{cfg.SUFIJO_MANIFIESTO}",
            }
        ]
        if len(exactos) == 1:
            return exactos[0]
        raise ErrorArchivo(
            "Hay varios manifiestos compatibles con el archivo corregido: "
            + ", ".join(ruta.name for ruta in candidatos)
            + ". Indique explícitamente el manifiesto correcto."
        )

    return candidatos[0]


def cargar_filas_manifiesto(ruta: Path | str) -> tuple[list[FilaOriginal], dict[str, Any]]:
    """Carga las filas de control del manifiesto v2."""

    import json

    ruta = Path(ruta)
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ErrorArchivo(f"No se pudo leer el manifiesto '{ruta}': {exc}") from exc

    filas_brutas = datos.get("filas")
    if not isinstance(filas_brutas, list) or not filas_brutas:
        raise ErrorArchivo(
            f"El manifiesto '{ruta.name}' no contiene las filas de control "
            "necesarias para reimportar (versión anterior). Vuelva a procesar "
            "el archivo original."
        )

    filas: list[FilaOriginal] = []
    for item in filas_brutas:
        id_tecnico = str(item.get("id_tecnico") or "")
        datos_fila = item.get("datos") or {}
        if not id_tecnico or not isinstance(datos_fila, dict):
            continue
        fila = FilaOriginal(
            id_tecnico=id_tecnico,
            fila=int(item.get("fila") or 0),
            huella=str(item.get("huella") or huella_fila(datos_fila)),
            datos=datos_fila,
            canonicos={
                columna: valor_canonico(columna, datos_fila.get(columna))
                for columna in COLUMNAS_OFICIALES
            },
        )
        filas.append(fila)

    if not filas:
        raise ErrorArchivo(
            f"El manifiesto '{ruta.name}' no contiene filas válidas."
        )
    return filas, datos


# ---------------------------------------------------------------------------
# Lectura del archivo corregido
# ---------------------------------------------------------------------------
def _normalizar_corregido(valor: Any, columna: str) -> Any:
    if columna == C_FECHA:
        return parsear_fecha(valor)[0]
    if columna == C_HORAS:
        return parsear_horas(valor)[0]
    if columna == C_SOLICITANTE:
        return limpiar_usuario(valor)
    if columna == C_AREA:
        return limpiar_area(valor)
    return texto_limpio(valor)


def leer_corregido(
    ruta: Path | str,
    hoja: str | None = None,
) -> tuple[pd.DataFrame, str]:
    """Lee un Excel corregido exigiendo exactamente el esquema oficial."""

    ruta = Path(ruta)
    if not ruta.exists():
        raise ErrorArchivo(f"No existe el archivo corregido: {ruta}")
    if ruta.suffix.lower() != ".xlsx":
        raise ErrorArchivo("El archivo corregido debe ser .xlsx.")

    from openpyxl import load_workbook

    libro = load_workbook(ruta, read_only=True)
    try:
        hojas = list(libro.sheetnames)
    finally:
        libro.close()

    if hoja:
        if hoja not in hojas:
            raise ErrorEstructura(
                f"La hoja '{hoja}' no existe en '{ruta.name}'. "
                f"Hojas: {', '.join(hojas)}"
            )
        hoja_elegida = hoja
    elif cfg.HOJA_DETALLE in hojas:
        hoja_elegida = cfg.HOJA_DETALLE
    else:
        hoja_elegida = hojas[0]

    df = leer_excel(ruta, sheet_name=hoja_elegida)
    validar_estructura_exacta(df.columns, contexto=f"archivo corregido ({hoja_elegida})")

    registros: list[dict[str, Any]] = []
    for posicion, fila in df.iterrows():
        numero = int(posicion) + 2
        valores = {
            columna: _normalizar_corregido(fila[columna], columna)
            for columna in COLUMNAS_OFICIALES
        }
        if all(valor is None for valor in valores.values()):
            continue
        registro = dict(valores)
        registro["_fila_origen"] = numero
        registros.append(registro)

    normalizado = pd.DataFrame(registros, columns=list(COLUMNAS_OFICIALES) + ["_fila_origen"])
    return normalizado, hoja_elegida


# ---------------------------------------------------------------------------
# Comparación
# ---------------------------------------------------------------------------
def _similitud_valor(columna: str, a: Any, b: Any) -> float:
    canonico_a = valor_canonico(columna, a)
    canonico_b = valor_canonico(columna, b)

    if canonico_a == canonico_b:
        return 1.0
    if not canonico_a and not canonico_b:
        return 0.5
    if not canonico_a or not canonico_b:
        return 0.0

    if columna in (C_FECHA, C_HORAS):
        return 0.0

    return difflib.SequenceMatcher(None, canonico_a, canonico_b).ratio()


def similitud_filas(
    a: dict[str, Any],
    b: dict[str, Any],
) -> float:
    """Similitud ponderada entre dos filas (0..1)."""

    total = 0.0
    for columna, peso in _PESOS.items():
        total += peso * _similitud_valor(columna, a.get(columna), b.get(columna))
    return total


def _fecha_aproximada(a: Any, b: Any) -> bool:
    if isinstance(a, (date, datetime)) and isinstance(b, (date, datetime)):
        return abs((a - b).days) <= 1
    return valor_canonico(C_FECHA, a) == valor_canonico(C_FECHA, b)


def _bloquea(a: FilaOriginal, b: dict[str, Any]) -> bool:
    """Evita comparar filas evidentemente distintas."""

    if a.canonicos[C_SOLICITANTE] == valor_canonico(
        C_SOLICITANTE, b.get(C_SOLICITANTE)
    ):
        return True
    if _fecha_aproximada(a.datos.get(C_FECHA), b.get(C_FECHA)):
        return True

    detalle_a = a.canonicos[C_DETALLE]
    detalle_b = valor_canonico(C_DETALLE, b.get(C_DETALLE))
    if detalle_a and detalle_b:
        ratio = difflib.SequenceMatcher(None, detalle_a, detalle_b).ratio()
        if ratio >= 0.55:
            return True
    return False


def analizar_reimportacion(
    ruta_corregido: Path | str,
    ruta_manifiesto: Path | str | None = None,
    hoja: str | None = None,
    umbral: float | None = None,
    margen: float | None = None,
) -> ResultadoReimportacion:
    """Compara el archivo corregido con el procesamiento original."""

    ruta_corregido = Path(ruta_corregido)
    ruta_manifiesto = (
        Path(ruta_manifiesto)
        if ruta_manifiesto
        else buscar_manifiesto_corregido(ruta_corregido)
    )
    umbral = cfg.UMBRAL_COINCIDENCIA if umbral is None else umbral
    margen = cfg.MARGEN_COINCIDENCIA if margen is None else margen

    originales, manifiesto = cargar_filas_manifiesto(ruta_manifiesto)
    df_corregido, _hoja = leer_corregido(ruta_corregido, hoja=hoja)

    usados: set[str] = set()
    cambios: list[CambioReimportacion] = []

    # Índice de huellas exactas (varias filas pueden compartir huella).
    por_huella: dict[str, list[FilaOriginal]] = {}
    for original in originales:
        por_huella.setdefault(original.huella, []).append(original)

    pendientes: list[tuple[int, dict[str, Any]]] = []
    huellas_corregidas: dict[str, int] = {}

    for _, serie in df_corregido.iterrows():
        fila = serie.to_dict()
        huella = huella_fila(fila)
        huellas_corregidas[huella] = huellas_corregidas.get(huella, 0) + 1
        es_duplicado = huellas_corregidas[huella] > 1

        candidatos = [
            original
            for original in por_huella.get(huella, [])
            if original.id_tecnico not in usados
        ]
        if candidatos:
            original = candidatos[0]
            usados.add(original.id_tecnico)
            cambios.append(
                CambioReimportacion(
                    tipo=TIPO_SIN_CAMBIOS,
                    fila_corregida=int(fila["_fila_origen"]),
                    fila_original=original.fila,
                    id_tecnico=original.id_tecnico,
                    similitud=1.0,
                    antes=original.datos,
                    despues=fila_a_dict(fila),
                    observacion=(
                        "Fila idéntica al procesamiento original."
                        if not es_duplicado
                        else "Fila idéntica; coincide con una ocurrencia duplicada."
                    ),
                    es_duplicado=es_duplicado,
                )
            )
            continue

        pendientes.append((int(fila["_fila_origen"]), fila))

    for numero_fila, fila in pendientes:
        disponibles = [
            original
            for original in originales
            if original.id_tecnico not in usados
        ]
        evaluados: list[tuple[float, FilaOriginal]] = []
        for original in disponibles:
            if not _bloquea(original, fila):
                continue
            evaluados.append((similitud_filas(original.datos, fila), original))

        evaluados.sort(key=lambda item: item[0], reverse=True)
        mejor = evaluados[0] if evaluados else None
        segundo = evaluados[1] if len(evaluados) > 1 else None

        huella = huella_fila(fila)
        es_duplicado = huellas_corregidas.get(huella, 0) > 1

        if mejor is None or mejor[0] < umbral:
            cambios.append(
                CambioReimportacion(
                    tipo=TIPO_DUPLICADO if es_duplicado else TIPO_NUEVO,
                    fila_corregida=numero_fila,
                    similitud=mejor[0] if mejor else 0.0,
                    despues=fila_a_dict(fila),
                    observacion=(
                        "Fila nueva (posible duplicado dentro del archivo)."
                        if es_duplicado
                        else "Fila nueva sin coincidencia con el procesamiento original."
                    ),
                    es_duplicado=es_duplicado,
                )
            )
            continue

        if segundo is not None and (mejor[0] - segundo[0]) < margen and segundo[0] >= umbral:
            cambios.append(
                CambioReimportacion(
                    tipo=TIPO_AMBIGUO,
                    fila_corregida=numero_fila,
                    fila_original=mejor[1].fila,
                    id_tecnico=mejor[1].id_tecnico,
                    similitud=mejor[0],
                    despues=fila_a_dict(fila),
                    observacion=(
                        f"Coincidencia ambigua entre las filas {mejor[1].fila} "
                        f"y {segundo[1].fila} (similitud {mejor[0]:.2f} vs "
                        f"{segundo[0]:.2f}). Requiere revisión manual."
                    ),
                    es_duplicado=es_duplicado,
                )
            )
            continue

        original = mejor[1]
        usados.add(original.id_tecnico)
        diferencias = diferencia_campos(original.datos, fila)
        cambios.append(
            CambioReimportacion(
                tipo=TIPO_MODIFICADO if diferencias else TIPO_SIN_CAMBIOS,
                fila_corregida=numero_fila,
                fila_original=original.fila,
                id_tecnico=original.id_tecnico,
                similitud=mejor[0],
                campos_cambiados=diferencias,
                antes=original.datos,
                despues=fila_a_dict(fila),
                observacion=(
                    "Campos modificados: " + ", ".join(diferencias)
                    if diferencias
                    else "Sin cambios canónicos."
                ),
                es_duplicado=es_duplicado,
            )
        )

    for original in originales:
        if original.id_tecnico in usados:
            continue
        cambios.append(
            CambioReimportacion(
                tipo=TIPO_AUSENTE,
                fila_original=original.fila,
                id_tecnico=original.id_tecnico,
                antes=original.datos,
                observacion=(
                    "Fila ausente en el archivo corregido. No se elimina del "
                    "maestro histórico."
                ),
            )
        )

    # Filas sin solicitante no deben aplicarse.
    for cambio in cambios:
        if cambio.tipo in (TIPO_NUEVO, TIPO_MODIFICADO) and not cambio.despues.get(
            C_SOLICITANTE
        ):
            cambio.tipo = TIPO_ERROR
            cambio.observacion = "La fila no tiene solicitante; no se aplicará."

    resumen: dict[str, int] = {}
    for cambio in cambios:
        resumen[cambio.tipo] = resumen.get(cambio.tipo, 0) + 1

    resultado = ResultadoReimportacion(
        ruta_corregido=ruta_corregido,
        ruta_manifiesto=Path(ruta_manifiesto),
        df_corregido=df_corregido,
        cambios=cambios,
        resumen=resumen,
        periodo=str(manifiesto.get("periodo") or ""),
        id_ejecucion_original=str(manifiesto.get("id_ejecucion") or ""),
    )

    LOGGER.info(
        "Reimportación analizada: %s | nuevos=%s modificados=%s sin_cambios=%s "
        "duplicados=%s ambiguos=%s ausentes=%s",
        ruta_corregido.name,
        resumen.get(TIPO_NUEVO, 0),
        resumen.get(TIPO_MODIFICADO, 0),
        resumen.get(TIPO_SIN_CAMBIOS, 0),
        resumen.get(TIPO_DUPLICADO, 0),
        resumen.get(TIPO_AMBIGUO, 0),
        resumen.get(TIPO_AUSENTE, 0),
    )
    return resultado


# ---------------------------------------------------------------------------
# Aplicación
# ---------------------------------------------------------------------------
def aplicar_reimportacion(
    resultado: ResultadoReimportacion,
    *,
    permitir_duplicados: bool = False,
    ruta_maestro: Path | str | None = None,
    usuario_ejecutor: str = "",
    id_ejecucion: str | None = None,
) -> dict[str, Any]:
    """Aplica los cambios confirmados al maestro histórico de tickets."""

    from core.maestro_tickets import MaestroTickets

    if resultado.resumen.get(TIPO_AMBIGUO, 0):
        raise ErrorArchivo(
            "La reimportación contiene coincidencias ambiguas. Resuélvalas antes "
            "de aplicar los cambios (el sistema no actualiza automáticamente una "
            "correspondencia dudosa)."
        )

    ejecucion = id_ejecucion or f"REIMP_{datetime.now():%Y%m%d_%H%M%S}"
    maestro = MaestroTickets.cargar(ruta_maestro)

    indices: list[int] = []
    ids: list[str | None] = []
    for posicion, (_, fila) in enumerate(resultado.df_corregido.iterrows()):
        numero = int(fila["_fila_origen"])
        cambio = next(
            (
                item
                for item in resultado.cambios
                if item.fila_corregida == numero
                and item.tipo
                in (TIPO_NUEVO, TIPO_MODIFICADO, TIPO_SIN_CAMBIOS, TIPO_DUPLICADO)
            ),
            None,
        )
        if cambio is None:
            continue
        if cambio.tipo == TIPO_DUPLICADO and not permitir_duplicados:
            continue
        indices.append(posicion)
        ids.append(cambio.id_tecnico)

    df_aplicar = resultado.df_corregido.iloc[indices].reset_index(drop=True)
    df_aplicar = df_aplicar.loc[:, list(COLUMNAS_OFICIALES)]

    salida = maestro.aplicar(
        df_aplicar,
        ids=ids,
        id_ejecucion=ejecucion,
        tipo_operacion="REIMPORTACION",
        archivo_fuente=resultado.ruta_corregido.name,
        permitir_duplicados=permitir_duplicados,
    )

    guardado = maestro.guardar(ruta_xlsx=ruta_maestro, crear_backup=True)

    from core import auditoria

    if salida.cambios_historial:
        auditoria.registrar_cambios_maestro(
            salida.cambios_historial,
            id_ejecucion=ejecucion,
            tipo="REIMPORTACION",
            archivo_fuente=resultado.ruta_corregido.name,
        )

    auditoria.registrar_evento(
        "REIMPORTACION",
        archivo_original=str(resultado.ruta_corregido.resolve()),
        archivo_salida=str(guardado.ruta_xlsx or ""),
        periodo=resultado.periodo,
        nuevos=salida.nuevos,
        modificados=salida.actualizados,
        usuario_ejecutor=usuario_ejecutor,
        mensajes=(
            f"Sin cambios: {salida.sin_cambios} | "
            f"Duplicados aplicados: {salida.duplicados} | "
            f"Ambiguos: {resultado.resumen.get(TIPO_AMBIGUO, 0)} | "
            f"Errores: {resultado.resumen.get(TIPO_ERROR, 0)}"
        ),
        id_ejecucion=ejecucion,
    )

    return {
        "id_ejecucion": ejecucion,
        "nuevos": salida.nuevos,
        "actualizados": salida.actualizados,
        "sin_cambios": salida.sin_cambios,
        "duplicados": salida.duplicados,
        "omitidos": salida.omitidos,
        "rutas": guardado,
        "total_maestro": len(maestro),
    }
