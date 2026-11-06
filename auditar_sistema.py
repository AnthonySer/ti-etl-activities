"""Auditoría técnica del Sistema Integral de Tickets TI - INKACROPS.

Verifica, sin modificar nada, la integridad de:

- la base de correspondencias USUARIO → ÁREA;
- el maestro histórico de tickets y su hoja CONTROL;
- el Excel procesado y su manifiesto de ejecución;
- el archivo corregido, si se indica;
- el registro de auditoría y el historial de cambios;
- la integridad del archivo original frente a su hash registrado.

Uso:
    python auditar_sistema.py
    python auditar_sistema.py --procesado output/consulta_septiembre_2026_procesado.xlsx
    python auditar_sistema.py --corregido output/consulta_septiembre_2026_procesado_corregido.xlsx

Devuelve 0 si no hay fallos críticos y 1 si encuentra alguno.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl import load_workbook

from config import configuracion as cfg
from core.archivos import (
    COLUMNA_AREA,
    COLUMNA_USUARIO,
    ErrorArchivo,
    ErrorEstructura,
    clave_esperada,
    leer_excel,
)
from core.auditoria import COLUMNAS_AUDITORIA, cargar_auditoria
from core.esquema import (
    C_FECHA,
    C_HORAS,
    COLUMNAS_OFICIALES,
    fila_a_dict,
    huella_fila,
    validar_estructura_exacta,
)
from core.maestro import ErrorMaestro, Maestro
from core.maestro_tickets import CONTROL_COLUMNAS, MaestroTickets
from core.normalizador import normalizar_usuario
from core.reimportacion import (
    TIPO_AMBIGUO,
    analizar_reimportacion,
    buscar_manifiesto_corregido,
    cargar_filas_manifiesto,
)

OK = "OK"
ADVERTENCIA = "ADVERTENCIA"
FALLO = "FALLO"
OMITIDO = "OMITIDO"


@dataclass
class Hallazgo:
    estado: str
    comprobacion: str
    detalle: str = ""


@dataclass
class Auditoria:
    hallazgos: list[Hallazgo] = field(default_factory=list)

    def registrar(self, estado: str, comprobacion: str, detalle: str = "") -> None:
        self.hallazgos.append(Hallazgo(estado, comprobacion, detalle))

    @property
    def fallos(self) -> int:
        return sum(1 for h in self.hallazgos if h.estado == FALLO)

    @property
    def advertencias(self) -> int:
        return sum(1 for h in self.hallazgos if h.estado == ADVERTENCIA)

    @property
    def omitidos(self) -> int:
        return sum(1 for h in self.hallazgos if h.estado == OMITIDO)

    def imprimir(self, titulo: str = "AUDITORÍA TÉCNICA") -> None:
        print()
        print("=" * 86)
        print(f"  {titulo} - INKACROPS")
        print("=" * 86)
        for hallazgo in self.hallazgos:
            print(f"[{hallazgo.estado:^11}] {hallazgo.comprobacion}")
            if hallazgo.detalle:
                for linea in str(hallazgo.detalle).splitlines():
                    print(f"              {linea}")
        print("-" * 86)
        print(
            f"Comprobaciones: {len(self.hallazgos)} | Fallos: {self.fallos} | "
            f"Advertencias: {self.advertencias} | Omitidas: {self.omitidos}"
        )
        if self.fallos:
            print("RESULTADO: CON FALLOS CRÍTICOS")
        elif self.advertencias:
            print("RESULTADO: SIN FALLOS CRÍTICOS (con advertencias)")
        else:
            print("RESULTADO: INTEGRIDAD VERIFICADA")
        print("=" * 86)


def _hash_sha256(ruta: Path) -> str:
    digest = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest()


def _hojas(ruta: Path) -> list[str]:
    libro = load_workbook(ruta, read_only=True)
    try:
        return list(libro.sheetnames)
    finally:
        libro.close()


# ---------------------------------------------------------------------------
# Base de áreas
# ---------------------------------------------------------------------------
def auditar_maestro_areas(
    auditoria: Auditoria,
    ruta_xlsx: Path,
    ruta_csv: Path,
) -> Maestro | None:
    if not ruta_xlsx.is_file():
        auditoria.registrar(
            FALLO, "Base USUARIO→ÁREA: archivo presente", f"No existe: {ruta_xlsx}"
        )
        return None

    if not ruta_csv.is_file():
        auditoria.registrar(
            ADVERTENCIA,
            "Base USUARIO→ÁREA: espejo CSV",
            f"No existe el espejo: {ruta_csv}",
        )

    try:
        maestro = Maestro.cargar(
            ruta_xlsx,
            ruta_csv,
            verificar_consistencia=ruta_csv.is_file(),
        )
    except (ErrorMaestro, ErrorArchivo, ErrorEstructura) as exc:
        auditoria.registrar(FALLO, "Base USUARIO→ÁREA: carga", str(exc))
        return None

    problemas = maestro.validar()
    if problemas:
        auditoria.registrar(
            FALLO, "Base USUARIO→ÁREA: validación", " ".join(problemas)
        )
    else:
        auditoria.registrar(
            OK,
            "Base USUARIO→ÁREA: validación",
            f"{len(maestro)} correspondencias, columnas {COLUMNA_USUARIO} y "
            f"{COLUMNA_AREA}.",
        )

    df = maestro.como_dataframe()
    if df.empty:
        auditoria.registrar(
            ADVERTENCIA,
            "Base USUARIO→ÁREA: contenido",
            "La base está vacía; los tickets quedarán pendientes de asignación.",
        )
    else:
        claves = [normalizar_usuario(valor) for valor in df[COLUMNA_USUARIO]]
        if len(claves) != len(set(claves)):
            auditoria.registrar(
                FALLO, "Base USUARIO→ÁREA: usuarios únicos", "Hay duplicados."
            )
        else:
            auditoria.registrar(
                OK,
                "Base USUARIO→ÁREA: usuarios únicos",
                f"{len(claves)} usuarios únicos; {df[COLUMNA_AREA].nunique()} áreas.",
            )

    if ruta_csv.is_file():
        try:
            df_csv = pd.read_csv(ruta_csv, encoding="utf-8-sig")
            df_xlsx = leer_excel(ruta_xlsx)
            columnas_csv = {clave_esperada(c) for c in df_csv.columns}
            columnas_xlsx = {clave_esperada(c) for c in df_xlsx.columns}
            if columnas_csv != columnas_xlsx:
                auditoria.registrar(
                    FALLO,
                    "Base USUARIO→ÁREA: Excel y CSV coinciden",
                    f"Columnas distintas: {sorted(columnas_xlsx)} vs {sorted(columnas_csv)}.",
                )
            elif len(df_csv) != len(df_xlsx):
                auditoria.registrar(
                    FALLO,
                    "Base USUARIO→ÁREA: Excel y CSV coinciden",
                    f"Filas distintas: {len(df_xlsx)} vs {len(df_csv)}.",
                )
            else:
                auditoria.registrar(
                    OK,
                    "Base USUARIO→ÁREA: Excel y CSV coinciden",
                    f"{len(df_xlsx)} filas en ambos archivos.",
                )
        except (OSError, ValueError) as exc:
            auditoria.registrar(
                FALLO, "Base USUARIO→ÁREA: lectura del espejo CSV", str(exc)
            )

    return maestro


# ---------------------------------------------------------------------------
# Maestro histórico de tickets
# ---------------------------------------------------------------------------
def auditar_maestro_tickets(
    auditoria: Auditoria,
    ruta_xlsx: Path,
    ruta_csv: Path,
) -> MaestroTickets | None:
    if not ruta_xlsx.is_file():
        auditoria.registrar(
            OMITIDO,
            "Maestro histórico de tickets",
            f"Aún no existe: {ruta_xlsx}. Se creará en la primera consolidación.",
        )
        return None

    try:
        maestro = MaestroTickets.cargar(
            ruta_xlsx,
            ruta_csv,
            verificar_consistencia=ruta_csv.is_file(),
        )
    except (ErrorArchivo, ErrorEstructura) as exc:
        auditoria.registrar(
            FALLO, "Maestro histórico de tickets: carga", str(exc)
        )
        return None

    if not ruta_csv.is_file():
        auditoria.registrar(
            ADVERTENCIA,
            "Maestro histórico de tickets: espejo CSV",
            f"No existe el espejo: {ruta_csv}",
        )

    problemas = maestro.validar()
    if problemas:
        auditoria.registrar(
            FALLO, "Maestro histórico de tickets: validación", " ".join(problemas)
        )
    else:
        auditoria.registrar(
            OK,
            "Maestro histórico de tickets: validación",
            "Detalle y CONTROL alineados; huellas e identificadores coherentes.",
        )

    df = maestro.a_dataframe()
    if list(df.columns) != list(COLUMNAS_OFICIALES):
        auditoria.registrar(
            FALLO,
            "Maestro histórico de tickets: 11 columnas oficiales",
            f"Columnas actuales: {list(df.columns)}.",
        )
    else:
        auditoria.registrar(
            OK,
            "Maestro histórico de tickets: 11 columnas oficiales",
            ", ".join(COLUMNAS_OFICIALES),
        )

    control = maestro.control()
    if list(control.columns) != list(CONTROL_COLUMNAS):
        auditoria.registrar(
            FALLO,
            "Maestro histórico de tickets: hoja CONTROL",
            f"Columnas: {list(control.columns)}.",
        )
    else:
        auditoria.registrar(
            OK,
            "Maestro histórico de tickets: hoja CONTROL",
            f"{len(control)} filas técnicas.",
        )

    ids = control["ID_TECNICO"].astype(str)
    if ids.duplicated().any():
        auditoria.registrar(
            FALLO,
            "Maestro histórico de tickets: IDs únicos",
            f"{int(ids.duplicated().sum())} identificadores duplicados.",
        )
    else:
        auditoria.registrar(
            OK,
            "Maestro histórico de tickets: IDs únicos",
            f"{len(ids)} identificadores.",
        )

    estado = maestro.estado()
    auditoria.registrar(
        OK if len(df) else ADVERTENCIA,
        "Maestro histórico de tickets: contenido",
        f"{estado['registros']} tickets | {estado['responsables']} responsables | "
        f"periodo {estado['periodo'] or 'sin fechas'}.",
    )

    copias = sorted(cfg.BACKUPS_DIR.glob("maestro_tickets_*.xlsx"))
    if copias:
        auditoria.registrar(
            OK,
            "Maestro histórico de tickets: copias de seguridad",
            f"{len(copias)} copia(s); la más reciente: {copias[-1].name}.",
        )
    else:
        auditoria.registrar(
            ADVERTENCIA,
            "Maestro histórico de tickets: copias de seguridad",
            "No se encontraron copias en data/backups.",
        )

    return maestro


# ---------------------------------------------------------------------------
# Excel procesado y manifiesto
# ---------------------------------------------------------------------------
def auditar_procesado(
    auditoria: Auditoria,
    ruta: Path,
    ruta_manifiesto: Path | None = None,
) -> None:
    if not ruta.is_file():
        auditoria.registrar(
            OMITIDO, "Excel procesado", f"No se indicó un archivo existente: {ruta}"
        )
        return

    try:
        hojas = _hojas(ruta)
    except (OSError, ValueError) as exc:
        auditoria.registrar(FALLO, "Excel procesado: lectura", str(exc))
        return

    if cfg.HOJA_DETALLE not in hojas:
        auditoria.registrar(
            FALLO,
            "Excel procesado: hoja de detalle",
            f"No existe '{cfg.HOJA_DETALLE}'. Hojas: {', '.join(hojas)}.",
        )
        return

    try:
        df = leer_excel(ruta, sheet_name=cfg.HOJA_DETALLE)
    except (OSError, ValueError) as exc:
        auditoria.registrar(FALLO, "Excel procesado: lectura del detalle", str(exc))
        return

    try:
        validar_estructura_exacta(df.columns, contexto="Excel procesado")
    except ErrorEstructura as exc:
        auditoria.registrar(
            FALLO, "Excel procesado: 11 columnas y orden oficial", str(exc)
        )
        return

    auditoria.registrar(
        OK,
        "Excel procesado: 11 columnas y orden oficial",
        f"{len(df)} registros; " + ", ".join(COLUMNAS_OFICIALES),
    )

    libro = load_workbook(ruta, read_only=True)
    try:
        hoja = libro[cfg.HOJA_DETALLE]
        if hoja.max_column != len(COLUMNAS_OFICIALES):
            auditoria.registrar(
                FALLO,
                "Excel procesado: sin columnas internas",
                f"La hoja tiene {hoja.max_column} columnas (se esperaban 11).",
            )
        else:
            auditoria.registrar(
                OK,
                "Excel procesado: sin columnas internas",
                "El detalle contiene exactamente 11 columnas.",
            )
        encabezados = next(hoja.iter_rows(min_row=1, max_row=1, values_only=True))
        if encabezados != COLUMNAS_OFICIALES:
            auditoria.registrar(
                FALLO,
                "Excel procesado: encabezados",
                f"Encabezados leídos: {encabezados}.",
            )
    finally:
        libro.close()

    auxiliares = [n for n in ("Observaciones", "Resumen", "Control") if n in hojas]
    auditoria.registrar(
        OK if len(auxiliares) == 3 else ADVERTENCIA,
        "Excel procesado: hojas auxiliares",
        "Presentes: " + (", ".join(auxiliares) or "ninguna") + ".",
    )

    if ruta_manifiesto is None:
        ruta_manifiesto = ruta.with_name(f"{ruta.stem}{cfg.SUFIJO_MANIFIESTO}")
    if not ruta_manifiesto.is_file():
        auditoria.registrar(
            FALLO,
            "Excel procesado: manifiesto de control",
            f"No existe: {ruta_manifiesto}",
        )
        return

    try:
        datos = json.loads(ruta_manifiesto.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        auditoria.registrar(FALLO, "Excel procesado: lectura del manifiesto", str(exc))
        return

    version = datos.get("version")
    if version != 2:
        auditoria.registrar(
            ADVERTENCIA,
            "Excel procesado: versión del manifiesto",
            f"Versión {version}; se esperaba 2 para reimportar con identificadores.",
        )
    else:
        auditoria.registrar(OK, "Excel procesado: versión del manifiesto", "v2.")

    try:
        filas_manifiesto, _ = cargar_filas_manifiesto(ruta_manifiesto)
    except ErrorArchivo as exc:
        auditoria.registrar(FALLO, "Excel procesado: filas del manifiesto", str(exc))
        return

    if len(filas_manifiesto) != len(df):
        auditoria.registrar(
            FALLO,
            "Excel procesado: manifiesto y filas coinciden",
            f"Manifiesto: {len(filas_manifiesto)}; detalle: {len(df)}.",
        )
        return
    auditoria.registrar(
        OK,
        "Excel procesado: manifiesto y filas coinciden",
        f"{len(df)} filas controladas.",
    )

    ids = [fila.id_tecnico for fila in filas_manifiesto]
    if len(ids) != len(set(ids)):
        auditoria.registrar(
            FALLO,
            "Excel procesado: identificadores técnicos únicos",
            "Hay identificadores repetidos en el manifiesto.",
        )
    else:
        auditoria.registrar(
            OK,
            "Excel procesado: identificadores técnicos únicos",
            f"{len(ids)} identificadores.",
        )

    discrepancias: list[str] = []
    for posicion, (_, fila) in enumerate(df.iterrows()):
        esperado = filas_manifiesto[posicion]
        datos_fila = fila_a_dict(fila)
        if huella_fila(datos_fila) != esperado.huella:
            discrepancias.append(
                f"fila {posicion + 2}: huella distinta de la registrada."
            )
            continue
        if datos_fila != esperado.datos:
            diferencias = [
                campo
                for campo in COLUMNAS_OFICIALES
                if str(datos_fila.get(campo, "")) != str(esperado.datos.get(campo, ""))
            ]
            if diferencias:
                discrepancias.append(
                    f"fila {posicion + 2}: valores distintos en "
                    + ", ".join(diferencias)
                )

    if discrepancias:
        auditoria.registrar(
            FALLO,
            "Excel procesado: contenido coincide con el manifiesto",
            "\n".join(discrepancias[:8])
            + (f"\n… {len(discrepancias) - 8} más." if len(discrepancias) > 8 else ""),
        )
    else:
        auditoria.registrar(
            OK,
            "Excel procesado: contenido coincide con el manifiesto",
            "Las 11 columnas de cada fila coinciden.",
        )

    if datos.get("observaciones") is not None:
        auditoria.registrar(
            OK,
            "Excel procesado: observaciones registradas",
            f"{len(datos.get('observaciones') or [])} observación(es) fuera del detalle.",
        )

    # Integridad del archivo original.
    original = Path(str(datos.get("archivo_entrada") or ""))
    hash_registrado = str(datos.get("hash_entrada") or "")
    if original.is_file() and hash_registrado:
        try:
            actual = _hash_sha256(original)
        except OSError as exc:
            auditoria.registrar(
                ADVERTENCIA,
                "Archivo original: hash",
                f"No se pudo leer el original: {exc}",
            )
        else:
            if actual == hash_registrado:
                auditoria.registrar(
                    OK,
                    "Archivo original: intacto",
                    f"{original.name} | SHA-256 {actual[:16]}…",
                )
            else:
                auditoria.registrar(
                    FALLO,
                    "Archivo original: intacto",
                    f"{original.name} cambió respecto del hash del manifiesto.",
                )
    else:
        auditoria.registrar(
            OMITIDO,
            "Archivo original: intacto",
            "El manifiesto no conserva una ruta/hash verificable.",
        )

    # Requisito de fechas y horas legibles.
    if C_FECHA in df.columns:
        invalidas = int(df[C_FECHA].isna().sum())
        auditoria.registrar(
            OK if invalidas == 0 else ADVERTENCIA,
            "Excel procesado: fechas completas",
            f"{invalidas} fecha(s) vacía(s).",
        )
    if C_HORAS in df.columns:
        horas = pd.to_numeric(df[C_HORAS], errors="coerce")
        no_numericas = int(horas.isna().sum())
        negativas = int((horas < 0).sum())
        auditoria.registrar(
            OK if no_numericas == 0 and negativas == 0 else ADVERTENCIA,
            "Excel procesado: horas numéricas",
            f"{no_numericas} no numérica(s), {negativas} negativa(s).",
        )


# ---------------------------------------------------------------------------
# Archivo corregido
# ---------------------------------------------------------------------------
def auditar_corregido(auditoria: Auditoria, ruta: Path) -> None:
    if not ruta.is_file():
        auditoria.registrar(
            OMITIDO, "Excel corregido", f"No existe: {ruta}"
        )
        return

    try:
        resultado = analizar_reimportacion(ruta)
    except (ErrorArchivo, ErrorEstructura) as exc:
        auditoria.registrar(FALLO, "Excel corregido: análisis", str(exc))
        return

    auditoria.registrar(
        OK,
        "Excel corregido: estructura y comparación",
        " | ".join(f"{tipo}: {cantidad}" for tipo, cantidad in sorted(resultado.resumen.items())),
    )
    if resultado.resumen.get(TIPO_AMBIGUO, 0):
        auditoria.registrar(
            FALLO,
            "Excel corregido: coincidencias ambiguas",
            f"{resultado.resumen[TIPO_AMBIGUO]} fila(s) requieren revisión manual.",
        )
    else:
        auditoria.registrar(
            OK, "Excel corregido: coincidencias ambiguas", "Ninguna."
        )
    auditoria.registrar(
        OK,
        "Excel corregido: manifiesto localizado",
        str(buscar_manifiesto_corregido(ruta)),
    )


# ---------------------------------------------------------------------------
# Auditoría e historial
# ---------------------------------------------------------------------------
def auditar_registro(auditoria: Auditoria, ruta: Path) -> None:
    if not ruta.is_file():
        auditoria.registrar(
            OMITIDO, "Registro de auditoría", f"Aún no existe: {ruta}"
        )
        return
    try:
        historial = cargar_auditoria(ruta)
    except ErrorArchivo as exc:
        auditoria.registrar(FALLO, "Registro de auditoría: lectura", str(exc))
        return

    faltantes = [c for c in COLUMNAS_AUDITORIA if c not in historial.columns]
    if faltantes:
        auditoria.registrar(
            FALLO,
            "Registro de auditoría: columnas",
            f"Faltan: {', '.join(faltantes)}.",
        )
        return

    auditoria.registrar(
        OK,
        "Registro de auditoría: columnas",
        f"{len(historial)} ejecución(es) registrada(s).",
    )

    ids = historial["ID_EJECUCION"].astype(str).str.strip()
    if (ids == "").any():
        auditoria.registrar(
            FALLO, "Registro de auditoría: IDs", "Hay ejecuciones sin ID."
        )
    elif ids.duplicated().any():
        auditoria.registrar(
            FALLO,
            "Registro de auditoría: IDs",
            f"{int(ids.duplicated().sum())} ID(s) duplicado(s).",
        )
    else:
        auditoria.registrar(
            OK, "Registro de auditoría: IDs", f"{len(ids)} IDs únicos."
        )

    tipos = set(historial["TIPO"].astype(str).str.strip()) - {""}
    auditoria.registrar(
        OK,
        "Registro de auditoría: tipos de evento",
        ", ".join(sorted(tipos)) or "sin eventos.",
    )


def auditar_historial_tickets(auditoria: Auditoria, ruta: Path) -> None:
    if not ruta.is_file():
        auditoria.registrar(
            OMITIDO, "Historial de cambios del maestro", f"Aún no existe: {ruta}"
        )
        return
    try:
        historial = pd.read_csv(ruta, encoding="utf-8-sig")
    except OSError as exc:
        auditoria.registrar(FALLO, "Historial de cambios: lectura", str(exc))
        return
    requeridas = {
        "FECHA",
        "ID_EJECUCION",
        "ID_TECNICO",
        "CAMPO",
        "VALOR_ANTERIOR",
        "VALOR_NUEVO",
    }
    faltantes = requeridas - set(historial.columns)
    if faltantes:
        auditoria.registrar(
            FALLO,
            "Historial de cambios: columnas",
            f"Faltan: {', '.join(sorted(faltantes))}.",
        )
    else:
        auditoria.registrar(
            OK,
            "Historial de cambios: columnas",
            f"{len(historial)} cambio(s) registrado(s).",
        )


# ---------------------------------------------------------------------------
# Principal
# ---------------------------------------------------------------------------
def _ultimo_procesado() -> Path:
    candidatos = sorted(
        cfg.OUTPUT_DIR.glob("*_procesado.xlsx"),
        key=lambda ruta: ruta.stat().st_mtime,
        reverse=True,
    )
    if candidatos:
        return candidatos[0]
    return cfg.OUTPUT_DIR / "consulta_septiembre_2026_procesado.xlsx"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Auditoría técnica del sistema.")
    parser.add_argument("--maestro", help="Excel de correspondencias USUARIO→ÁREA.")
    parser.add_argument("--maestro-tickets", help="Excel maestro histórico de tickets.")
    parser.add_argument("--procesado", help="Excel procesado a auditar.")
    parser.add_argument("--manifiesto", help="Manifiesto del Excel procesado.")
    parser.add_argument("--corregido", help="Excel corregido a comparar.")
    parser.add_argument("--auditoria-csv", help="CSV de auditoría de ejecuciones.")
    parser.add_argument("--historial", help="CSV de cambios del maestro.")
    parser.add_argument(
        "--sin-procesado",
        action="store_true",
        help="No auditar ningún Excel procesado.",
    )
    args = parser.parse_args(argv)

    cfg.asegurar_directorios()

    ruta_maestro = cfg.resolver_ruta(args.maestro, cfg.MAESTRO_XLSX)
    ruta_maestro_tickets = cfg.resolver_ruta(
        args.maestro_tickets, cfg.MAESTRO_TICKETS_XLSX
    )
    procesado = (
        None
        if args.sin_procesado
        else cfg.resolver_ruta(args.procesado, _ultimo_procesado())
    )
    manifiesto = (
        cfg.resolver_ruta(args.manifiesto)
        if args.manifiesto
        else (
            procesado.with_name(f"{procesado.stem}{cfg.SUFIJO_MANIFIESTO}")
            if procesado is not None
            else None
        )
    )
    corregido = cfg.resolver_ruta(args.corregido) if args.corregido else None
    auditoria_csv = cfg.resolver_ruta(args.auditoria_csv, cfg.AUDITORIA_CSV)
    historial = cfg.resolver_ruta(args.historial, cfg.HISTORIAL_TICKETS_CSV)

    auditoria = Auditoria()
    auditar_maestro_areas(
        auditoria, ruta_maestro, ruta_maestro.with_suffix(".csv")
    )
    auditar_maestro_tickets(
        auditoria,
        ruta_maestro_tickets,
        ruta_maestro_tickets.with_suffix(".csv"),
    )
    if procesado is not None:
        auditar_procesado(auditoria, procesado, manifiesto)
    if corregido is not None:
        auditar_corregido(auditoria, corregido)
    auditar_registro(auditoria, auditoria_csv)
    auditar_historial_tickets(auditoria, historial)

    auditoria.imprimir()
    return 1 if auditoria.fallos else 0


if __name__ == "__main__":
    sys.exit(main())
