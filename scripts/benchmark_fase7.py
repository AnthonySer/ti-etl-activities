"""Mediciones de rendimiento para la Fase 7.

Genera un Excel temporal, mide importación, búsqueda exacta/aproximada,
consolidación de coincidencias, memoria y publicación de eventos. No usa
SQL Server ni escribe en las carpetas reales del sistema.
"""

from __future__ import annotations

import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core import eventos, similitud  # noqa: E402
from core.etl import extraer, previsualizar  # noqa: E402
from core.maestro import Maestro  # noqa: E402
from database import coincidencias  # noqa: E402
from database.repository import Empleado, ResultadoConsulta  # noqa: E402

FILAS = 1000
CONSULTAS = 20


def _medir(funcion, *args, repeticiones: int = 1, **kwargs) -> tuple[float, object]:
    inicio = time.perf_counter()
    resultado = None
    for _ in range(repeticiones):
        resultado = funcion(*args, **kwargs)
    return (time.perf_counter() - inicio) / repeticiones, resultado


def _crear_excel(ruta: Path) -> list[str]:
    nombres = [f"USUARIO APELLIDO {indice:04d}" for indice in range(FILAS)]
    df = pd.DataFrame(
        {
            "Detalle": ["Ticket"] * FILAS,
            "Fecha": ["2026-01-15"] * FILAS,
            "Solicitante": nombres,
            "Area": ["SISTEMAS"] * FILAS,
            "Atención": ["Remoto"] * FILAS,
            "Requerimiento": ["Soporte"] * FILAS,
            "Responsable": ["MESA"] * FILAS,
            "Subtipo": ["Incidente"] * FILAS,
            "Proyecto": ["Operación"] * FILAS,
            "Horas": [1.5] * FILAS,
            "Actividad": ["Atención de ticket"] * FILAS,
        }
    )
    df.to_excel(ruta, index=False, sheet_name="Tickets")
    return nombres


class RepositorioFalso:
    """Repositorio en memoria con la misma forma que el real."""

    def __init__(self, empleados: list[Empleado]) -> None:
        self._por_nombre: dict[str, list[Empleado]] = {}
        for empleado in empleados:
            clave = coincidencias.normalizar_nombre(empleado.nombre)
            self._por_nombre.setdefault(clave, []).append(empleado)

    def buscar_por_nombres(self, nombres, empresas=None, **_kwargs) -> ResultadoConsulta:
        resultado = ResultadoConsulta()
        for nombre in nombres:
            clave = coincidencias.normalizar_nombre(nombre)
            empleados = list(self._por_nombre.get(clave, []))
            resultado.por_consulta[clave] = empleados
            resultado.empleados.extend(empleados)
        return resultado


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="benchmark_fase7_") as temporal:
        raiz = Path(temporal)
        ruta_excel = raiz / "tickets.xlsx"
        nombres = _crear_excel(ruta_excel)

        tiempo_preview, _ = _medir(previsualizar, ruta_excel, repeticiones=3)
        tiempo_importacion, extraccion = _medir(extraer, ruta_excel, repeticiones=2)
        total_filas = len(getattr(extraccion, "df", []))

        maestro = Maestro(
            pd.DataFrame(
                {
                    "USUARIO": nombres[:1000],
                    "AREA_USUARIO": ["SISTEMAS"] * 1000,
                }
            )
        )
        empleados = [
            Empleado(
                empresa="SERVIAM",
                codigo=f"{indice:05d}",
                nombre=nombre,
                departamento="SISTEMAS",
                activo=True,
            )
            for indice, nombre in enumerate(nombres)
        ]
        repositorio = RepositorioFalso(empleados)
        consolidador = coincidencias.ConsolidadorCoincidencias(
            maestro=maestro,
            repositorio=repositorio,
            cargar_empleados=lambda: empleados,
        )

        exactos = [nombre for nombre in nombres[:CONSULTAS]]
        tiempo_exacta, grupos = _medir(
            consolidador.buscar, exactos, usar_sql=False, repeticiones=1
        )
        coincidencias_exactas = sum(
            1 for grupo in grupos.values() if grupo.exactos
        )

        aproximados = [f"USUARIO APELLIDO {indice:04d} X" for indice in range(CONSULTAS)]
        tiempo_aproximada, _ = _medir(
            consolidador.buscar, aproximados, usar_sql=False, repeticiones=1
        )

        pendientes = [
            {"usuario": nombres[indice], "filas": [indice + 2]}
            for indice in range(CONSULTAS)
        ]
        tiempo_consolidado, consolidado = _medir(
            consolidador.buscar_para_pendientes,
            pendientes,
            usar_sql=True,
            repeticiones=1,
        )

        consultas_sql = len(pendientes)
        tiempo_similitud, _ = _medir(
            similitud.buscar_similares,
            "USUARIO APELLIDO 0001 X",
            nombres,
            limite=8,
            umbral=0.72,
            repeticiones=1,
        )

        tracemalloc.start()
        _ = consolidador.buscar(aproximados[:10], usar_sql=False)
        _actual, pico = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        contador = {"eventos": 0}

        def suscriptor(_evento):
            contador["eventos"] += 1

        cancelar = eventos.suscribir(eventos.ASIGNACION_REALIZADA, suscriptor)
        tiempo_eventos, _ = _medir(
            eventos.publicar_asignacion,
            "USUARIO PRUEBA",
            "SISTEMAS",
            origen="benchmark",
            repeticiones=1000,
        )
        cancelar()

        print("| Medición | Resultado |")
        print("| --- | --- |")
        print(f"| Filas importadas | {total_filas} |")
        print(f"| Vista previa (ms) | {tiempo_preview * 1000:.1f} |")
        print(f"| Importación completa (ms) | {tiempo_importacion * 1000:.1f} |")
        print(
            f"| Búsqueda exacta, {CONSULTAS} usuarios (ms) | "
            f"{tiempo_exacta * 1000:.1f} |"
        )
        print(f"| Coincidencias exactas encontradas | {coincidencias_exactas} |")
        print(
            f"| Búsqueda aproximada, {CONSULTAS} usuarios (ms) | "
            f"{tiempo_aproximada * 1000:.1f} |"
        )
        print(
            f"| Consolidación con SQL simulado (ms) | "
            f"{tiempo_consolidado * 1000:.1f} |"
        )
        print(f"| Consultas SQL ejecutadas | {consultas_sql} |")
        print(
            f"| Similitud sobre {FILAS} nombres (ms) | "
            f"{tiempo_similitud * 1000:.1f} |"
        )
        print(f"| Memoria pico aproximada (KB) | {pico / 1024:.1f} |")
        print(
            f"| Publicación de 1 evento (µs) | "
            f"{tiempo_eventos * 1_000_000:.1f} |"
        )
        print(f"| Pendientes consolidados | {len(consolidado)} |")
        print(f"| Eventos entregados | {contador['eventos']} |")


if __name__ == "__main__":
    main()
