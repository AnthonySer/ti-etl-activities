"""Actualiza la base maestra con las correcciones manuales de un archivo revisado."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from config import configuracion as cfg
from config.logging_config import configurar_consola, configurar_logging
from core.archivos import ErrorArchivo, leer_hoja
from core.maestro import ErrorMaestro, Maestro, ResultadoActualizacion
from core.retroalimentacion import (
    buscar_manifiesto,
    cargar_historial,
    cargar_manifiesto,
    registrar_historial,
    ultimas_manuales,
)

LOGGER = logging.getLogger("automatizador")


def _resolver_entrada(valor: str) -> Path:
    ruta = cfg.resolver_entrada(valor)
    if not ruta.exists():
        raise ErrorArchivo(f"No existe el archivo revisado: {valor}")
    return ruta


def _imprimir_resumen(
    archivo_revisado: Path,
    resultado: ResultadoActualizacion,
    total_maestro: int,
    respaldos: list[Path],
    hubo_cambios: bool,
    error_historial: str | None = None,
) -> None:
    print()
    print("========================================")
    print("    ACTUALIZACIÓN DE BASE MAESTRA")
    print("========================================")
    print()
    print(f"Archivo revisado: {archivo_revisado.name}")
    if resultado.manifiesto_usado:
        print(f"Manifiesto usado: {cfg.ruta_visible(Path(resultado.manifiesto_usado))}")
    else:
        print(
            "Manifiesto usado: no encontrado "
            "(modo heredado: no se distingue automático de manual)"
        )
    print()
    print(f"Registros analizados: {resultado.registros_analizados}")
    print(f"Usuarios nuevos incorporados: {resultado.usuarios_nuevos}")
    print(
        "Correspondencias actualizadas: "
        f"{resultado.correspondencias_actualizadas}"
    )
    print(f"Correspondencias sin cambios: {resultado.sin_cambios}")
    print(f"Registros omitidos: {resultado.registros_omitidos}")
    print(f"  Áreas vacías ignoradas: {resultado.areas_vacias_ignoradas}")
    print(f"Duplicados detectados: {resultado.duplicados_detectados}")
    print(f"Conflictos detectados: {resultado.conflictos_detectados}")
    print(
        "Asignaciones automáticas ignoradas (no son correcciones): "
        f"{resultado.asignaciones_automaticas_ignoradas}"
    )
    print(
        "Registros antiguos ignorados por fecha: "
        f"{resultado.registros_antiguos_ignorados}"
    )
    print(f"Correcciones manuales aplicadas: {resultado.correcciones_manuales}")
    print()
    print("Detalle de validación:")
    print(f"  Total de correspondencias en el maestro: {total_maestro}")
    if respaldos:
        for respaldo in respaldos:
            print(f"  Copia de seguridad: {cfg.ruta_visible(respaldo)}")
    if not hubo_cambios:
        print("  No hubo cambios: no fue necesario reescribir la base maestra.")
    if error_historial:
        print(f"  ADVERTENCIA: no se pudo registrar el historial: {error_historial}")
    print()
    if hubo_cambios:
        print("Base maestra actualizada correctamente.")
    else:
        print("Base maestra revisada sin cambios.")
    print("========================================")


def main(argv: list[str] | None = None) -> int:
    configurar_consola()
    logger = configurar_logging()

    parser = argparse.ArgumentParser(
        description=(
            "Incorpora a la base maestra los usuarios y las áreas corregidas "
            "manualmente en un archivo procesado."
        )
    )
    parser.add_argument("revisado", help="Archivo Excel revisado manualmente")
    parser.add_argument("--maestro", help="Ruta alternativa de la base maestra")
    parser.add_argument(
        "--hoja",
        help="Hoja revisada a leer (por defecto se detecta la hoja principal)",
    )
    parser.add_argument(
        "--manifiesto",
        help=(
            "Ruta del manifiesto JSON generado al procesar el archivo. "
            "Si se omite, se busca junto al archivo revisado."
        ),
    )
    parser.add_argument(
        "--historial",
        help=(
            "Ruta alternativa del historial de correcciones "
            f"(por defecto {cfg.HISTORIAL_CSV})."
        ),
    )
    parser.add_argument(
        "--crear-si-no-existe",
        action="store_true",
        help="Crea la base maestra desde el archivo revisado si aún no existe.",
    )
    args = parser.parse_args(argv)

    try:
        ruta_revisado = _resolver_entrada(args.revisado)
        ruta_maestro = cfg.resolver_ruta(args.maestro, cfg.MAESTRO_XLSX)
        ruta_csv = ruta_maestro.with_suffix(".csv")
        existe_maestro = ruta_maestro.exists() or ruta_csv.exists()

        if not existe_maestro and not args.crear_si_no_existe:
            raise ErrorMaestro(
                "No existe la base maestra. Ejecute primero 'construir_maestro.py' "
                "o use --crear-si-no-existe."
            )

        if existe_maestro:
            maestro = Maestro.cargar(ruta_maestro, ruta_csv)
        else:
            maestro = Maestro()
            LOGGER.warning(
                "No existía la base maestra; se creará desde el archivo revisado."
            )

        df_revisado, hoja_revisada, _ = leer_hoja(ruta_revisado, hoja=args.hoja)
        LOGGER.info("Hoja revisada: %s", hoja_revisada)

        # Manifiesto: distingue asignaciones automáticas de correcciones manuales.
        if args.manifiesto:
            ruta_manifiesto = cfg.resolver_ruta(args.manifiesto)
            if not ruta_manifiesto.is_file():
                raise ErrorArchivo(
                    f"No existe el manifiesto indicado: {ruta_manifiesto}"
                )
        else:
            ruta_manifiesto = buscar_manifiesto(ruta_revisado)

        manifiesto = cargar_manifiesto(ruta_manifiesto)
        if manifiesto is None:
            LOGGER.warning(
                "No se encontró manifiesto para '%s'. Se usará el modo heredado: "
                "no será posible distinguir automáticos de correcciones manuales.",
                ruta_revisado.name,
            )
        else:
            LOGGER.info("Manifiesto usado: %s", manifiesto.ruta)

        # Fecha del archivo de origen: protege contra archivos antiguos.
        fecha_archivo_ns = ruta_revisado.stat().st_mtime_ns
        ruta_historial = cfg.resolver_ruta(args.historial, cfg.HISTORIAL_CSV)
        historial = cargar_historial(ruta_historial)
        ultimas = ultimas_manuales(historial)

        resultado = maestro.actualizar(
            df_revisado,
            manifiesto=manifiesto,
            fecha_archivo_ns=fecha_archivo_ns,
            ultimas_manuales=ultimas,
        )

        for detalle in resultado.detalles_nuevos:
            logger.info("Usuario nuevo: %s", detalle)
        for detalle in resultado.detalles_actualizados:
            logger.info("Correspondencia actualizada: %s", detalle)
        for detalle in resultado.detalles_conflictos:
            logger.warning("Conflicto en archivo revisado: %s", detalle)
        for detalle in resultado.detalles_antiguos:
            logger.warning("Registro antiguo ignorado: %s", detalle)

        hubo_cambios = bool(
            resultado.usuarios_nuevos or resultado.correspondencias_actualizadas
        )

        respaldos: list[Path] = []
        error_historial: str | None = None

        if hubo_cambios:
            guardado = maestro.guardar(
                ruta_maestro,
                ruta_csv,
                crear_backup=True,
            )
            respaldos = list(guardado.respaldos)
            logger.info(
                "Base maestra guardada: %s (%s correspondencias)",
                ruta_maestro,
                len(maestro),
            )

            try:
                registrar_historial(
                    resultado.cambios,
                    ruta_historial,
                    fuente=ruta_revisado.name,
                    fecha_archivo_ns=fecha_archivo_ns,
                )
            except ErrorArchivo as exc:
                error_historial = str(exc)
                LOGGER.error(
                    "La base maestra se actualizó, pero no se pudo registrar "
                    "el historial: %s",
                    exc,
                )
        else:
            logger.info("Sin cambios en la base maestra; no se reescribió.")

        _imprimir_resumen(
            ruta_revisado,
            resultado,
            len(maestro),
            respaldos,
            hubo_cambios,
            error_historial,
        )
        return 1 if error_historial else 0

    except (ErrorArchivo, ErrorMaestro) as exc:
        logger.error("%s", exc)
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - red de seguridad
        logger.exception("Error inesperado durante la actualización del maestro")
        print(f"\nERROR inesperado: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
