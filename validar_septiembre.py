"""Validación del sistema contra el análisis de septiembre de 2026.

Procesa el Excel original (sin modificarlo) y compara los indicadores con los
valores de referencia del informe de INKACROPS:

- 179 tickets y 426.75 horas.
- 22 días hábiles, 8.1 tickets/día y 19.4 horas/día.
- 5 responsables, 880 horas nominales y 48.5 % de imputación.
- Atención: Mesa de Ayuda 110, Gestión 41, Proyectos 28.
- Distribución de requerimientos y de responsables del informe.

No altera los datos para forzar coincidencias: solo informa PASS/FAIL.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl import load_workbook

from config import configuracion as cfg
from config.logging_config import configurar_consola, configurar_logging
from core.archivos import ErrorArchivo, ErrorEstructura
from core.esquema import COLUMNAS_OFICIALES, C_ATENCION, C_HORAS, C_RESPONSABLE, C_REQUERIMIENTO
from core.maestro import Maestro
from core.procesado import resolver_salida
from core.procesador import procesar_archivo
from core.reportes import calcular_indicadores, clave_grupo

REFERENCIA_TICKETS = 179
REFERENCIA_HORAS = 426.75
REFERENCIA_DIAS_HABILES = 22
REFERENCIA_TICKETS_DIA = 8.1
REFERENCIA_HORAS_DIA = 19.4
REFERENCIA_RESPONSABLES = 5
REFERENCIA_CAPACIDAD = 880.0
REFERENCIA_IMPUTACION = 48.5

REFERENCIA_ATENCION = {
    "MESA DE AYUDA": 110,
    "GESTION": 41,
    "PROYECTOS": 28,
}

REFERENCIA_REQUERIMIENTO = {
    "SOPORTE": 90,
    "GESTION": 37,
    "PROYECTO SOFTWARE": 23,
    "INCIDENCIAS": 18,
    "MEJORAS": 4,
    "PROYECTOS DE INFRAESTRUCTURA": 4,
    "SOLICITUD": 2,
    "PROYECTO BI": 1,
}

REFERENCIA_RESPONSABLE = {
    "MARCO CCANTO": (48, 136.50),
    "PAUL ARANDA": (21, 115.00),
    "MIGUEL BARCENAS": (48, 110.50),
    "CIRO HUAMANI": (48, 51.00),
    "GIALINA MACHADO": (14, 13.75),
}

_PASS = "PASS"
_FAIL = "FAIL"


def _hash(ruta: Path) -> str:
    digest = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest()


def _localizar_archivo(valor: str | None) -> Path:
    if valor:
        ruta = cfg.resolver_ruta(valor)
        if ruta.is_file():
            return ruta
        raise ErrorArchivo(f"No existe el archivo indicado: {ruta}")

    candidatos = [
        cfg.PROYECTO_RAIZ / "2026" / "EXCEL" / cfg.ARCHIVO_SEPTIEMBRE,
        cfg.INPUT_DIR / cfg.ARCHIVO_SEPTIEMBRE,
    ]
    for ruta in candidatos:
        if ruta.is_file():
            return ruta
    raise ErrorArchivo(
        "No se encontró el Excel de septiembre de 2026. "
        "Indíquelo con --archivo."
    )


class Validacion:
    def __init__(self) -> None:
        self.resultados: list[tuple[str, str, str, str]] = []

    def comparar(
        self,
        nombre: str,
        esperado: Any,
        obtenido: Any,
        tolerancia: float = 0.0,
    ) -> None:
        if isinstance(esperado, (int, float)) and isinstance(obtenido, (int, float)):
            correcto = abs(float(esperado) - float(obtenido)) <= tolerancia + 1e-9
        else:
            correcto = str(esperado) == str(obtenido)
        estado = _PASS if correcto else _FAIL
        self.resultados.append(
            (estado, nombre, str(esperado), str(obtenido))
        )

    def comparar_mapa(
        self,
        nombre: str,
        esperado: dict[str, Any],
        obtenido: dict[str, Any],
        tolerancia: float = 0.0,
    ) -> None:
        claves = sorted(set(esperado) | set(obtenido))
        for clave in claves:
            self.comparar(
                f"{nombre} · {clave}",
                esperado.get(clave, 0),
                obtenido.get(clave, 0),
                tolerancia=tolerancia,
            )

    @property
    def fallos(self) -> int:
        return sum(1 for estado, *_ in self.resultados if estado == _FAIL)

    def imprimir(self) -> None:
        print()
        print("=" * 90)
        print("  VALIDACIÓN CON SEPTIEMBRE DE 2026 - INKACROPS")
        print("=" * 90)
        ancho = max(len(nombre) for _, nombre, _, _ in self.resultados)
        print(f"{'Estado':<6} {'Comprobación':<{ancho}} {'Esperado':>22} {'Obtenido':>22}")
        print("-" * 90)
        for estado, nombre, esperado, obtenido in self.resultados:
            print(f"{estado:<6} {nombre:<{ancho}} {esperado:>22} {obtenido:>22}")
        print("-" * 90)
        print(
            f"Total: {len(self.resultados)} | PASS: "
            f"{len(self.resultados) - self.fallos} | FAIL: {self.fallos}"
        )
        print("=" * 90)


def _distribucion(df: pd.DataFrame, columna: str) -> dict[str, int]:
    if df.empty:
        return {}
    claves = df[columna].map(clave_grupo)
    claves = claves[claves != ""]
    return {str(clave): int(valor) for clave, valor in claves.value_counts().items()}


def _horas_por_responsable(df: pd.DataFrame) -> dict[str, tuple[int, float]]:
    resultado: dict[str, tuple[int, float]] = {}
    if df.empty:
        return resultado
    trabajo = pd.DataFrame(
        {
            "clave": df[C_RESPONSABLE].map(clave_grupo),
            "horas": pd.to_numeric(df[C_HORAS], errors="coerce").fillna(0.0),
        }
    )
    trabajo = trabajo[trabajo["clave"] != ""]
    for clave, grupo in trabajo.groupby("clave", sort=False):
        resultado[str(clave)] = (int(len(grupo)), round(float(grupo["horas"].sum()), 2))
    return resultado


def validar(archivo: Path, salida: Path, sobrescribir: bool) -> int:
    validacion = Validacion()
    original_hash = _hash(archivo)

    maestro = Maestro.cargar() if Path(cfg.MAESTRO_XLSX).exists() else Maestro()
    destino = resolver_salida(archivo, salida, sobrescribir=sobrescribir)
    estadisticas = procesar_archivo(
        archivo,
        maestro,
        ruta_salida=destino,
        sobrescribir=sobrescribir,
        formato_fecha=cfg.FORMATO_FECHA_PREFERIDO,
        registrar_auditoria=False,
    )

    df = pd.read_excel(estadisticas.archivo_salida, sheet_name=cfg.HOJA_DETALLE)
    indicadores = calcular_indicadores(
        df,
        dias_habiles=REFERENCIA_DIAS_HABILES,
        horas_jornada=cfg.HORAS_JORNADA,
    )

    # Estructura oficial.
    validacion.comparar(
        "Columnas oficiales (11, en orden)",
        " | ".join(COLUMNAS_OFICIALES),
        " | ".join(str(c) for c in df.columns),
    )
    libro = load_workbook(estadisticas.archivo_salida, read_only=True)
    try:
        hoja = libro[cfg.HOJA_DETALLE]
        validacion.comparar(
            "Cantidad de columnas del detalle", 11, hoja.max_column
        )
        validacion.comparar(
            "Filas de datos del detalle", REFERENCIA_TICKETS, hoja.max_row - 1
        )
    finally:
        libro.close()

    # Indicadores generales.
    validacion.comparar(
        "Total de tickets", REFERENCIA_TICKETS, int(indicadores["Total de tickets"])
    )
    validacion.comparar(
        "Horas registradas", REFERENCIA_HORAS, float(indicadores["Horas registradas"]), 0.005
    )
    validacion.comparar(
        "Días hábiles considerados",
        REFERENCIA_DIAS_HABILES,
        int(indicadores["Días hábiles considerados"]),
    )
    validacion.comparar(
        "Promedio de tickets por día",
        REFERENCIA_TICKETS_DIA,
        float(indicadores["Promedio de tickets por día"]),
        0.05,
    )
    validacion.comparar(
        "Promedio de horas por día",
        REFERENCIA_HORAS_DIA,
        float(indicadores["Promedio de horas por día"]),
        0.05,
    )
    validacion.comparar(
        "Responsables", REFERENCIA_RESPONSABLES, int(indicadores["Responsables"])
    )
    validacion.comparar(
        "Capacidad nominal estimada (h)",
        REFERENCIA_CAPACIDAD,
        float(indicadores["Capacidad nominal estimada (h)"]),
        0.01,
    )
    validacion.comparar(
        "Imputación de horas (%)",
        REFERENCIA_IMPUTACION,
        float(indicadores["Imputación de horas (%)"]),
        0.05,
    )

    # Distribuciones.
    validacion.comparar_mapa(
        "Atención", REFERENCIA_ATENCION, _distribucion(df, C_ATENCION)
    )
    validacion.comparar_mapa(
        "Requerimiento",
        REFERENCIA_REQUERIMIENTO,
        _distribucion(df, C_REQUERIMIENTO),
    )

    horas_responsable = _horas_por_responsable(df)
    for responsable, (tickets, horas) in REFERENCIA_RESPONSABLE.items():
        obtenido = horas_responsable.get(responsable, (0, 0.0))
        validacion.comparar(
            f"Responsable · {responsable} · tickets", tickets, obtenido[0]
        )
        validacion.comparar(
            f"Responsable · {responsable} · horas", horas, obtenido[1], 0.005
        )

    # Integridad.
    validacion.comparar(
        "Registros leídos coinciden con los escritos",
        REFERENCIA_TICKETS,
        int(estadisticas.registros_totales),
    )
    validacion.comparar(
        "Archivo original sin modificar",
        original_hash,
        _hash(archivo),
    )
    validacion.comparar(
        "Manifiesto generado",
        True,
        bool(estadisticas.archivo_manifiesto),
    )

    validacion.imprimir()
    print()
    print(f"Excel procesado : {cfg.ruta_visible(Path(estadisticas.archivo_salida))}")
    print(f"Manifiesto      : {cfg.ruta_visible(Path(estadisticas.archivo_manifiesto or ''))}")
    print(f"Hoja            : {estadisticas.hoja}")
    print(f"Periodo         : {estadisticas.periodo}")
    print(f"ID de ejecución : {estadisticas.id_ejecucion}")
    if validacion.fallos:
        print("\nResultado: VALIDACIÓN CON DISCREPANCIAS. Revise los FAIL.")
        return 1
    print("\nResultado: TODOS LOS INDICADORES DE REFERENCIA COINCIDEN.")
    return 0


def main(argv: list[str] | None = None) -> int:
    configurar_consola()
    configurar_logging()
    cfg.asegurar_directorios()

    parser = argparse.ArgumentParser(
        description="Valida el sistema con el Excel de septiembre de 2026."
    )
    parser.add_argument("--archivo", help="Ruta del Excel original de septiembre.")
    parser.add_argument(
        "--salida",
        default=str(cfg.OUTPUT_DIR / "consulta_septiembre_2026_procesado.xlsx"),
        help="Ruta del Excel procesado de validación.",
    )
    parser.add_argument(
        "--sobrescribir",
        action="store_true",
        help="Sobrescribe la salida si ya existe.",
    )
    args = parser.parse_args(argv)

    try:
        archivo = _localizar_archivo(args.archivo)
        return validar(archivo, Path(args.salida), args.sobrescribir)
    except (ErrorArchivo, ErrorEstructura) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - red de seguridad
        print(f"\nERROR inesperado: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
