"""Construye la base maestra inicial a partir de agosto y septiembre de 2026."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from config import configuracion as cfg
from config.logging_config import configurar_consola, configurar_logging
from core.archivos import ErrorArchivo, leer_hoja, respaldar_archivo
from core.consolidacion import consolidar, localizar_fuentes
from core.maestro import ErrorMaestro, Maestro

LOGGER = logging.getLogger("automatizador")


def _destino_versionado(destino: Path) -> Path:
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidato = destino.with_name(f"{destino.stem}_v{marca}{destino.suffix}")
    contador = 2
    while candidato.exists():
        candidato = destino.with_name(
            f"{destino.stem}_v{marca}_{contador}{destino.suffix}"
        )
        contador += 1
    return candidato


def _resolver_destino(
    salida: str | None,
    sobrescribir: bool,
    nueva_version: bool,
) -> tuple[Path, bool]:
    """Devuelve (ruta_destino, reemplaza_existente)."""

    destino = cfg.resolver_ruta(salida, cfg.MAESTRO_XLSX)

    if not destino.exists():
        return destino, False
    if sobrescribir:
        return destino, True
    if nueva_version:
        return _destino_versionado(destino), False

    if not sys.stdin.isatty():
        print(
            "Ya existe una base maestra y la sesión no es interactiva: "
            "se generará una nueva versión sin reemplazarla."
        )
        return _destino_versionado(destino), False

    print()
    print("Ya existe una base maestra:")
    print(f"  {cfg.ruta_visible(destino)}")
    print()
    print("¿Qué desea hacer?")
    print("  1) Reemplazarla (se crea una copia de seguridad antes)")
    print("  2) Generar una nueva versión con marca de tiempo (recomendado)")
    print("  3) Cancelar")
    print()

    while True:
        try:
            opcion = input("Seleccione una opción [2]: ").strip() or "2"
        except (EOFError, KeyboardInterrupt):
            print()
            print(
                "No se recibió confirmación: se generará una nueva versión "
                "sin reemplazar la base maestra existente."
            )
            return _destino_versionado(destino), False
        if opcion == "1":
            return destino, True
        if opcion == "2":
            return _destino_versionado(destino), False
        if opcion == "3":
            print("Operación cancelada. La base maestra existente no se modificó.")
            raise SystemExit(0)
        print("Opción no válida. Ingrese 1, 2 o 3.")


def _imprimir_resumen(
    ruta_septiembre: Path,
    ruta_agosto: Path,
    estadisticas,
    destino: Path,
    total_maestro: int,
    respaldo: Path | None,
) -> None:
    print()
    print("========================================")
    print("    CONSTRUCCIÓN DE BASE MAESTRA")
    print("========================================")
    print()
    print(f"Archivo 1: {ruta_septiembre.name}")
    print(f"Archivo 2: {ruta_agosto.name}")
    print()
    print(f"Registros leídos de septiembre: {estadisticas.registros_septiembre}")
    print(f"Registros leídos de agosto: {estadisticas.registros_agosto}")
    print()
    print(f"Usuarios únicos identificados: {estadisticas.usuarios_unicos}")
    print(f"Correspondencias válidas: {estadisticas.correspondencias_validas}")
    print(f"Usuarios sin área válida: {estadisticas.usuarios_sin_area}")
    print(f"Duplicados eliminados: {estadisticas.duplicados_eliminados}")
    print(f"Conflictos detectados: {estadisticas.conflictos_detectados}")
    print(f"Conflictos resueltos por prioridad: {estadisticas.conflictos_resueltos}")
    print()
    print("Detalle de validación:")
    print(
        f"  Filas sin usuario válido: {estadisticas.filas_usuario_invalido}"
    )
    print(f"  Filas sin área válida: {estadisticas.filas_area_invalida}")
    print(
        "  Conflictos internos en una misma fuente: "
        f"{estadisticas.conflictos_internos}"
    )
    print(
        "  Correspondencias válidas duplicadas consolidadas: "
        f"{estadisticas.duplicados_validos}"
    )
    print(f"  Total de correspondencias en el maestro: {total_maestro}")
    if respaldo is not None:
        print(f"  Copia de seguridad: {cfg.ruta_visible(respaldo)}")
    print()
    print("Base maestra generada correctamente:")
    print(cfg.ruta_visible(destino))
    print("========================================")


def main(argv: list[str] | None = None) -> int:
    configurar_consola()
    logger = configurar_logging()

    parser = argparse.ArgumentParser(
        description=(
            "Construye data/maestro_areas.xlsx consolidando "
            "consulta_septiembre_2026.xlsx y consulta_agosto_2026.xlsx."
        )
    )
    parser.add_argument("--septiembre", help="Ruta del archivo de septiembre")
    parser.add_argument("--agosto", help="Ruta del archivo de agosto")
    parser.add_argument("--salida", help="Ruta del maestro a generar")
    grupo = parser.add_mutually_exclusive_group()
    grupo.add_argument(
        "--sobrescribir",
        action="store_true",
        help="Reemplaza el maestro existente (crea copia de seguridad).",
    )
    grupo.add_argument(
        "--nueva-version",
        action="store_true",
        help="Genera una nueva versión con marca de tiempo sin reemplazar.",
    )
    parser.add_argument(
        "--detalle",
        action="store_true",
        help="Muestra en consola el detalle de conflictos e incidencias.",
    )
    args = parser.parse_args(argv)

    try:
        ruta_septiembre, ruta_agosto = localizar_fuentes(
            args.septiembre,
            args.agosto,
        )
        LOGGER.info("Fuente septiembre: %s", ruta_septiembre)
        LOGGER.info("Fuente agosto: %s", ruta_agosto)

        df_septiembre, hoja_septiembre, _ = leer_hoja(ruta_septiembre)
        df_agosto, hoja_agosto, _ = leer_hoja(ruta_agosto)
        LOGGER.info("Hoja septiembre: %s", hoja_septiembre)
        LOGGER.info("Hoja agosto: %s", hoja_agosto)

        df_maestro, estadisticas = consolidar(
            df_septiembre,
            df_agosto,
            nombre_septiembre=ruta_septiembre.name,
            nombre_agosto=ruta_agosto.name,
        )

        if estadisticas.correspondencias_validas == 0:
            raise ErrorMaestro(
                "No se encontraron correspondencias válidas USUARIO/AREA_USUARIO "
                "en los archivos históricos."
            )

        maestro = Maestro(df_maestro)
        problemas = maestro.validar()
        if problemas:
            raise ErrorMaestro(
                "La base maestra no superó la validación: " + " ".join(problemas)
            )

        for incidencia in estadisticas.incidencias:
            logger.info("Incidencia: %s", incidencia)

        destino, reemplazar = _resolver_destino(
            args.salida,
            args.sobrescribir,
            args.nueva_version,
        )

        respaldo = None
        if reemplazar and destino.exists():
            respaldo = respaldar_archivo(destino)

        maestro.guardar(destino, crear_backup=False)
        logger.info(
            "Base maestra generada: %s (%s correspondencias)",
            destino,
            len(maestro),
        )

        _imprimir_resumen(
            ruta_septiembre,
            ruta_agosto,
            estadisticas,
            destino,
            len(maestro),
            respaldo,
        )

        if args.detalle and estadisticas.incidencias:
            print()
            print("Detalle de incidencias:")
            for incidencia in estadisticas.incidencias:
                print(f"  - {incidencia}")

        return 0

    except SystemExit:
        raise
    except (ErrorArchivo, ErrorMaestro) as exc:
        logger.error("%s", exc)
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - red de seguridad
        logger.exception("Error inesperado durante la construcción del maestro")
        print(f"\nERROR inesperado: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
