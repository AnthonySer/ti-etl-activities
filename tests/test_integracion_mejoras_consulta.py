"""Pruebas de integración de las mejoras de consulta (Fase 5).

Se usan exclusivamente dobles de prueba: no se conecta ni se modifica una base
de datos real.
"""

from __future__ import annotations

import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config.sqlserver import ConfiguracionSQL  # noqa: E402
from core import busqueda_solicitantes as busqueda  # noqa: E402
from core import seguimiento_consultas as seguimiento  # noqa: E402
from database import queries  # noqa: E402
from database.repository import RepositorioEmpleados  # noqa: E402
from database.resolucion import CASO_A, CASO_B, ResolverAreas  # noqa: E402

COLUMNAS = {
    "EMPLEADO": ["EMPLEADO", "NOMBRE", "DEPARTAMENTO", "ACTIVO"],
}


def config(empresas: list[str], lote: int = 50) -> ConfiguracionSQL:
    return ConfiguracionSQL(
        habilitado=True,
        servidor="10.0.0.1",
        base_datos="EXACTUS",
        autenticacion="windows",
        empresas=empresas,
        lote=lote,
    )


def _empresa_de(sql: str, parametros: list[Any], filas: dict[str, list[dict]]) -> str:
    if "INFORMATION_SCHEMA.COLUMNS" in sql:
        return str(parametros[0]) if parametros else ""
    for empresa in filas:
        if f"[{empresa}]" in sql:
            return empresa
    return ""


def repositorio(
    configuracion: ConfiguracionSQL,
    filas: dict[str, list[dict[str, Any]]],
    *,
    patrones_vistos: list[list[Any]] | None = None,
) -> RepositorioEmpleados:
    @contextmanager
    def conexion():
        yield object()

    def fabrica(_config):
        return conexion()

    def ejecutor(_conexion, sql, parametros):
        empresa = _empresa_de(sql, parametros, filas)
        if "INFORMATION_SCHEMA.COLUMNS" in sql:
            return [
                {"TABLE_NAME": "EMPLEADO", "COLUMN_NAME": columna}
                for columna in COLUMNAS["EMPLEADO"]
            ]
        if patrones_vistos is not None:
            patrones_vistos.append(list(parametros))
        return list(filas.get(empresa, []))

    return RepositorioEmpleados(
        configuracion, fabrica_conexion=fabrica, ejecutor=ejecutor
    )


def resolver(empleados) -> ResolverAreas:
    return ResolverAreas(
        empleados, cargar_procedencia=lambda: {"registros": {}}
    )


class PruebasSqlParametrizado(unittest.TestCase):
    def test_like_usa_parametros_y_escape(self) -> None:
        sql, cantidad = queries.sql_empleados(
            base_datos="EXACTUS",
            esquema="SERVIAM",
            patrones=["%MARTINEZ%PERCY%"],
            columnas_disponibles={
                "EMPLEADO": {"EMPLEADO", "NOMBRE", "DEPARTAMENTO", "ACTIVO"}
            },
        )
        self.assertEqual(cantidad, 1)
        self.assertIn("LIKE ? ESCAPE '\\'", sql)
        self.assertNotIn("MARTINEZ", sql)
        self.assertNotIn("PERCY", sql)

    def test_dos_palabras_se_envian_invertidas(self) -> None:
        patrones_vistos: list[list[Any]] = []
        repo = repositorio(
            config(["SERVIAM"]), {"SERVIAM": []}, patrones_vistos=patrones_vistos
        )
        repo.buscar_por_nombres(["Percy Martinez"])
        self.assertEqual(patrones_vistos, [["%MARTINEZ%PERCY%"]])

    def test_comodines_del_solicitante_se_escapan(self) -> None:
        # La normalización elimina los signos del nombre del Excel; aun así el
        # constructor de patrones escapa cualquier comodín que llegue a SQL.
        patron = busqueda.patron_like(["50%_LITERAL"])
        self.assertEqual(patron, "%50\\%\\_LITERAL%")
        sql, _ = queries.sql_empleados(
            base_datos="EXACTUS",
            esquema="SERVIAM",
            patrones=[patron],
            columnas_disponibles={
                "EMPLEADO": {"EMPLEADO", "NOMBRE", "DEPARTAMENTO"}
            },
        )
        self.assertIn("ESCAPE", sql)
        self.assertNotIn("50%_LITERAL", sql)


