"""Validaciones para autocompletado de áreas y resolución previa a SQL."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.esquema import C_AREA, C_SOLICITANTE  # noqa: E402
from core.maestro import Maestro  # noqa: E402
from core.normalizador import normalizar_usuario  # noqa: E402
from core.procesador import _resolver_pendientes_sql  # noqa: E402
from database.repository import Empleado  # noqa: E402
from ventana_validacion import filtrar_areas  # noqa: E402


class PruebasFiltradoAreas(unittest.TestCase):
    def setUp(self) -> None:
        self.areas = [
            "ADMINISTRACIÓN",
            "CONTABILIDAD",
            "RECURSOS HUMANOS",
            "SISTEMAS",
            "LOGÍSTICA",
            "OPERACIONES",
            "Sistemas",
        ]

    def test_lista_vacia_devuelve_opciones_maestras_sin_duplicados(self) -> None:
        opciones = filtrar_areas(self.areas, "")
        self.assertEqual(len(opciones), 6)
        self.assertIn("SISTEMAS", opciones)

    def test_coincidencia_parcial_y_sin_distinguir_mayusculas(self) -> None:
        self.assertEqual(filtrar_areas(self.areas, "A"), ["ADMINISTRACIÓN"])
        self.assertEqual(filtrar_areas(self.areas, "SI"), ["SISTEMAS"])
        self.assertEqual(filtrar_areas(self.areas, "re"), ["RECURSOS HUMANOS"])
        self.assertEqual(filtrar_areas(self.areas, "conta"), ["CONTABILIDAD"])

    def test_sin_coincidencias(self) -> None:
        self.assertEqual(filtrar_areas(self.areas, "inexistente"), [])


def _empleado(nombre: str, area: str) -> Empleado:
    return Empleado(
        empresa="SERVIAM",
        codigo=nombre,
        nombre=nombre,
        departamento=area,
        activo=True,
    )


def _pendiente(usuario: str, filas: list[int]) -> tuple[str, dict]:
    clave = normalizar_usuario(usuario) or ""
    return clave, {"usuario": usuario, "filas": filas}


def _fila(usuario: str, numero: int) -> dict:
    return {
        C_SOLICITANTE: usuario,
        C_AREA: "",
        "_fila_origen": numero,
        "_estado": "PENDIENTE_DE_ASIGNACION",
        "_observaciones": [],
    }


class PruebasResolucionAutomatica(unittest.TestCase):
    def test_resuelve_desde_cache_sin_consultar_sql(self) -> None:
        empleado = _empleado("ANA TORRES", "SISTEMAS")
        pendientes = dict([_pendiente("ANA TORRES", [2])])
        fila = _fila("ANA TORRES", 2)
        with mock.patch("database.synchronizer.cargar_empleados", return_value=[empleado]), mock.patch(
            "database.repository.RepositorioEmpleados"
        ) as repo:
            resultado = _resolver_pendientes_sql(
                pendientes, [fila], Maestro(), None, "ejecucion"
            )

        self.assertEqual(fila[C_AREA], "SISTEMAS")
        self.assertEqual(fila["_estado"], "Validado")
        self.assertFalse(pendientes)
        self.assertEqual(resultado[2:4], (1, 1))
        repo.assert_not_called()

    def test_sql_recibe_en_lote_solo_usuarios_no_resueltos_en_cache(self) -> None:
        cache = [_empleado("ANA TORRES", "SISTEMAS")]
        sql = [_empleado("LUIS PEREZ", "CONTABILIDAD")]
        pendientes = dict(
            [
                _pendiente("ANA TORRES", [2]),
                _pendiente("LUIS PEREZ", [3]),
                _pendiente("MARIA ROJAS", [4]),
            ]
        )
        filas = [_fila(usuario, n) for n, usuario in enumerate(
            ("ANA TORRES", "LUIS PEREZ", "MARIA ROJAS"), start=2
        )]
        consulta = SimpleNamespace(errores=[], empleados=sql)
        with (
            mock.patch("database.synchronizer.cargar_empleados", side_effect=[cache, cache + sql]),
            mock.patch("database.repository.RepositorioEmpleados") as repo_clase,
            mock.patch("database.synchronizer.Sincronizador") as sincronizador,
        ):
            repo_clase.return_value.buscar_por_nombres.return_value = consulta
            resultado = _resolver_pendientes_sql(
                pendientes, filas, Maestro(), object(), "ejecucion"
            )

        repo_clase.return_value.buscar_por_nombres.assert_called_once_with(
            ["LUIS PEREZ", "MARIA ROJAS"]
        )
        sincronizador.return_value.actualizar_desde_busqueda.assert_called_once_with(sql)
        self.assertEqual([fila[C_AREA] for fila in filas], ["SISTEMAS", "CONTABILIDAD", ""])
        self.assertEqual(len(pendientes), 1)
        self.assertEqual(pendientes.popitem()[1]["usuario"], "MARIA ROJAS")
        self.assertEqual(resultado[2:4], (2, 2))

    def test_consulta_sql_fallida_conserva_resolucion_de_cache(self) -> None:
        empleado = _empleado("ANA TORRES", "SISTEMAS")
        pendientes = dict(
            [_pendiente("ANA TORRES", [2]), _pendiente("LUIS PEREZ", [3])]
        )
        filas = [_fila("ANA TORRES", 2), _fila("LUIS PEREZ", 3)]
        with mock.patch("database.synchronizer.cargar_empleados", return_value=[empleado]), mock.patch(
            "database.repository.RepositorioEmpleados",
            side_effect=RuntimeError("SQL no disponible"),
        ):
            resultado = _resolver_pendientes_sql(
                pendientes, filas, Maestro(), object(), "ejecucion"
            )

        self.assertEqual(filas[0][C_AREA], "SISTEMAS")
        self.assertEqual(filas[1][C_AREA], "")
        self.assertEqual(len(pendientes), 1)
        self.assertEqual(resultado[2:4], (1, 1))

    def test_asignacion_manual_existente_no_se_sobrescribe(self) -> None:
        empleado = _empleado("ANA TORRES", "OTRA AREA")
        maestro = Maestro(
            pd.DataFrame([{"USUARIO": "ANA TORRES", "AREA_USUARIO": "AREA MANUAL"}])
        )
        pendientes = dict([_pendiente("ANA TORRES", [2])])
        fila = _fila("ANA TORRES", 2)
        with mock.patch("database.synchronizer.cargar_empleados", return_value=[empleado]):
            resultado = _resolver_pendientes_sql(
                pendientes, [fila], maestro, None, "ejecucion"
            )

        self.assertEqual(fila[C_AREA], "")
        self.assertEqual(maestro.buscar("ANA TORRES"), "AREA MANUAL")
        self.assertEqual(len(pendientes), 1)
        self.assertEqual(len(resultado[1]), 1)


if __name__ == "__main__":
    unittest.main()
