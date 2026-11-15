"""Pruebas de política y mapeo de consultas SQL manuales."""

from __future__ import annotations

import sys
import unittest
from types import ModuleType, SimpleNamespace
from pathlib import Path
from unittest import mock

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.consulta_sql_manual import ErrorConsultaManual, mapear_filas, validar_select  # noqa: E402


class PruebasConsultaManual(unittest.TestCase):
    def test_falla_cerrado_si_no_hay_parser_ast(self) -> None:
        try:
            import sqlglot  # noqa: F401
        except ImportError:
            with self.assertRaises(ErrorConsultaManual):
                validar_select("SELECT 1")

    def test_rechaza_ast_de_escritura_y_sentencias_multiples(self) -> None:
        class Nodo:
            def __init__(self, *hijos):
                self.hijos = hijos

            def walk(self):
                yield self
                for hijo in self.hijos:
                    yield from hijo.walk()

        class Select(Nodo):
            pass

        class Update(Nodo):
            pass

        class Delete(Nodo):
            pass

        class With(Nodo):
            def __init__(self, this):
                super().__init__(this)
                self.this = this

        parser = ModuleType("sqlglot")
        parser.exp = SimpleNamespace(
            Select=Select, With=With, Insert=type("Insert", (Nodo,), {}),
            Update=Update, Delete=Delete, Merge=type("Merge", (Nodo,), {}),
            Create=type("Create", (Nodo,), {}), Drop=type("Drop", (Nodo,), {}),
            Alter=type("Alter", (Nodo,), {}), Command=type("Command", (Nodo,), {}),
            Transaction=type("Transaction", (Nodo,), {}), Use=type("Use", (Nodo,), {}),
            Set=type("Set", (Nodo,), {}), Grant=type("Grant", (Nodo,), {}),
            Revoke=type("Revoke", (Nodo,), {}), Into=type("Into", (Nodo,), {}),
            TruncateTable=type("TruncateTable", (Nodo,), {}),
            NextValueFor=type("NextValueFor", (Nodo,), {}), Anonymous=type("Anonymous", (Nodo,), {}),
        )
        parser.parse = lambda sql, read: (
            [Select()] if sql == "SELECT 1" else
            [With(Select())] if sql.startswith("WITH") else
            [Update()] if sql.startswith("UPDATE") else
            [Select(), Select()] if ";" in sql else
            [Select(Delete())]
        )
        with mock.patch.dict(sys.modules, {"sqlglot": parser}):
            self.assertEqual(validar_select("SELECT 1"), "SELECT 1")
            self.assertEqual(validar_select("WITH x AS (...) SELECT 1"), "WITH x AS (...) SELECT 1")
            for consulta in ("UPDATE t SET x=1", "SELECT 1; SELECT 2", "SELECT (DELETE)"):
                with self.assertRaises(ErrorConsultaManual):
                    validar_select(consulta)

    def test_mapper_informa_columnas_faltantes(self) -> None:
        resultado = mapear_filas([{"Nombre": "Ana", "Area": "TI"}])
        self.assertEqual(resultado["faltantes"], ["EMPLEADO", "FUENTE"])
        self.assertEqual(resultado["columnas"], ["Nombre", "Area"])

    def test_mapper_no_asume_columnas_opcionales(self) -> None:
        resultado = mapear_filas([
            {"empleado": "001", "nombre": "Ana", "fuente": "ANDINA"}
        ])
        self.assertEqual(resultado["faltantes"], [])
        self.assertNotIn("DEPARTAMENTO", resultado["mapeo"])


if __name__ == "__main__":
    unittest.main()
