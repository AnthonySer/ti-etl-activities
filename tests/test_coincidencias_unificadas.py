"""Pruebas de la vista unificada de coincidencias Excel/SQL.

Cubren la capa pura ``core.coincidencias_vista``, la combinación de
candidatos y la apertura de la ventana en ``ventana_validacion``, y la
clasificación tri-estado de ``core.asignacion_asistida``.

No se conecta a SQL Server ni se escribe en disco. Las rutas que podrían
ejecutar consultas reales se sustituyen con dobles de prueba.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core import asignacion_asistida as asistida  # noqa: E402
from core import coincidencias_vista as vista  # noqa: E402


# ---------------------------------------------------------------------------
# Datos de apoyo
# ---------------------------------------------------------------------------
def fila_sql(
    nombre: str = "JUAN PEREZ GOMEZ",
    empresa: str = "SERVIAM",
    empleado: str = "E001",
    *,
    area: str = "SISTEMAS",
    activo: object = True,
    estado_empleado: str = "",
    **extra: object,
) -> dict:
    """Candidato típico devuelto por SQL Server."""

    datos = {
        "empresa": empresa,
        "empleado": empleado,
        "nombre": nombre,
        "departamento": area,
        "area": area,
        "puesto": "Analista",
        "activo": activo,
        "ESTADO_EMPLEADO": estado_empleado,
        "fuente": "EMPLEADO",
        "origen": "SQL_SERVER",
    }
    datos.update(extra)
    return datos


def fila_maestro(
    nombre: str = "JUAN PEREZ",
    *,
    area: str = "CONTABILIDAD",
    empresa: str = "MAESTRO",
    empleado: str = "",
    **extra: object,
) -> dict:
    """Candidato típico del Excel maestro."""

    datos = {
        "empresa": empresa,
        "empleado": empleado,
        "nombre": nombre,
        "area": area,
        "fuente": "MAESTRO",
        "origen": "MAESTRO",
    }
    datos.update(extra)
    return datos


# ---------------------------------------------------------------------------
# Capa pura: procedencia, estado, cruces, consolidación y conteos
# ---------------------------------------------------------------------------
class PruebasProcedencia(unittest.TestCase):
    def test_maestro_y_sql_se_clasifican_por_su_origen(self) -> None:
        self.assertEqual(
            vista.origen_candidato(fila_maestro()), vista.PROCEDENCIA_EXCEL
        )
        self.assertEqual(
            vista.origen_candidato(fila_sql()), vista.PROCEDENCIA_SQL
        )

    def test_preparar_candidato_conserva_identificadores_y_campos_ajenos(self) -> None:
        original = fila_sql(
            conflicto=True,
            motivo="Área distinta en el maestro",
            resolucion="pendiente",
        )
        preparado = vista.preparar_candidato(original)

        self.assertEqual(preparado["empleado"], "E001")
        self.assertEqual(preparado["empresa"], "SERVIAM")
        self.assertTrue(preparado["conflicto"])
        self.assertEqual(preparado["motivo"], "Área distinta en el maestro")
        self.assertEqual(preparado["resolucion"], "pendiente")
        self.assertEqual(preparado["procedencia"], vista.PROCEDENCIA_SQL)
        self.assertEqual(preparado["tabla_origen"], "EMPLEADO")
        self.assertEqual(preparado["AREA_SQL"], "SISTEMAS")
        # El diccionario recibido no se altera.
        self.assertNotIn("procedencia", original)
        self.assertNotIn("estado_usuario", original)

    def test_maestro_no_aporta_area_sql(self) -> None:
        preparado = vista.preparar_candidato(
            fila_maestro(area="CONTABILIDAD")
        )
        self.assertEqual(preparado["procedencia"], vista.PROCEDENCIA_EXCEL)
        self.assertEqual(preparado["AREA_SQL"], "")
        self.assertEqual(preparado["AREA_MAESTRO"], "CONTABILIDAD")
        self.assertEqual(preparado["tabla_origen"], "")

    def test_area_maestro_se_completa_solo_en_filas_sql(self) -> None:
        preparado = vista.preparar_candidato(
            fila_sql(area="SISTEMAS"), area_maestro="CONTABILIDAD"
        )
        self.assertEqual(preparado["AREA_SQL"], "SISTEMAS")
        self.assertEqual(preparado["AREA_MAESTRO"], "CONTABILIDAD")

    def test_tabla_origen_no_repite_la_empresa(self) -> None:
        preparado = vista.preparar_candidato(
            fila_sql(empresa="SERVIAM", fuente="SERVIAM")
        )
        self.assertEqual(preparado["tabla_origen"], "")


class PruebasEstadoTriEstatal(unittest.TestCase):
    def test_estados_inequivocos(self) -> None:
        casos = (
            ({"activo": True}, vista.ESTADO_ACTIVO),
            ({"activo": "1"}, vista.ESTADO_ACTIVO),
            ({"activo": "A"}, vista.ESTADO_ACTIVO),
            ({"estado_empleado": "ACTIVO"}, vista.ESTADO_ACTIVO),
            ({"activo": False}, vista.ESTADO_INACTIVO),
            ({"activo": "0"}, vista.ESTADO_INACTIVO),
            ({"ESTADO_EMPLEADO": "I"}, vista.ESTADO_INACTIVO),
            ({"ESTADO_EMPLEADO": "INAC"}, vista.ESTADO_INACTIVO),
            ({"ESTADO_EMPLEADO": "CESA"}, vista.ESTADO_INACTIVO),
            ({"activo": None}, vista.ESTADO_NO_DISPONIBLE),
            ({}, vista.ESTADO_NO_DISPONIBLE),
            ({"activo": "DESCONOCIDO"}, vista.ESTADO_NO_DISPONIBLE),
        )
        for datos, esperado in casos:
            with self.subTest(datos=datos):
                self.assertEqual(vista.estado_candidato(datos), esperado)
                self.assertEqual(
                    vista.es_inactivo(datos), esperado == vista.ESTADO_INACTIVO
                )

    def test_campos_contradictorios_no_permiten_afirmar_estado(self) -> None:
        datos = {"activo": True, "ACTIVO": False}
        self.assertEqual(
            vista.estado_candidato(datos), vista.ESTADO_NO_DISPONIBLE
        )
        self.assertFalse(vista.es_inactivo(datos))

    def test_un_campo_conocido_completa_el_estado(self) -> None:
        self.assertEqual(
            vista.estado_candidato(
                {"activo": None, "ESTADO_EMPLEADO": "CESA"}
            ),
            vista.ESTADO_INACTIVO,
        )
        self.assertEqual(
            vista.estado_candidato({"activo": None, "ACTIVO": "A"}),
            vista.ESTADO_ACTIVO,
        )


class PruebasCruces(unittest.TestCase):
    def test_identidad_igual_confirma_el_cruce(self) -> None:
        excel = fila_maestro(empresa="SERVIAM", empleado="E001")
        sql = fila_sql(empresa="SERVIAM", empleado="E001")
        consolidados = vista.consolidar([excel, sql])

        self.assertEqual(len(consolidados), 2)
        for candidato in consolidados:
            self.assertEqual(candidato["cruce_fuentes"], vista.CRUCE_CONFIRMADO)
            self.assertTrue(candidato["coincide_entre_fuentes"])
            self.assertFalse(candidato["es_posible_coincidencia"])

    def test_mismo_nombre_sin_identidad_solo_es_posible(self) -> None:
        excel = fila_maestro(nombre="JUAN PEREZ")
        sql = fila_sql(
            nombre="JUAN PEREZ GOMEZ", empresa="FILASUR", empleado="E999"
        )
        consolidados = vista.consolidar([excel, sql])

        for candidato in consolidados:
            self.assertEqual(candidato["cruce_fuentes"], vista.CRUCE_POSIBLE)
            self.assertFalse(candidato["coincide_entre_fuentes"])
            self.assertTrue(candidato["es_posible_coincidencia"])
        self.assertNotIn(
            vista.CRUCE_CONFIRMADO,
            {candidato["cruce_fuentes"] for candidato in consolidados},
        )

    def test_identidades_completas_distintas_no_se_cruzan(self) -> None:
        excel = fila_maestro(
            nombre="JUAN PEREZ", empresa="SERVIAM", empleado="E001"
        )
        sql = fila_sql(
            nombre="JUAN PEREZ", empresa="SERVIAM", empleado="E002"
        )
        consolidados = vista.consolidar([excel, sql])

        for candidato in consolidados:
            self.assertEqual(candidato["cruce_fuentes"], vista.CRUCE_NINGUNO)
            self.assertFalse(candidato["coincide_entre_fuentes"])

    def test_identidad_incompleta_por_falta_de_empleado_solo_es_posible(self) -> None:
        excel = fila_maestro(
            nombre="MARIA LOPEZ", empresa="SERVIAM", empleado=""
        )
        sql = fila_sql(
            nombre="MARIA LOPEZ", empresa="SERVIAM", empleado="E010"
        )
        consolidados = vista.consolidar([excel, sql])

        for candidato in consolidados:
            self.assertEqual(candidato["cruce_fuentes"], vista.CRUCE_POSIBLE)

    def test_maestro_nunca_es_empresa_verificable(self) -> None:
        excel = fila_maestro(nombre="JUAN PEREZ")
        sql = fila_sql(nombre="JUAN PEREZ", empresa="MAESTRO", empleado="E001")
        consolidados = vista.consolidar([excel, sql])

        for candidato in consolidados:
            self.assertNotEqual(
                candidato["cruce_fuentes"], vista.CRUCE_CONFIRMADO
            )

    def test_texto_cruce_distingue_confirmada_y_posible(self) -> None:
        self.assertIn(
            "Confirmada", vista.texto_cruce({"cruce_fuentes": vista.CRUCE_CONFIRMADO})
        )
        self.assertIn(
            "Posible", vista.texto_cruce({"cruce_fuentes": vista.CRUCE_POSIBLE})
        )
        self.assertEqual(
            vista.texto_cruce({"cruce_fuentes": vista.CRUCE_NINGUNO}), ""
        )


class PruebasConsolidacion(unittest.TestCase):
    def test_no_fusiona_filas_de_fuentes_distintas(self) -> None:
        consolidados = vista.consolidar(
            [fila_maestro(nombre="JUAN PEREZ"), fila_sql(nombre="JUAN PEREZ GOMEZ")]
        )
        self.assertEqual(len(consolidados), 2)
        self.assertEqual(
            {candidato["procedencia"] for candidato in consolidados},
            {vista.PROCEDENCIA_EXCEL, vista.PROCEDENCIA_SQL},
        )

    def test_deduplica_sin_perder_campos_disponibles(self) -> None:
        primero = fila_sql(puesto="Analista", correo="")
        segundo = fila_sql(puesto="", correo="juan@serviam.com")
        originales = [dict(primero), dict(segundo)]

        consolidados = vista.consolidar(originales)

        self.assertEqual(len(consolidados), 1)
        self.assertEqual(consolidados[0]["puesto"], "Analista")
        self.assertEqual(consolidados[0]["correo"], "juan@serviam.com")
        # Las entradas no se modifican al consolidar.
        self.assertEqual(originales[0]["correo"], "")
        self.assertEqual(originales[1]["puesto"], "")

    def test_deduplica_en_ambos_ordenes(self) -> None:
        pobre = fila_sql(puesto="", correo="")
        rico = fila_sql(puesto="Analista", correo="juan@serviam.com")
        for orden in ((pobre, rico), (rico, pobre)):
            with self.subTest(orden=[id(item) for item in orden]):
                consolidados = vista.consolidar(orden)
                self.assertEqual(len(consolidados), 1)
                self.assertEqual(consolidados[0]["puesto"], "Analista")
                self.assertEqual(
                    consolidados[0]["correo"], "juan@serviam.com"
                )

    def test_filas_con_area_distinta_no_son_duplicados(self) -> None:
        consolidados = vista.consolidar(
            [
                fila_sql(area="SISTEMAS"),
                fila_sql(area="CONTABILIDAD"),
            ]
        )
        self.assertEqual(len(consolidados), 2)

    def test_duplicado_completa_el_estado_desde_la_fila_mas_rica(self) -> None:
        sin_estado = fila_sql(activo=None, puesto="Analista", correo="")
        con_estado = fila_sql(
            activo=True, puesto="", correo="juan@serviam.com"
        )
        for orden in ((sin_estado, con_estado), (con_estado, sin_estado)):
            with self.subTest(orden=[id(item) for item in orden]):
                consolidados = vista.consolidar(orden)
                self.assertEqual(len(consolidados), 1)
                self.assertEqual(
                    consolidados[0]["estado_usuario"], vista.ESTADO_ACTIVO
                )
                self.assertEqual(consolidados[0]["puesto"], "Analista")
                self.assertEqual(
                    consolidados[0]["correo"], "juan@serviam.com"
                )

    def test_duplicado_sql_contradictorio_en_ambos_ordenes(self) -> None:
        activo = fila_sql(activo=True)
        inactivo = fila_sql(activo=False)
        for orden in ((activo, inactivo), (inactivo, activo)):
            with self.subTest(orden=[id(item) for item in orden]):
                consolidados = vista.consolidar(orden)
                self.assertEqual(len(consolidados), 1)
                fila = consolidados[0]
                self.assertTrue(fila["estado_contradictorio"])
                self.assertEqual(
                    fila["estado_usuario"], vista.ESTADO_NO_DISPONIBLE
                )
                self.assertEqual(
                    vista.estado_candidato(fila),
                    vista.ESTADO_NO_DISPONIBLE,
                )
                self.assertFalse(vista.es_inactivo(fila))

    def test_duplicado_excel_contradictorio_en_ambos_ordenes(self) -> None:
        activo = fila_maestro(activo=True)
        inactivo = fila_maestro(activo=False)
        for orden in ((activo, inactivo), (inactivo, activo)):
            with self.subTest(orden=[id(item) for item in orden]):
                consolidados = vista.consolidar(orden)
                self.assertEqual(len(consolidados), 1)
                fila = consolidados[0]
                self.assertEqual(
                    fila["procedencia"], vista.PROCEDENCIA_EXCEL
                )
                self.assertTrue(fila["estado_contradictorio"])
                self.assertEqual(
                    fila["estado_usuario"], vista.ESTADO_NO_DISPONIBLE
                )
                self.assertFalse(vista.es_inactivo(fila))

    def test_contradiccion_entre_campos_de_una_fila_marca_el_estado(self) -> None:
        preparado = vista.preparar_candidato(fila_sql(activo=True, ACTIVO=False))
        self.assertTrue(preparado["estado_contradictorio"])
        self.assertEqual(
            preparado["estado_usuario"], vista.ESTADO_NO_DISPONIBLE
        )
        self.assertEqual(
            vista.estado_candidato(preparado), vista.ESTADO_NO_DISPONIBLE
        )
        self.assertFalse(vista.es_inactivo(preparado))

    def test_contradiccion_entre_campos_de_duplicados_distintos(self) -> None:
        consolidados = vista.consolidar(
            [
                fila_sql(activo=None, ESTADO_EMPLEADO="CESA"),
                fila_sql(activo=True, ESTADO_EMPLEADO="ACTIVO"),
            ]
        )
        self.assertEqual(len(consolidados), 1)
        self.assertTrue(consolidados[0]["estado_contradictorio"])

    def test_valores_coincidentes_conservan_el_estado(self) -> None:
        casos = (
            (True, "1", vista.ESTADO_ACTIVO),
            (False, "CESA", vista.ESTADO_INACTIVO),
        )
        for primero, segundo, esperado in casos:
            with self.subTest(esperado=esperado):
                consolidados = vista.consolidar(
                    [fila_sql(activo=primero), fila_sql(activo=segundo)]
                )
                self.assertEqual(len(consolidados), 1)
                self.assertFalse(consolidados[0]["estado_contradictorio"])
                self.assertEqual(
                    consolidados[0]["estado_usuario"], esperado
                )

    def test_conocido_y_vacio_completa_en_ambos_ordenes(self) -> None:
        vacio = fila_sql(activo=None)
        conocido = fila_sql(activo=False)
        for orden in ((vacio, conocido), (conocido, vacio)):
            with self.subTest(orden=[id(item) for item in orden]):
                consolidados = vista.consolidar(orden)
                self.assertEqual(len(consolidados), 1)
                self.assertFalse(consolidados[0]["estado_contradictorio"])
                self.assertEqual(
                    consolidados[0]["estado_usuario"],
                    vista.ESTADO_INACTIVO,
                )

    def test_valor_desconocido_no_contradice_al_conocido(self) -> None:
        desconocido = fila_sql(activo="DESCONOCIDO")
        conocido = fila_sql(activo=True)
        for orden in ((desconocido, conocido), (conocido, desconocido)):
            with self.subTest(orden=[id(item) for item in orden]):
                consolidados = vista.consolidar(orden)
                self.assertEqual(len(consolidados), 1)
                self.assertFalse(consolidados[0]["estado_contradictorio"])
                self.assertEqual(
                    consolidados[0]["estado_usuario"],
                    vista.ESTADO_ACTIVO,
                )

    def test_procedencias_distintas_no_se_fusionan_ni_contradicen(self) -> None:
        excel = fila_maestro(nombre="JUAN PEREZ", activo=True)
        sql = fila_sql(
            nombre="JUAN PEREZ",
            empresa="FILASUR",
            empleado="E999",
            activo=False,
        )
        consolidados = vista.consolidar([excel, sql])

        self.assertEqual(len(consolidados), 2)
        for candidato in consolidados:
            self.assertFalse(candidato["estado_contradictorio"])
        self.assertEqual(
            {candidato["estado_usuario"] for candidato in consolidados},
            {vista.ESTADO_ACTIVO, vista.ESTADO_INACTIVO},
        )

    def test_entradas_originales_no_se_mutan_al_detectar_contradiccion(self) -> None:
        primero = fila_sql(activo=True)
        segundo = fila_sql(activo=False)
        vista.consolidar([primero, segundo])

        for original in (primero, segundo):
            self.assertNotIn("estado_contradictorio", original)
            self.assertNotIn("estado_usuario", original)
            self.assertNotIn("procedencia", original)
        self.assertTrue(primero["activo"])
        self.assertFalse(segundo["activo"])

    def test_resumen_cuenta_contradiccion_como_no_disponible(self) -> None:
        consolidados = vista.consolidar(
            [fila_sql(activo=False), fila_sql(activo=True)]
        )
        conteo = vista.resumen(consolidados)

        self.assertEqual(conteo["total"], 1)
        self.assertEqual(conteo["activos"], 0)
        self.assertEqual(conteo["inactivos"], 0)
        self.assertEqual(conteo["no_disponibles"], 1)

    def test_estado_se_completa_entre_excel_y_sql_confirmados(self) -> None:
        excel = fila_maestro(empresa="SERVIAM", empleado="E001")
        sql = fila_sql(
            empresa="SERVIAM", empleado="E001", activo=True
        )
        consolidados = vista.consolidar([excel, sql])

        self.assertEqual(
            {candidato["estado_usuario"] for candidato in consolidados},
            {vista.ESTADO_ACTIVO},
        )

    def test_estado_contradictorio_entre_fuentes_confirmadas_no_se_infiere(self) -> None:
        excel = fila_maestro(
            empresa="SERVIAM", empleado="E001", activo=True
        )
        sql = fila_sql(empresa="SERVIAM", empleado="E001", activo=False)
        consolidados = vista.consolidar([excel, sql])

        self.assertEqual(
            {candidato["estado_usuario"] for candidato in consolidados},
            {vista.ESTADO_NO_DISPONIBLE},
        )
        for candidato in consolidados:
            self.assertTrue(candidato["estado_contradictorio"])
            self.assertEqual(
                vista.estado_candidato(candidato),
                vista.ESTADO_NO_DISPONIBLE,
            )

    def test_cruce_confirmado_con_contradiccion_interna_marca_ambos(self) -> None:
        excel = fila_maestro(empresa="SERVIAM", empleado="E001")
        duplicado_1 = fila_sql(empresa="SERVIAM", empleado="E001", activo=True)
        duplicado_2 = fila_sql(empresa="SERVIAM", empleado="E001", activo=False)
        consolidados = vista.consolidar([excel, duplicado_1, duplicado_2])

        self.assertEqual(len(consolidados), 2)
        for candidato in consolidados:
            self.assertTrue(candidato["estado_contradictorio"])
            self.assertEqual(
                candidato["estado_usuario"], vista.ESTADO_NO_DISPONIBLE
            )
            self.assertEqual(
                vista.estado_candidato(candidato),
                vista.ESTADO_NO_DISPONIBLE,
            )
            self.assertFalse(vista.es_inactivo(candidato))

    def test_cruce_confirmado_con_estados_iguales_no_marca_contradiccion(self) -> None:
        excel = fila_maestro(
            empresa="SERVIAM", empleado="E001", activo=True
        )
        sql = fila_sql(empresa="SERVIAM", empleado="E001", activo=True)
        consolidados = vista.consolidar([excel, sql])

        for candidato in consolidados:
            self.assertFalse(candidato["estado_contradictorio"])
            self.assertEqual(candidato["estado_usuario"], vista.ESTADO_ACTIVO)

    def test_ignora_entradas_que_no_son_diccionarios(self) -> None:
        consolidados = vista.consolidar([fila_sql(), None, "texto", 42])  # type: ignore[list-item]
        self.assertEqual(len(consolidados), 1)


class PruebasResumenYFiltros(unittest.TestCase):
    def test_resumen_cuenta_filas_por_fuente_estado_y_cruce(self) -> None:
        filas = vista.consolidar(
            [
                fila_maestro(nombre="JUAN PEREZ"),
                fila_sql(
                    nombre="MARIA LOPEZ",
                    empleado="E100",
                    activo=True,
                ),
                fila_sql(
                    nombre="PEDRO RAMOS",
                    empleado="E200",
                    activo=False,
                    estado_empleado="CESA",
                ),
                fila_sql(
                    nombre="LUCIA SOTO",
                    empleado="E300",
                    activo=None,
                ),
            ]
        )
        conteo = vista.resumen(filas)

        self.assertEqual(conteo["total"], 4)
        self.assertEqual(conteo["excel"], 1)
        self.assertEqual(conteo["sql"], 3)
        self.assertEqual(conteo["activos"], 1)
        self.assertEqual(conteo["inactivos"], 1)
        self.assertEqual(conteo["no_disponibles"], 2)
        self.assertEqual(conteo["filas_cruce_confirmado"], 0)
        self.assertEqual(conteo["filas_cruce_posible"], 0)

    def test_resumen_cuenta_cruces_por_fila_marcada(self) -> None:
        filas = vista.consolidar(
            [
                fila_maestro(empresa="SERVIAM", empleado="E001"),
                fila_sql(empresa="SERVIAM", empleado="E001"),
            ]
        )
        conteo = vista.resumen(filas)
        self.assertEqual(conteo["filas_cruce_confirmado"], 2)

    def test_filtrar_por_pestana_conserva_orden(self) -> None:
        filas = vista.consolidar(
            [
                fila_sql(nombre="UNO", empleado="E1"),
                fila_maestro(nombre="DOS"),
                fila_sql(nombre="TRES", empleado="E3"),
            ]
        )
        todos = vista.filtrar(filas, vista.PESTANA_TODOS)
        self.assertEqual([fila["nombre"] for fila in todos], ["UNO", "DOS", "TRES"])

        sql = vista.filtrar(filas, vista.PESTANA_SQL)
        self.assertEqual([fila["nombre"] for fila in sql], ["UNO", "TRES"])

        excel = vista.filtrar(filas, vista.PESTANA_EXCEL)
        self.assertEqual([fila["nombre"] for fila in excel], ["DOS"])

    def test_pestana_desconocida_es_error(self) -> None:
        with self.assertRaises(ValueError):
            vista.filtrar([fila_sql()], "Otra")


# ---------------------------------------------------------------------------
# Combinación de candidatos y apertura de la ventana
# ---------------------------------------------------------------------------
class PruebasCombinarCandidatos(unittest.TestCase):
    def setUp(self) -> None:
        from ventana_validacion import _combinar_candidatos

        self.combinar = _combinar_candidatos

    def test_lista_nueva_vacia_conserva_los_previos(self) -> None:
        previos = [
            fila_sql(empresa="SERVIAM", empleado="E1"),
            fila_sql(empresa="FILASUR", empleado="E2"),
        ]
        combinados = self.combinar(previos, [])

        self.assertEqual(
            [(item["empresa"], item["empleado"]) for item in combinados],
            [("SERVIAM", "E1"), ("FILASUR", "E2")],
        )

    def test_sin_previos_usa_los_nuevos(self) -> None:
        combinados = self.combinar(None, [fila_sql(empleado="E9")])
        self.assertEqual(len(combinados), 1)
        self.assertEqual(combinados[0]["empleado"], "E9")

    def test_union_sin_perdida_cuando_ambas_listas_tienen_datos(self) -> None:
        previos = [fila_sql(empresa="SERVIAM", empleado="E1")]
        nuevos = [fila_sql(empresa="FILASUR", empleado="E2")]
        combinados = self.combinar(previos, nuevos)

        self.assertEqual(
            {item["empleado"] for item in combinados}, {"E1", "E2"}
        )

    def test_duplicado_completa_campos_faltantes_en_ambos_ordenes(self) -> None:
        pobre = fila_sql(empleado="E1", puesto="", correo="")
        rico = fila_sql(
            empleado="E1", puesto="Analista", correo="juan@serviam.com"
        )
        for previos, nuevos in (([rico], [pobre]), ([pobre], [rico])):
            with self.subTest(previos_ricos=previos is rico):
                combinados = self.combinar(previos, nuevos)
                self.assertEqual(len(combinados), 1)
                self.assertEqual(combinados[0]["puesto"], "Analista")
                self.assertEqual(
                    combinados[0]["correo"], "juan@serviam.com"
                )

    def test_no_muta_las_listas_ni_los_diccionarios(self) -> None:
        previo = fila_sql(empleado="E1", puesto="")
        nuevo = fila_sql(empleado="E1", puesto="Analista")
        previos, nuevos = [previo], [nuevo]

        self.combinar(previos, nuevos)

        self.assertEqual(previo["puesto"], "")
        self.assertEqual(nuevo["puesto"], "Analista")
        self.assertEqual(previos, [previo])
        self.assertEqual(nuevos, [nuevo])

    def test_vacios_y_none(self) -> None:
        self.assertEqual(self.combinar(None, None), [])
        self.assertEqual(self.combinar([], []), [])


class PruebasAbrirSeleccion(unittest.TestCase):
    """``_abrir_seleccion`` sin SQL real ni ventanas visibles."""

    USUARIO = "JUAN PEREZ"

    def _stub(
        self,
        *,
        vivo: dict | None,
        area_maestro: str = "",
    ) -> SimpleNamespace:
        revision = mock.Mock()
        revision.buscar.return_value = vivo
        maestro = mock.Mock()
        maestro.buscar.return_value = area_maestro
        elegir = mock.Mock(return_value=True)
        stub = SimpleNamespace(
            revision=revision,
            maestro=maestro,
            _elegir_candidato=elegir,
        )
        stub.elegir = elegir
        return stub

    def _abrir(self, stub: SimpleNamespace, registro: dict) -> dict:
        from ventana_validacion import VentanaValidacion

        VentanaValidacion._abrir_seleccion(stub, registro)
        return stub

    def test_combina_registro_recibido_con_el_vigente(self) -> None:
        from ventana_validacion import VentanaCoincidencias

        previos = [fila_sql(empresa="SERVIAM", empleado="E1")]
        vivos = [fila_sql(empresa="FILASUR", empleado="E2")]
        stub = self._stub(vivo={"usuario": self.USUARIO, "candidatos": vivos})

        with mock.patch.object(
            __import__("ventana_validacion", fromlist=["VentanaCoincidencias"]),
            "VentanaCoincidencias",
        ) as ventana:
            self._abrir(
                stub,
                {"usuario": self.USUARIO, "candidatos": previos},
            )

        candidatos = ventana.call_args.kwargs["candidatos"]
        self.assertEqual(
            {item["empleado"] for item in candidatos}, {"E1", "E2"}
        )

    def test_conserva_previos_si_el_vigente_no_tiene_candidatos(self) -> None:
        from ventana_validacion import VentanaCoincidencias

        previos = [fila_sql(empresa="SERVIAM", empleado="E1")]
        stub = self._stub(vivo={"usuario": self.USUARIO, "candidatos": []})

        with mock.patch.object(
            __import__("ventana_validacion", fromlist=["VentanaCoincidencias"]),
            "VentanaCoincidencias",
        ) as ventana:
            self._abrir(
                stub,
                {"usuario": self.USUARIO, "candidatos": previos},
            )

        candidatos = ventana.call_args.kwargs["candidatos"]
        self.assertEqual(len(candidatos), 1)
        self.assertEqual(candidatos[0]["empleado"], "E1")

    def test_con_candidatos_no_instancia_el_consolidador_sql(self) -> None:
        from ventana_validacion import VentanaCoincidencias

        previos = [fila_sql(empleado="E1")]
        stub = self._stub(vivo={"usuario": self.USUARIO, "candidatos": []})

        with mock.patch.object(
            __import__("ventana_validacion", fromlist=["VentanaCoincidencias"]),
            "VentanaCoincidencias",
        ), mock.patch(
            "database.coincidencias.ConsolidadorCoincidencias"
        ) as consolidador:
            self._abrir(
                stub,
                {"usuario": self.USUARIO, "candidatos": previos},
            )

        consolidador.assert_not_called()

    def test_fallback_usa_el_maestro_sin_repetir_sql(self) -> None:
        from ventana_validacion import VentanaCoincidencias

        stub = self._stub(vivo=None, area_maestro="CONTABILIDAD")
        maestro = fila_maestro(nombre="JUAN PEREZ")
        grupo = SimpleNamespace(candidatos=[maestro])

        with mock.patch(
            "database.coincidencias.ConsolidadorCoincidencias"
        ) as consolidador, mock.patch.object(
            __import__("ventana_validacion", fromlist=["VentanaCoincidencias"]),
            "VentanaCoincidencias",
        ) as ventana:
            consolidador.return_value.buscar.return_value = {"clave": grupo}
            self._abrir(
                stub, {"usuario": self.USUARIO, "candidatos": []}
            )

        consolidador.return_value.buscar.assert_called_once_with(
            [self.USUARIO], usar_sql=False
        )
        candidatos = ventana.call_args.kwargs["candidatos"]
        self.assertEqual(len(candidatos), 1)
        self.assertEqual(
            candidatos[0]["procedencia"], vista.PROCEDENCIA_EXCEL
        )
        self.assertEqual(candidatos[0]["AREA_MAESTRO"], "CONTABILIDAD")

    def test_sin_coincidencias_ni_maestro_no_abre_ventana_ni_consulta_sql(self) -> None:
        stub = self._stub(vivo=None)

        with mock.patch(
            "database.coincidencias.ConsolidadorCoincidencias"
        ) as consolidador, mock.patch.object(
            __import__("ventana_validacion", fromlist=["VentanaCoincidencias"]),
            "VentanaCoincidencias",
        ) as ventana, mock.patch(
            "ventana_validacion.messagebox.askyesno", return_value=False
        ) as preguntar, mock.patch(
            "ventana_validacion.VentanaConsultaSqlManual"
        ) as manual:
            consolidador.return_value.buscar.return_value = {}
            self._abrir(
                stub, {"usuario": self.USUARIO, "candidatos": []}
            )

        ventana.assert_not_called()
        manual.assert_not_called()
        preguntar.assert_called_once()
        consolidador.return_value.buscar.assert_called_once_with(
            [self.USUARIO], usar_sql=False
        )

    def test_sin_maestro_y_usuario_acepta_abre_consulta_manual(self) -> None:
        stub = self._stub(vivo=None)

        with mock.patch(
            "database.coincidencias.ConsolidadorCoincidencias"
        ) as consolidador, mock.patch(
            "ventana_validacion.messagebox.askyesno", return_value=True
        ), mock.patch(
            "ventana_validacion.VentanaConsultaSqlManual"
        ) as manual:
            consolidador.return_value.buscar.return_value = {}
            self._abrir(
                stub, {"usuario": self.USUARIO, "candidatos": []}
            )

        manual.assert_called_once()

    def test_los_callbacks_trabajan_con_el_registro_vigente(self) -> None:
        from ventana_validacion import VentanaCoincidencias

        previos = [fila_sql(empresa="SERVIAM", empleado="E1")]
        vigente = {"usuario": self.USUARIO, "candidatos": previos}
        stub = self._stub(vivo=vigente)
        candidato = fila_sql(empleado="E1")

        with mock.patch.object(
            __import__("ventana_validacion", fromlist=["VentanaCoincidencias"]),
            "VentanaCoincidencias",
        ) as ventana:
            self._abrir(
                stub,
                {"usuario": self.USUARIO, "candidatos": []},
            )

        ventana.call_args.kwargs["al_elegir"](candidato)
        stub.elegir.assert_called_once_with(vigente, candidato)


# ---------------------------------------------------------------------------
# Asignación asistida: tri-estado, compatibilidad y ejecución
# ---------------------------------------------------------------------------
class PruebasAsignacionTriEstatal(unittest.TestCase):
    def _preparar(self, datos: dict, area: str = "SISTEMAS"):
        return asistida.preparar_asignacion(
            "ANA TORRES", dict(datos, area=area)
        )

    def test_clasificacion_equivalente_a_la_capa_de_vista(self) -> None:
        casos = (
            {"activo": True},
            {"activo": False},
            {},
            {"activo": None, "ESTADO_EMPLEADO": "CESA"},
            {"activo": None, "ESTADO_EMPLEADO": "A"},
            {"activo": True, "ACTIVO": False},
            {"ACTIVO": "SÍ"},
            {"ACTIVO": "0"},
            {"ESTADO_EMPLEADO": "INAC"},
            {"ESTADO_EMPLEADO": "RETIRADO"},
            {"activo": "1"},
            {"activo": "0"},
        )
        for datos in casos:
            with self.subTest(datos=datos):
                preparacion = self._preparar(datos)
                self.assertEqual(
                    preparacion.estado, vista.estado_candidato(datos)
                )
                self.assertEqual(
                    preparacion.inactivo, vista.es_inactivo(datos)
                )

    def test_no_disponible_no_se_confunde_con_activo_ni_inactivo(self) -> None:
        preparacion = self._preparar({})

        self.assertEqual(preparacion.estado, asistida.ESTADO_NO_DISPONIBLE)
        self.assertIsNone(preparacion.activo)
        self.assertFalse(preparacion.inactivo)
        self.assertTrue(preparacion.no_disponible)
        self.assertNotIn("figura como inactivo", preparacion.mensaje)
        self.assertIn("No hay información confiable", preparacion.mensaje)

    def test_inactivo_exige_confirmacion_expresa(self) -> None:
        preparacion = self._preparar(
            {"activo": None, "ESTADO_EMPLEADO": "CESA"}
        )
        self.assertEqual(preparacion.estado, asistida.ESTADO_INACTIVO)
        self.assertIs(preparacion.activo, False)
        self.assertTrue(preparacion.inactivo)
        self.assertFalse(preparacion.no_disponible)
        self.assertIn("inactivo", preparacion.mensaje.lower())

    def test_campos_contradictorios_dan_no_disponible(self) -> None:
        preparacion = self._preparar({"activo": True, "ACTIVO": False})
        self.assertEqual(preparacion.estado, asistida.ESTADO_NO_DISPONIBLE)
        self.assertIsNone(preparacion.activo)
        self.assertFalse(preparacion.inactivo)

    def test_candidato_consolidado_contradictorio_es_no_disponible(self) -> None:
        fila = vista.consolidar(
            [fila_sql(activo=False), fila_sql(activo=True)]
        )[0]
        self.assertTrue(fila["estado_contradictorio"])

        preparacion = asistida.preparar_asignacion("ANA TORRES", fila)

        self.assertEqual(preparacion.estado, asistida.ESTADO_NO_DISPONIBLE)
        self.assertIsNone(preparacion.activo)
        self.assertFalse(preparacion.inactivo)
        self.assertTrue(preparacion.no_disponible)
        self.assertIn("No hay información confiable", preparacion.mensaje)
        self.assertNotIn("figura como inactivo", preparacion.mensaje)

    def test_post_init_alinea_construcciones_historicas(self) -> None:
        manual = asistida.PreparacionAsignacion(
            "ANA", {}, area="SISTEMAS", activo=False
        )
        self.assertEqual(manual.estado, asistida.ESTADO_INACTIVO)
        self.assertTrue(manual.inactivo)

        explicita = asistida.PreparacionAsignacion(
            "ANA", {}, area="SISTEMAS", estado=asistida.ESTADO_INACTIVO
        )
        self.assertIs(explicita.activo, False)
        self.assertTrue(explicita.inactivo)

        vacia = asistida.PreparacionAsignacion("ANA", {})
        self.assertEqual(vacia.estado, asistida.ESTADO_NO_DISPONIBLE)
        self.assertIsNone(vacia.activo)
        self.assertFalse(vacia.inactivo)

    def test_compatibilidad_posicional_y_como_dict(self) -> None:
        preparacion = asistida.PreparacionAsignacion(
            "ANA", {}, "EMP", "ANA TORRES", "SISTEMAS", False, False, True,
            "mensaje",
        )
        self.assertEqual(preparacion.estado, asistida.ESTADO_INACTIVO)
        self.assertFalse(preparacion.activo)
        self.assertTrue(preparacion.inactivo)

        datos = preparacion.como_dict()
        for clave in (
            "usuario",
            "candidato",
            "empresa",
            "nombre_encontrado",
            "area",
            "activo",
            "inactivo",
            "requiere_confirmacion",
            "mensaje",
        ):
            self.assertIn(clave, datos)
        self.assertEqual(datos["estado"], asistida.ESTADO_INACTIVO)
        self.assertTrue(datos["no_disponible"] is False)


class PruebasEjecucionAsignacion(unittest.TestCase):
    def setUp(self) -> None:
        self.maestro = object()
        self.publicar = mock.patch.object(
            asistida.eventos, "publicar_asignacion"
        ).start()
        self.addCleanup(mock.patch.stopall)

    def _preparar(self, datos: dict):
        return asistida.preparar_asignacion(
            "ANA TORRES", dict(datos, area="SISTEMAS")
        )

    def _ejecutar(self, preparacion, **kwargs):
        persistir = mock.Mock(return_value={"guardado": True})
        resultado = asistida.ejecutar_asignacion(
            self.maestro,
            preparacion,
            persistir=persistir,
            **kwargs,
        )
        return resultado, persistir

    def test_no_disponible_no_exige_permiso_de_inactivo(self) -> None:
        preparacion = self._preparar({})
        resultado, persistir = self._ejecutar(preparacion, confirmar=True)

        self.assertTrue(resultado.ejecutada)
        persistir.assert_called_once()
        self.assertIsNone(preparacion.activo)
        self.assertFalse(preparacion.inactivo)
        self.assertEqual(
            preparacion.estado, asistida.ESTADO_NO_DISPONIBLE
        )

    def test_inactivo_sin_permiso_no_se_ejecuta(self) -> None:
        preparacion = self._preparar({"activo": False})
        resultado, persistir = self._ejecutar(preparacion, confirmar=True)

        self.assertFalse(resultado.ejecutada)
        persistir.assert_not_called()
        self.assertIn("inactivo", resultado.mensaje.lower())

    def test_contradiccion_no_exige_permiso_de_inactivo(self) -> None:
        fila = vista.consolidar(
            [fila_sql(activo=False), fila_sql(activo=True)]
        )[0]
        preparacion = asistida.preparar_asignacion("ANA TORRES", fila)
        resultado, persistir = self._ejecutar(preparacion, confirmar=True)

        self.assertTrue(resultado.ejecutada)
        persistir.assert_called_once()
        self.assertEqual(
            preparacion.estado, asistida.ESTADO_NO_DISPONIBLE
        )
        self.assertFalse(preparacion.inactivo)

    def test_inactivo_con_permiso_expreso_se_ejecuta(self) -> None:
        preparacion = self._preparar({"activo": False})
        resultado, persistir = self._ejecutar(
            preparacion, confirmar=True, permitir_inactivo=True
        )

        self.assertTrue(resultado.ejecutada)
        persistir.assert_called_once()

    def test_inactivo_manual_tambien_queda_retenido(self) -> None:
        manual = asistida.PreparacionAsignacion(
            "ANA", {}, area="SISTEMAS", estado=asistida.ESTADO_INACTIVO
        )
        resultado, persistir = self._ejecutar(manual, confirmar=True)

        self.assertFalse(resultado.ejecutada)
        persistir.assert_not_called()

    def test_activo_solo_requiere_confirmacion(self) -> None:
        preparacion = self._preparar({"activo": True})
        resultado, persistir = self._ejecutar(preparacion, confirmar=True)

        self.assertTrue(resultado.ejecutada)
        persistir.assert_called_once()

    def test_sin_confirmacion_no_se_ejecuta(self) -> None:
        preparacion = self._preparar({"activo": True})
        resultado, persistir = self._ejecutar(preparacion)

        self.assertFalse(resultado.ejecutada)
        persistir.assert_not_called()

    def test_sin_area_no_se_ejecuta(self) -> None:
        preparacion = asistida.preparar_asignacion("ANA TORRES", {})
        resultado, persistir = self._ejecutar(preparacion, confirmar=True)

        self.assertFalse(resultado.ejecutada)
        persistir.assert_not_called()

    def test_persistencia_recibe_usuario_area_y_guardar(self) -> None:
        preparacion = self._preparar({"activo": True})
        resultado, persistir = self._ejecutar(preparacion, confirmar=True)

        args, kwargs = persistir.call_args
        self.assertEqual(args[0], self.maestro)
        self.assertEqual(args[1], preparacion.usuario)
        self.assertEqual(args[2], preparacion.area)
        self.assertTrue(kwargs["guardar"])
        self.assertTrue(resultado.ejecutada)

    def test_el_evento_incluye_el_estado_sin_modificarlo(self) -> None:
        preparacion = self._preparar({})
        self._ejecutar(preparacion, confirmar=True)

        self.publicar.assert_called_once()
        datos_evento = self.publicar.call_args.kwargs["datos"]
        self.assertEqual(
            datos_evento["estado"], asistida.ESTADO_NO_DISPONIBLE
        )
        self.assertFalse(datos_evento["inactivo"])
        self.assertEqual(
            preparacion.estado, asistida.ESTADO_NO_DISPONIBLE
        )


class PruebasResolverAreasConContradiccion(unittest.TestCase):
    """Caracterización del comportamiento actual de ``ResolverAreas``.

    Estas pruebas documentan que ``ResolverAreas`` no conoce la marca
    ``estado_contradictorio``. No corrigen el resolvedor: solo dejan constancia
    de que su resultado depende del primer valor crudo del candidato.
    """

    def _resolver(self, candidato: dict):
        from database.resolucion import ResolverAreas

        resolver = ResolverAreas(
            [], cargar_procedencia=lambda: {"registros": {}}
        )
        return resolver.resolver_candidato("JUAN PEREZ", candidato)

    def test_resolver_ignora_la_marca_y_depende_del_primer_valor(self) -> None:
        from database.resolucion import CASO_A, CASO_E

        base = {
            "empresa": "SERVIAM",
            "empleado": "E001",
            "nombre": "JUAN PEREZ",
            "departamento": "SISTEMAS",
            "area": "SISTEMAS",
            "fuente": "EMPLEADO",
            "origen": "SQL_SERVER",
        }
        escenarios = (
            (dict(base, activo=False), dict(base, activo=True), CASO_E),
            (dict(base, activo=True), dict(base, activo=False), CASO_A),
        )
        for primero, segundo, caso_esperado in escenarios:
            with self.subTest(caso=caso_esperado):
                consolidado = vista.consolidar([primero, segundo])[0]
                self.assertTrue(consolidado["estado_contradictorio"])
                self.assertEqual(
                    vista.estado_candidato(consolidado),
                    vista.ESTADO_NO_DISPONIBLE,
                )

                resolucion = self._resolver(consolidado)

                # Documentado: el resolvedor no ve la contradicción y usa el
                # primer valor crudo conservado por la consolidación.
                self.assertEqual(resolucion.caso, caso_esperado)
                self.assertEqual(
                    resolucion.empleado.inactivo, caso_esperado == CASO_E
                )

    def test_resolver_candidatos_conserva_el_primer_empleado_de_la_consulta(self) -> None:
        from database.repository import Empleado
        from database.resolucion import CASO_A, CASO_E, ResolverAreas

        def empleado(activo: bool) -> Empleado:
            return Empleado(
                empresa="SERVIAM",
                codigo="E001",
                nombre="JUAN PEREZ",
                departamento="SISTEMAS",
                fuente="EMPLEADO",
                activo=activo,
            )

        resolver = ResolverAreas(
            [], cargar_procedencia=lambda: {"registros": {}}
        )
        escenarios = (
            ((empleado(True), empleado(False)), CASO_A),
            ((empleado(False), empleado(True)), CASO_E),
        )
        for candidatos, caso_esperado in escenarios:
            with self.subTest(caso=caso_esperado):
                resolucion = resolver.resolver_candidatos(
                    "JUAN PEREZ", candidatos
                )
                self.assertEqual(resolucion.caso, caso_esperado)
                self.assertEqual(
                    resolucion.empleado.activo, caso_esperado == CASO_A
                )


# ---------------------------------------------------------------------------
# Ventana de coincidencias: botón inactivo y conservación de la selección
# ---------------------------------------------------------------------------
class PruebasVentanaCoincidencias(unittest.TestCase):
    """Pruebas de interfaz (se omiten si no hay entorno gráfico)."""

    def setUp(self) -> None:
        import tkinter as tk

        try:
            self.raiz = tk.Tk()
        except tk.TclError as exc:  # pragma: no cover - sin entorno gráfico
            self.skipTest(f"Tk no disponible: {exc}")
        self.raiz.withdraw()
        self.ventanas: list = []

    def tearDown(self) -> None:
        for ventana in self.ventanas:
            try:
                ventana.destroy()
            except Exception:  # noqa: BLE001
                pass
        try:
            self.raiz.destroy()
        except Exception:  # noqa: BLE001
            pass

    def _abrir(self, candidatos, *, elegir=None, inactivo=None):
        import tkinter as tk

        from ventana_validacion import VentanaCoincidencias

        with mock.patch.object(tk.Toplevel, "grab_set", lambda self: None):
            ventana = VentanaCoincidencias(
                self.raiz,
                usuario="JUAN PEREZ",
                candidatos=candidatos,
                al_elegir=elegir or (lambda candidato: True),
                al_asignar_inactivo=inactivo or (lambda candidato: True),
            )
        self.ventanas.append(ventana)
        ventana.update_idletasks()
        return ventana

    def _iid_por_estado(self, ventana, estado: str) -> str:
        for iid in ventana._tabla.get_children():
            if ventana._tabla.set(iid, "activo") == estado:
                return iid
        raise AssertionError(f"No hay fila con estado {estado}")

    def test_solo_inactivo_inequivoco_habilita_la_asignacion_especial(self) -> None:
        ventana = self._abrir(
            [
                fila_sql(empleado="E1", activo=True),
                fila_sql(empleado="E2", activo=False, estado_empleado="CESA"),
                fila_sql(empleado="E3", activo=None),
            ]
        )

        esperados = {
            "ACTIVO": "disabled",
            "INACTIVO": "normal",
            "NO DISPONIBLE": "disabled",
        }
        for estado, esperado in esperados.items():
            with self.subTest(estado=estado):
                iid = self._iid_por_estado(ventana, estado)
                ventana._tabla.selection_set(iid)
                ventana._actualizar_boton()
                self.assertEqual(
                    str(ventana._btn_asignar_inactivo.cget("state")), esperado
                )

    def test_no_disponible_no_dispara_la_asignacion_especial(self) -> None:
        llamadas: list = []
        ventana = self._abrir(
            [fila_sql(empleado="E3", activo=None)],
            inactivo=llamadas.append,
        )
        iid = ventana._tabla.get_children()[0]
        ventana._tabla.selection_set(iid)
        ventana._actualizar_boton()

        ventana._asignar_inactivo()

        self.assertEqual(llamadas, [])
        self.assertTrue(ventana.winfo_exists())
        self.assertEqual(ventana._tabla.selection(), (iid,))

    def test_duplicado_contradictorio_no_habilita_asignacion_inactiva(self) -> None:
        llamadas: list = []
        ventana = self._abrir(
            [
                fila_sql(empleado="E1", activo=False),
                fila_sql(empleado="E1", activo=True),
            ],
            inactivo=llamadas.append,
        )

        self.assertEqual(len(ventana._tabla.get_children()), 1)
        iid = ventana._tabla.get_children()[0]
        self.assertEqual(
            ventana._tabla.set(iid, "activo"), vista.ESTADO_NO_DISPONIBLE
        )

        ventana._tabla.selection_set(iid)
        ventana._actualizar_boton()
        self.assertEqual(
            str(ventana._btn_asignar_inactivo.cget("state")), "disabled"
        )

        ventana._asignar_inactivo()

        self.assertEqual(llamadas, [])
        self.assertTrue(ventana.winfo_exists())
        self.assertEqual(ventana._tabla.selection(), (iid,))

    def test_cancelacion_o_conflicto_conserva_ventana_y_seleccion(self) -> None:
        ventana = self._abrir(
            [fila_sql(empleado="E1", activo=True)],
            elegir=lambda candidato: False,
        )
        iid = ventana._tabla.get_children()[0]
        ventana._tabla.selection_set(iid)
        ventana._actualizar_boton()

        ventana._elegir()

        self.assertTrue(ventana.winfo_exists())
        self.assertEqual(ventana._tabla.selection(), (iid,))

    def test_error_al_asignar_conserva_ventana_y_seleccion(self) -> None:
        def fallar(candidato):
            raise RuntimeError("conflicto de área")

        ventana = self._abrir(
            [fila_sql(empleado="E1", activo=True)], elegir=fallar
        )
        iid = ventana._tabla.get_children()[0]
        ventana._tabla.selection_set(iid)
        ventana._actualizar_boton()

        with self.assertRaises(RuntimeError):
            ventana._elegir()

        self.assertTrue(ventana.winfo_exists())
        self.assertEqual(ventana._tabla.selection(), (iid,))

    def test_exito_cierra_la_ventana(self) -> None:
        ventana = self._abrir(
            [fila_sql(empleado="E1", activo=True)],
            elegir=lambda candidato: True,
        )
        ventana._tabla.selection_set(ventana._tabla.get_children()[0])
        ventana._actualizar_boton()

        ventana._elegir()

        self.assertFalse(ventana.winfo_exists())

    def test_fallo_de_asignacion_inactiva_conserva_ventana(self) -> None:
        ventana = self._abrir(
            [fila_sql(empleado="E1", activo=False, estado_empleado="CESA")],
            inactivo=lambda candidato: False,
        )
        iid = ventana._tabla.get_children()[0]
        ventana._tabla.selection_set(iid)
        ventana._actualizar_boton()

        ventana._asignar_inactivo()

        self.assertTrue(ventana.winfo_exists())
        self.assertEqual(ventana._tabla.selection(), (iid,))

    def test_pestanas_filtran_y_muestran_conteos(self) -> None:
        ventana = self._abrir(
            [
                fila_maestro(nombre="JUAN PEREZ"),
                fila_sql(nombre="JUAN PEREZ GOMEZ", empleado="E1"),
                fila_sql(nombre="JUAN PEREZ GOMEZ", empleado="E2"),
            ]
        )

        self.assertEqual(
            ventana._botones_pestana[vista.PESTANA_TODOS].cget("text"),
            "Todos (3)",
        )
        self.assertEqual(
            ventana._botones_pestana[vista.PESTANA_EXCEL].cget("text"),
            "Excel maestro (1)",
        )
        self.assertEqual(
            ventana._botones_pestana[vista.PESTANA_SQL].cget("text"),
            "Base de datos SQL (2)",
        )

        ventana._cambiar_pestana(vista.PESTANA_EXCEL)
        self.assertEqual(len(ventana._tabla.get_children()), 1)

        ventana._cambiar_pestana(vista.PESTANA_SQL)
        self.assertEqual(len(ventana._tabla.get_children()), 2)

        ventana._cambiar_pestana(vista.PESTANA_TODOS)
        self.assertEqual(len(ventana._tabla.get_children()), 3)


if __name__ == "__main__":
    unittest.main()