class PruebasProgresoYResultados(unittest.TestCase):
    def _filas(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "SERVIAM": [
                {
                    "EMPLEADO": "1",
                    "NOMBRE": "MARTINEZ PERCY",
                    "DEPARTAMENTO": "SISTEMAS",
                    "ACTIVO": True,
                }
            ],
            "FILASUR": [
                {
                    "EMPLEADO": "1",
                    "NOMBRE": "MARTINEZ PERCY",
                    "DEPARTAMENTO": "CONTABILIDAD",
                    "ACTIVO": True,
                }
            ],
        }

    def test_eventos_reales_y_mapeo_por_consulta(self) -> None:
        repo = repositorio(config(["SERVIAM", "FILASUR"]), self._filas())
        plan = busqueda.plan_busqueda("Percy Martinez").como_dict()
        eventos: list[dict[str, Any]] = []
        consulta = repo.buscar_por_consultas([plan], al_progreso=eventos.append)

        estados = {
            evento["empresa"]: evento["estado"]
            for evento in eventos
            if evento["estado"] in seguimiento.ESTADOS_FINALES
        }
        self.assertEqual(estados["SERVIAM"], seguimiento.ESTADO_COMPLETADA)
        self.assertEqual(estados["FILASUR"], seguimiento.ESTADO_COMPLETADA)
        self.assertEqual(consulta.resumen()["empleados"], 2)
        self.assertEqual(
            len(consulta.por_consulta[plan["clave"]]), 2
        )
        self.assertGreater(consulta.duracion_total, 0)

    def test_mismo_nombre_en_dos_empresas_no_se_fusiona(self) -> None:
        repo = repositorio(config(["SERVIAM", "FILASUR"]), self._filas())
        plan = busqueda.plan_busqueda("Percy Martinez").como_dict()
        consulta = repo.buscar_por_consultas([plan])
        candidatos = consulta.por_consulta[plan["clave"]]
        self.assertEqual(
            {(empleado.empresa, empleado.codigo) for empleado in candidatos},
            {("SERVIAM", "1"), ("FILASUR", "1")},
        )
        resolucion = resolver(consulta.empleados).resolver_candidatos(
            "Percy Martinez", candidatos
        )
        self.assertEqual(resolucion.caso, CASO_B)
        self.assertEqual(len(resolucion.candidatos), 2)

    def test_seleccion_usa_solo_el_candidato_elegido(self) -> None:
        repo = repositorio(config(["SERVIAM", "FILASUR"]), self._filas())
        plan = busqueda.plan_busqueda("Percy Martinez").como_dict()
        consulta = repo.buscar_por_consultas([plan])
        candidatos = consulta.por_consulta[plan["clave"]]
        asignador = resolver(consulta.empleados)
        areas = {}
        for candidato in candidatos:
            resolucion = asignador.resolver_candidato(
                "Percy Martinez", candidato
            )
            self.assertEqual(resolucion.caso, CASO_A)
            areas[candidato.empresa] = resolucion.area
        self.assertEqual(
            areas, {"SERVIAM": "SISTEMAS", "FILASUR": "CONTABILIDAD"}
        )

    def test_una_empresa_con_error_no_se_marca_completada(self) -> None:
        filas = {"SERVIAM": self._filas()["SERVIAM"]}
        repo = repositorio(config(["SERVIAM", "FILASUR"]), filas)
        eventos: list[dict[str, Any]] = []
        consulta = repo.buscar_por_consultas(
            [busqueda.plan_busqueda("Percy").como_dict()],
            al_progreso=eventos.append,
        )
        # FILASUR no tiene metadatos en el doble: se informa sin esquema.
        estados = {
            evento["empresa"]: evento["estado"]
            for evento in eventos
            if evento["estado"] in seguimiento.ESTADOS_FINALES
        }
        self.assertEqual(estados["SERVIAM"], seguimiento.ESTADO_COMPLETADA)
        self.assertNotEqual(
            estados.get("FILASUR"), seguimiento.ESTADO_COMPLETADA
        )
        self.assertFalse(consulta.cancelada)

    def test_cancelacion_detiene_empresas_pendientes(self) -> None:
        repo = repositorio(config(["SERVIAM", "FILASUR"]), self._filas())
        eventos: list[dict[str, Any]] = []
        llamadas = {"n": 0}

        def debe_cancelar() -> bool:
            llamadas["n"] += 1
            return llamadas["n"] >= 3

        consulta = repo.buscar_por_consultas(
            [busqueda.plan_busqueda("Percy Martinez").como_dict()],
            al_progreso=eventos.append,
            debe_cancelar=debe_cancelar,
        )
        self.assertTrue(consulta.cancelada)
        self.assertEqual(consulta.empresas_consultadas, ["SERVIAM"])
        canceladas = {
            evento["empresa"]
            for evento in eventos
            if evento["estado"] == seguimiento.ESTADO_CANCELADA
        }
        self.assertEqual(canceladas, {"FILASUR"})
        self.assertEqual(len(consulta.por_consulta["PERCY MARTINEZ"]), 1)


