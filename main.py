"""Punto de entrada del Sistema Integral de Tickets TI - INKACROPS.

Sin argumentos abre la interfaz gráfica. Con un archivo como argumento ejecuta
el flujo completo en modo consola (procesamiento, reporte opcional y
consolidación opcional). El archivo original nunca se modifica.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

from config import configuracion as cfg
from config.logging_config import configurar_consola, configurar_logging
from core import estado as svc_estado
from core.archivos import ErrorArchivo, ErrorEstructura, leer_excel, localizar_archivo
from core.asignaciones import cargar_maestro
from core.bloqueo import ErrorBloqueo, bloqueo
from core.consolidacion_tickets import consolidar_procesado
from core.procesador import EstadisticasProcesamiento, procesar_archivo
from core.reportes import generar_reporte

LOGGER = logging.getLogger("automatizador")


def _resolver_entrada(valor: str) -> Path:
    ruta = cfg.resolver_entrada(valor)
    if ruta.exists():
        return ruta
    encontrado = localizar_archivo(Path(valor).name)
    if encontrado is not None:
        return encontrado
    raise ErrorArchivo(f"No existe el archivo de entrada: {valor}")


def _imprimir_resumen(estadisticas: EstadisticasProcesamiento) -> None:
    print()
    print("=" * 64)
    print("  SISTEMA INTEGRAL DE TICKETS TI - INKACROPS")
    print("  Procesamiento de tickets (11 columnas oficiales)")
    print("=" * 64)
    print(f"Archivo de entrada : {estadisticas.archivo_entrada}")
    print(f"Hoja procesada     : {estadisticas.hoja}")
    print(f"Periodo detectado  : {estadisticas.periodo or 'no determinado'}")
    print(f"ID de ejecución    : {estadisticas.id_ejecucion}")
    print()
    print(f"Registros leídos   : {estadisticas.registros_totales}")
    print(f"Registros válidos  : {estadisticas.registros_validos}")
    print(f"Observados         : {estadisticas.registros_observados}")
    print(f"Pendientes de área : {estadisticas.registros_pendientes}")
    print(f"Duplicados         : {estadisticas.registros_duplicados}")
    print(f"Errores            : {estadisticas.registros_error}")
    print(f"Horas registradas  : {estadisticas.total_horas}")
    print(f"Observaciones      : {estadisticas.observaciones}")
    print()
    print(f"Solicitantes únicos: {estadisticas.usuarios_unicos}")
    print(f"En base de áreas   : {estadisticas.usuarios_en_maestro}")
    print(f"Áreas asignadas    : {estadisticas.areas_asignadas}")
    print(f"Pendientes         : {estadisticas.usuarios_pendientes} usuario(s)")
    print()
    print("Archivo generado   :")
    print(f"  {cfg.ruta_visible(Path(estadisticas.archivo_salida))}")
    if estadisticas.archivo_detalle:
        print("Excel a detalle    :")
        print(f"  {cfg.ruta_visible(Path(estadisticas.archivo_detalle))}")
    if estadisticas.ruta_copia_revision:
        print("Copia de revisión  :")
        print(f"  {cfg.ruta_visible(Path(estadisticas.ruta_copia_revision))}")
    if estadisticas.archivo_manifiesto:
        print("Manifiesto         :")
        print(f"  {cfg.ruta_visible(Path(estadisticas.archivo_manifiesto))}")
    print(f"Modo de generación : {estadisticas.modo_generacion}")
    print(f"Generación parcial : {'Sí' if estadisticas.generacion_parcial else 'No'}")
    print()
    print("El detalle conserva exactamente las 11 columnas oficiales.")
    print("Las observaciones, el resumen y el control están en hojas auxiliares.")
    print("=" * 64)


def _reporte_desde(archivo: Path) -> dict:
    from core.archivos import listar_hojas

    hojas = listar_hojas(archivo)
    if cfg.HOJA_CONSULTA in hojas:
        hoja = cfg.HOJA_CONSULTA
    elif cfg.HOJA_DETALLE in hojas:
        hoja = cfg.HOJA_DETALLE
    else:
        hoja = hojas[0] if hojas else cfg.HOJA_DETALLE
    df = leer_excel(archivo, sheet_name=hoja)
    marca = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    return generar_reporte(
        df,
        ruta_salida=cfg.REPORTES_DIR / f"reporte_tickets_{marca}.xlsx",
    )


def ejecutar_consola(args: argparse.Namespace) -> int:
    ruta_entrada = _resolver_entrada(args.archivo)
    maestro = cargar_maestro(args.maestro)

    try:
        with bloqueo("procesamiento desde consola"):
            estadisticas = procesar_archivo(
                ruta_entrada,
                maestro,
                ruta_salida=args.salida,
                hoja=args.hoja,
                sobrescribir=args.sobrescribir,
                formato_fecha=args.formato_fecha,
                usuario_ejecutor=args.usuario or "",
                modo=args.modo,
                permitir_pendientes=args.permitir_pendientes,
            )
    except ErrorArchivo as exc:
        if "Excel completo" in str(exc):
            print()
            print("GENERACIÓN CANCELADA", file=sys.stderr)
            print(f"  {exc}", file=sys.stderr)
            print(
                "  Si desea un archivo parcial, repita el comando con "
                "--permitir-pendientes.",
                file=sys.stderr,
            )
            return 2
        raise

    _imprimir_resumen(estadisticas)
    svc_estado.actualizar_desde_estadisticas(
        estadisticas,
        mensajes=[
            f"{estadisticas.archivo_entrada}: "
            f"{estadisticas.registros_totales} registros procesados."
        ],
    )

    if args.reporte:
        resultado = _reporte_desde(Path(estadisticas.archivo_salida))
        svc_estado.registrar_reporte(resultado["xlsx"])
        print()
        print("Reporte generado:")
        print(f"  Excel    : {cfg.ruta_visible(resultado['xlsx'])}")
        print(f"  Markdown : {cfg.ruta_visible(resultado['markdown'])}")
        for clave, valor in resultado["indicadores"].items():
            if clave == "Nota":
                continue
            print(f"  {clave}: {valor}")

    if args.consolidar:
        if args.maestro_tickets:
            ruta_maestro = cfg.resolver_ruta(args.maestro_tickets)
        else:
            ruta_maestro = None
        salida = consolidar_procesado(
            estadisticas.archivo_salida,
            ruta_maestro=ruta_maestro,
            usuario_ejecutor=args.usuario or "",
        )
        svc_estado.registrar_consolidacion(salida)
        print()
        print("Consolidación en el maestro histórico:")
        print(f"  Nuevos              : {salida['nuevos']}")
        print(f"  Actualizados        : {salida['actualizados']}")
        print(f"  Sin cambios         : {salida['sin_cambios']}")
        print(f"  Duplicados omitidos : {salida['duplicados']}")
        print(f"  Total en maestro    : {salida['total_maestro']}")
        print(f"  Maestro             : {cfg.ruta_visible(Path(cfg.MAESTRO_TICKETS_XLSX))}")

    return 0


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Sistema Integral de Tickets TI - INKACROPS. "
            "Sin argumentos abre la interfaz gráfica."
        )
    )
    parser.add_argument(
        "--modo",
        choices=[cfg.MODO_NORMAL, cfg.MODO_DETALLE, cfg.MODO_LEGACY],
        default=cfg.MODO_NORMAL,
        help=(
            "Formato de salida: normal (una hoja Consulta), detalle (cinco hojas "
            "y actualización del canónico) o legacy (formato histórico)."
        ),
    )
    parser.add_argument(
        "--permitir-pendientes",
        action="store_true",
        help=(
            "Permite generar el Excel aunque existan registros sin área validada. "
            "Sin esta opción, la generación se cancela y se informa el problema."
        ),
    )
    parser.add_argument(
        "archivo",
        nargs="?",
        help="Excel de entrada (.xlsx). Si se omite, se abre la interfaz.",
    )
    parser.add_argument("--interfaz", action="store_true", help="Abrir la interfaz gráfica.")
    parser.add_argument("--hoja", help="Hoja a procesar (por defecto se detecta).")
    parser.add_argument("--salida", help="Ruta del Excel procesado de salida.")
    parser.add_argument("--maestro", help="Ruta alternativa de la base USUARIO→ÁREA.")
    parser.add_argument(
        "--maestro-tickets",
        help="Ruta alternativa del maestro histórico de tickets.",
    )
    parser.add_argument(
        "--sobrescribir",
        action="store_true",
        help="Sobrescribe la salida si existe (por defecto crea una versión nueva).",
    )
    parser.add_argument(
        "--formato-fecha",
        default=cfg.FORMATO_FECHA_PREFERIDO,
        choices=["DD/MM/YYYY", "MM/DD/YYYY", "YYYY-MM-DD"],
        help="Interpretación explícita de fechas ambiguas.",
    )
    parser.add_argument(
        "--reporte", action="store_true", help="Generar reporte Excel y Markdown."
    )
    parser.add_argument(
        "--consolidar",
        action="store_true",
        help="Incorporar el resultado al maestro histórico de tickets.",
    )
    parser.add_argument("--usuario", help="Nombre del operador para la auditoría.")
    parser.add_argument(
        "--verbose", action="store_true", help="Mostrar detalle de depuración."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    configurar_consola()
    parser = construir_parser()
    args = parser.parse_args(argv)

    logger = configurar_logging()
    cfg.asegurar_directorios()

    if args.interfaz or not args.archivo:
        try:
            from interfaz import main as main_interfaz

            return main_interfaz(argv)
        except ImportError as exc:  # pragma: no cover
            print(f"No se pudo cargar la interfaz gráfica: {exc}", file=sys.stderr)
            return 1

    try:
        return ejecutar_consola(args)
    except (ErrorArchivo, ErrorEstructura, ErrorBloqueo) as exc:
        logger.error("%s", exc)
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - red de seguridad
        logger.exception("Error inesperado durante el procesamiento")
        print(f"\nERROR inesperado: {exc}", file=sys.stderr)
        if args.verbose:
            raise
        return 1


if __name__ == "__main__":
    sys.exit(main())
