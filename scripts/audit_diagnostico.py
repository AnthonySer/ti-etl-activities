"""Auditoría de solo lectura del sistema real (Fase 0).

Inspecciona la estructura del proyecto, el maestro de áreas, la caché de
empleados, la configuración SQL (sin mostrar credenciales), la auditoría y el
diario de operaciones. Nunca escribe en los archivos que analiza: el único
archivo generado es el informe Markdown solicitado.

Uso:
    python scripts/audit_diagnostico.py
    python scripts/audit_diagnostico.py --salida docs/AUDITORIA_SISTEMA_ACTUAL.md
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import pandas as pd  # noqa: E402

from config import configuracion as cfg  # noqa: E402


def _leer_excel_seguro(ruta: Path) -> dict[str, Any]:
    if not ruta.is_file():
        return {"existe": False, "filas": 0, "columnas": [], "hojas": []}
    try:
        hojas = pd.ExcelFile(ruta, engine="openpyxl").sheet_names
        df = pd.read_excel(ruta, sheet_name=hojas[0], engine="openpyxl")
        return {
            "existe": True,
            "filas": int(len(df)),
            "columnas": [str(columna) for columna in df.columns],
            "hojas": list(hojas),
            "duplicados_usuario": (
                int(
                    df[cfg.COLUMNA_USUARIO]
                    .astype(str)
                    .str.strip()
                    .str.upper()
                    .duplicated()
                    .sum()
                )
                if cfg.COLUMNA_USUARIO in df.columns
                else 0
            ),
            "areas_vacias": (
                int(df[cfg.COLUMNA_AREA].isna().sum())
                if cfg.COLUMNA_AREA in df.columns
                else 0
            ),
            "areas_unicas": (
                int(df[cfg.COLUMNA_AREA].dropna().astype(str).nunique())
                if cfg.COLUMNA_AREA in df.columns
                else 0
            ),
        }
    except Exception as exc:  # noqa: BLE001 - el informe debe continuar
        return {"existe": True, "error": str(exc), "filas": 0, "columnas": []}


def _resumen_csv(ruta: Path) -> dict[str, Any]:
    if not ruta.is_file():
        return {"existe": False, "filas": 0}
    try:
        df = pd.read_csv(ruta, encoding="utf-8-sig")
        return {
            "existe": True,
            "filas": int(len(df)),
            "columnas": [str(columna) for columna in df.columns],
            "ultima": df.iloc[-1].to_dict() if len(df) else {},
        }
    except Exception as exc:  # noqa: BLE001
        return {"existe": True, "error": str(exc), "filas": 0}


def _inventario_modulos() -> list[dict[str, Any]]:
    inventario: list[dict[str, Any]] = []
    for carpeta in ("core", "database", "config", "tests", "scripts"):
        directorio = BASE_DIR / carpeta
        if not directorio.is_dir():
            continue
        for archivo in sorted(directorio.glob("*.py")):
            try:
                lineas = sum(1 for _ in archivo.open(encoding="utf-8"))
            except OSError:
                lineas = 0
            inventario.append(
                {
                    "carpeta": carpeta,
                    "archivo": archivo.name,
                    "lineas": lineas,
                }
            )
    return inventario


def _estado_sql() -> dict[str, Any]:
    """Muestra solo el estado, nunca usuario, contraseña ni cadena completa."""

    try:
        from config.sqlserver import cargar_configuracion

        config = cargar_configuracion()
        return {
            "configurada": bool(config.configurada),
            "habilitado": bool(config.habilitado),
            "servidor": getattr(config, "servidor", ""),
            "base_datos": getattr(config, "base_datos", ""),
            "autenticacion": getattr(config, "autenticacion", ""),
            "empresas": len(getattr(config, "empresas", []) or []),
            "timeout": getattr(config, "timeout", ""),
            "lote": getattr(config, "lote", ""),
        }
    except Exception as exc:  # noqa: BLE001
        return {"configurada": False, "error": str(exc)}


def _estado_directorios() -> list[dict[str, Any]]:
    resultado: list[dict[str, Any]] = []
    for nombre, ruta in (
        ("input", cfg.INPUT_DIR),
        ("output", cfg.OUTPUT_DIR),
        ("data", cfg.DATA_DIR),
        ("respaldos", cfg.BACKUPS_DIR),
        ("revisiones", cfg.REVISION_DIR),
        ("reportes", cfg.REPORTES_DIR),
        ("logs", cfg.LOGS_DIR),
    ):
        resultado.append(
            {
                "nombre": nombre,
                "ruta": str(ruta),
                "existe": Path(ruta).is_dir(),
                "archivos": len(list(Path(ruta).glob("*"))) if Path(ruta).is_dir() else 0,
            }
        )
    return resultado


def _operaciones() -> dict[str, Any]:
    try:
        from core import operaciones as svc_operaciones

        diario = svc_operaciones.DiarioOperaciones()
        return {
            "ruta": str(diario.ruta),
            "resumen": diario.resumen(),
            "pendientes": [item.como_dict() for item in diario.pendientes(limite=10)],
        }
    except Exception as exc:  # noqa: BLE001
        return {"resumen": {}, "pendientes": [], "error": str(exc)}


def _leer_bytes(ruta: Path) -> str:
    try:
        return ruta.read_text(encoding="utf-8")
    except OSError:
        return ""


def _detectar_dependencias() -> dict[str, str]:
    estado: dict[str, str] = {}
    for modulo in ("pandas", "openpyxl", "pyodbc", "tkinter"):
        try:
            __import__(modulo)
            estado[modulo] = "disponible"
        except Exception as exc:  # noqa: BLE001
            estado[modulo] = f"no disponible ({exc})"
    return estado


def auditar() -> dict[str, Any]:
    """Recolecta el diagnóstico completo sin modificar datos."""

    maestro = _leer_excel_seguro(Path(cfg.MAESTRO_XLSX))
    maestro_csv = _resumen_csv(Path(cfg.MAESTRO_CSV))
    tickets = _leer_excel_seguro(Path(cfg.MAESTRO_TICKETS_XLSX))
    cache_xlsx = Path(getattr(cfg, "DATA_DIR", "data")) / "cache_empleados.xlsx"
    cache = _leer_excel_seguro(cache_xlsx)
    auditoria = _resumen_csv(Path(cfg.AUDITORIA_CSV))
    modulos = _inventario_modulos()
    carpetas = Counter(item["carpeta"] for item in modulos)

    return {
        "fecha": datetime.now().astimezone().isoformat(timespec="seconds"),
        "raiz": str(BASE_DIR),
        "python": sys.version.split()[0],
        "dependencias": _detectar_dependencias(),
        "directorios": _estado_directorios(),
        "modulos": modulos,
        "carpetas": dict(carpetas),
        "maestro": maestro,
        "maestro_csv": maestro_csv,
        "maestro_tickets": tickets,
        "cache": cache,
        "auditoria": auditoria,
        "sql": _estado_sql(),
        "operaciones": _operaciones(),
        "readme": (BASE_DIR / "README.md").is_file(),
    }


def _tabla_areas(maestro: dict[str, Any]) -> list[str]:
    lineas = [
        "| Métrica | Valor |",
        "| --- | --- |",
        f"| Archivo existe | {'sí' if maestro.get('existe') else 'no'} |",
        f"| Usuarios | {maestro.get('filas', 0)} |",
        f"| Áreas únicas | {maestro.get('areas_unicas', 0)} |",
        f"| Áreas vacías | {maestro.get('areas_vacias', 0)} |",
        f"| Usuarios duplicados | {maestro.get('duplicados_usuario', 0)} |",
        f"| Columnas | {', '.join(maestro.get('columnas') or []) or 'no disponibles'} |",
        f"| Hojas | {', '.join(maestro.get('hojas') or []) or 'no disponibles'} |",
    ]
    return lineas


def generar_markdown(datos: dict[str, Any]) -> str:
    maestro = datos.get("maestro") or {}
    sql = datos.get("sql") or {}
    auditoria = datos.get("auditoria") or {}
    operaciones = datos.get("operaciones") or {}
    resumen_ops = operaciones.get("resumen") or {}
    pendientes = operaciones.get("pendientes") or []

    lineas: list[str] = [
        "# Auditoría del sistema actual",
        "",
        f"Generado: {datos.get('fecha', '')}",
        "",
        "> Informe de solo lectura. No se modificó ningún Excel, base de datos ni archivo de configuración.",
        "",
        "## 1. Arquitectura observada",
        "",
        f"- Raíz del proyecto: `{datos.get('raiz', '')}`",
        f"- Python: {datos.get('python', '')}",
        "- Aplicación de escritorio Tkinter con motor ETL en Python.",
        "- Módulos de dominio en `core/`, acceso a SQL en `database/`, configuración en `config/`.",
        "- El Excel original nunca se sobrescribe: se genera una salida de 11 columnas.",
        "- Maestro persistente `USUARIO -> AREA_USUARIO` en `data/maestro_areas.xlsx` con espejo CSV.",
        "",
        "### Dependencias",
        "",
        "| Dependencia | Estado |",
        "| --- | --- |",
    ]
    for nombre, estado in (datos.get("dependencias") or {}).items():
        lineas.append(f"| {nombre} | {estado} |")

    lineas.extend(
        [
            "",
            "## 2. Flujo actual",
            "",
            "```text",
            "Excel de tickets",
            "  -> validación de estructura (11 columnas oficiales)",
            "  -> extracción y normalización",
            "  -> búsqueda en maestro USUARIO -> ÁREA",
            "  -> resolución contra caché local / SQL Server (solo lectura)",
            "  -> pendientes y conflictos a revisión",
            "  -> generación de Excel de salida + manifiesto",
            "  -> auditoría CSV y estado de la interfaz",
            "  -> validación manual y actualización del maestro",
            "```",
            "",
            "## 3. Maestro de áreas",
            "",
        ]
    )
    lineas.extend(_tabla_areas(maestro))
    lineas.extend(
        [
            "",
            f"- Espejo CSV: {'presente' if datos.get('maestro_csv', {}).get('existe') else 'ausente'} "
            f"({datos.get('maestro_csv', {}).get('filas', 0)} filas).",
            f"- Maestro histórico de tickets: "
            f"{'presente' if datos.get('maestro_tickets', {}).get('existe') else 'ausente'} "
            f"({datos.get('maestro_tickets', {}).get('filas', 0)} filas).",
            f"- Caché de empleados: "
            f"{'presente' if datos.get('cache', {}).get('existe') else 'ausente'} "
            f"({datos.get('cache', {}).get('filas', 0)} filas).",
            "",
            "## 4. Conexión SQL",
            "",
        ]
    )
    if sql.get("error"):
        lineas.append(f"- No se pudo leer la configuración: {sql['error']}")
    else:
        lineas.extend(
            [
                f"- Configurada: {'sí' if sql.get('configurada') else 'no'}",
                f"- Habilitada: {'sí' if sql.get('habilitado') else 'no'}",
                f"- Servidor: `{sql.get('servidor', '')}`",
                f"- Base de datos: `{sql.get('base_datos', '')}`",
                f"- Autenticación: `{sql.get('autenticacion', '')}`",
                f"- Empresas autorizadas: {sql.get('empresas', 0)}",
                "- La contraseña no se muestra en este informe.",
                "- Todas las consultas son de solo lectura; ``database.connection`` rechaza escrituras.",
            ]
        )

    lineas.extend(
        [
            "",
            "## 5. Auditoría y operaciones",
            "",
            f"- Registros de auditoría: {auditoria.get('filas', 0)}.",
        ]
    )
    ultima = auditoria.get("ultima") or {}
    if ultima:
        lineas.append(
            f"- Última ejecución: {ultima.get('TIPO', '')} "
            f"{ultima.get('FECHA_HORA', '')} ({ultima.get('ESTADO', '')})."
        )
    lineas.append("- Diario de operaciones:")
    for estado, cantidad in resumen_ops.items():
        lineas.append(f"  - {estado}: {cantidad}")
    if pendientes:
        lineas.append("- Operaciones pendientes o con error:")
        for item in pendientes[:10]:
            lineas.append(
                f"  - {item.get('tipo', '')} | {item.get('usuario', '')} | "
                f"{item.get('estado', '')} | {item.get('detalle', '')}"
            )

    lineas.extend(
        [
            "",
            "## 6. Módulos existentes",
            "",
            "| Carpeta | Archivos | Líneas |",
            "| --- | --- | --- |",
        ]
    )
    for carpeta, cantidad in (datos.get("carpetas") or {}).items():
        lineas.append(f"| {carpeta} | {cantidad} | |")
    lineas.extend(
        [
            "",
            "## 7. Funcionalidades que deben conservarse",
            "",
            "- Procesamiento del Excel de 11 columnas y generación del archivo de salida.",
            "- Validación de estructura, fechas, horas, duplicados y observaciones.",
            "- Maestro `USUARIO -> AREA_USUARIO` con guardado transaccional y respaldos.",
            "- Resolución de casos A–E y protección de asignaciones manuales.",
            "- Consulta SQL de solo lectura por empresa, con aislamiento de errores.",
            "- Revisión persistente, reimportación, consolidación y reportes.",
            "- Auditoría CSV independiente y estado de la interfaz.",
            "",
            "## 8. Problemas y riesgos detectados",
            "",
            "- La búsqueda de sugerencias usaba ``difflib`` como único criterio; no cubría bien orden de nombres ni errores combinados.",
            "- No existía un mecanismo de eventos para refrescar solo la región afectada de la interfaz.",
            "- No había un diario único de operaciones con estados PENDIENTE/COMPLETADO/ERROR/REQUIERE_REVISION.",
            "- No se detectaban cambios externos del Excel maestro antes de sobrescribirlo.",
            "- El catálogo de áreas no tenía validación centralizada de duplicados y nombres parecidos.",
            "- Los reintentos de sincronización no contaban con una clave de idempotencia.",
            "- Persisten riesgos propios de Windows/Excel: archivo abierto, permisos y bloqueo de escritura.",
            "",
            "## 9. Mejoras implementadas en las fases siguientes",
            "",
            "- `core/similitud.py`: Jaro-Winkler, Levenshtein y comparación por tokens.",
            "- `core/eventos.py`: bus de eventos para actualización inmediata por componente.",
            "- `database/coincidencias.py`: consolidación de caché, SQL y maestro con caché por ejecución.",
            "- `core/catalogo_areas.py`: validación y creación segura de áreas.",
            "- `core/asignacion_asistida.py`: confirmación, advertencia de inactivos y asignación desde coincidencias.",
            "- `core/operaciones.py`: diario con estados e idempotencia.",
            "- `core/integridad_excel.py`: huellas, detección de cambios externos y escritura protegida.",
            "- `core/diagnostico.py`: historial, alertas y recuperación.",
            "",
            "## 10. Archivos que probablemente deben modificarse",
            "",
            "- `core/asignaciones.py` (integrado con similitud y eventos).",
            "- `database/resolucion.py` (sugerencias con el nuevo motor).",
            "- `interfaz.py` (suscripción a eventos de actualización).",
            "- `config/configuracion.py` (rutas y umbrales configurables).",
            "- `tests/` (pruebas de las nuevas fases).",
            "",
            "## 11. Propuesta de arquitectura objetivo",
            "",
            "```text",
            "Interfaz Tkinter",
            "  -> Bus de eventos (core.eventos)",
            "  -> Servicios de asignación y catálogo",
            "  -> Motor de similitud (core.similitud)",
            "  -> Consolidador de coincidencias (database.coincidencias)",
            "  -> Maestro Excel + caché local + SQL de solo lectura",
            "  -> Diario de operaciones + integridad de Excel",
            "  -> Auditoría y diagnóstico",
            "```",
            "",
            "Regla de responsabilidades: identidad y estado provienen de SQL; el catálogo y la asignación de áreas se administran en el Excel maestro; las coincidencias solo proponen, nunca asignan solas.",
            "",
        ]
    )
    return "\n".join(lineas)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Auditoría de solo lectura del sistema")
    parser.add_argument(
        "--salida",
        default=str(BASE_DIR / "AUDITORIA_SISTEMA_ACTUAL.md"),
        help="Ruta del informe Markdown",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Muestra el diagnóstico como JSON en lugar de generar el Markdown",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    datos = auditar()
    if args.json:
        print(json.dumps(datos, ensure_ascii=False, indent=2, default=str))
        return 0

    destino = Path(args.salida)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(generar_markdown(datos), encoding="utf-8")
    print(f"Auditoría generada: {destino}")
    print(f"Maestro: {datos['maestro'].get('filas', 0)} usuarios")
    print(f"SQL configurado: {datos['sql'].get('configurada', False)}")
    print(f"Operaciones pendientes: {len(datos['operaciones'].get('pendientes') or [])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