class PruebasFlujoConsultaSql(unittest.TestCase):
    def _ventana(self, usuario: str):
        from ventana_validacion import VentanaValidacion

        ventana = VentanaValidacion.__new__(VentanaValidacion)
        ventana._ocupado = False
        ventana._seleccionados = lambda: [
            {"usuario": usuario, "clave": "CLAVE"}
        ]
        ventana._var_resumen = SimpleNamespace(set=mock.MagicMock())
        ventana._ejecutar = mock.MagicMock()
        return ventana

    def test_cancelar_el_orden_no_abre_consulta(self) -> None:
        configuracion = SimpleNamespace(
            habilitado=True,
            configurada=True,
            empresas_activas=lambda: ["SERVIAM"],
        )
        with (
            mock.patch(
                "ventana_validacion.cargar_configuracion",
                return_value=configuracion,
            ),
            mock.patch(
                "ventana_validacion.controlador_disponible",
                return_value=True,
            ),
            mock.patch(
                "ventana_validacion.VentanaOrdenTerminos.pedir",
                return_value=None,
            ) as pedir,
            mock.patch(
                "ventana_validacion.VentanaSeguimientoConsulta"
            ) as seguimiento_ventana,
        ):
            ventana = self._ventana("Juan Carlos Perez")
            ventana._consultar_sql()

        pedir.assert_called_once()
        seguimiento_ventana.assert_not_called()
        ventana._ejecutar.assert_not_called()
        mensaje = ventana._var_resumen.set.call_args[0][0]
        self.assertIn("No se ejecutó ninguna consulta", mensaje)

    def test_sin_solicitante_no_abre_consulta(self) -> None:
        configuracion = SimpleNamespace(
            habilitado=True,
            configurada=True,
            empresas_activas=lambda: ["SERVIAM"],
        )
        with (
            mock.patch(
                "ventana_validacion.cargar_configuracion",
                return_value=configuracion,
            ),
            mock.patch(
                "ventana_validacion.controlador_disponible",
                return_value=True,
            ),
            mock.patch(
                "ventana_validacion.VentanaSeguimientoConsulta"
            ) as seguimiento_ventana,
        ):
            ventana = self._ventana("   ")
            ventana._consultar_sql()

        seguimiento_ventana.assert_not_called()
        ventana._ejecutar.assert_not_called()


if __name__ == "__main__":
    unittest.main()
