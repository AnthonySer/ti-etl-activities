"""Pruebas de la consulta SQL multiempresa y la selección manual de empleados.

Cubre los 24 escenarios obligatorios del plan
``docs/consulta-sql-multiempresa.md``. Todas las pruebas usan dobles de prueba y
archivos temporales: nunca se conecta ni se escribe en una base productiva.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openpyxl import Workbook

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config.sqlserver import ConfiguracionSQL  # noqa: E402
from core import revision as svc_revision  # noqa: E402
from core import seleccion_empleados as sel  # noqa: E402
from core.archivos import ErrorEstructura  # noqa: E402
from core.esquema import C_SOLICITANTE  # noqa: E402
from core.maestro_tickets import MaestroTickets  # noqa: E402
from database import queries  # noqa: E402
from database.connection import ErrorConexionSQL  # noqa: E402
from database.repository import Empleado, RepositorioEmpleados  # noqa: E402
from database.resolucion import (  # noqa: E402
    CASO_A,
    CASO_B,
    CASO_D,
    CASO_E,
    ESTADO_AMBIGUO,
    ESTADO_INACTIVO,
    ESTADO_SIN_DEPARTAMENTO,
    ESTADO_VALIDADO,
    ResolverAreas,
)

# ---------------------------------------------------------------------------
# Datos de esquema simulados
# ---------------------------------------------------------------------------
COLUMNAS_COMPLETAS: dict[str, set[str]] = {
    "EMPLEADO": {
        "EMPLEADO",
        "NOMBRE",
        "PRIMER_APELLIDO",
        "SEGUNDO_APELLIDO",
        "NOMBRE_PILA",
        "ESTADO_EMPLEADO",
        "E_MAIL",
        "U_CORREO_CORP",
        "DEPARTAMENTO",
        "PUESTO",
        "ACTIVO",
    },
    "DEPARTAMENTO": {"DEPARTAMENTO", "DESCRIPCION"},
    "PUESTO": {"PUESTO", "DESCRIPCION"},
}


def sql_de(esquema: str, columnas: dict[str, set[str]], cantidad: int = 0) -> str:
    sql, _ = queries.sql_empleados(
        base_datos="EXACTUS",
        esquema=esquema,
        cantidad=cantidad,
        columnas_disponibles=columnas,
    )
    return sql


def candidato(
    nombre: str,
    empresa: str,
    departamento: str,
    *,
    codigo: str = "100",
    activo: bool | None = True,
    fuente: str = "EMPLEADO",
    puesto: str = "",
    correo: str = "",
) -> dict[str, Any]:
    return Empleado(
        empresa=empresa,
        codigo=codigo,
        nombre=nombre,
        departamento=departamento,
        puesto=puesto,
        activo=activo,
        fuente=fuente,
        email=correo,
    ).como_dict()


def resolver(empleados: list[Empleado], maestro: Any = None) -> ResolverAreas:
    return ResolverAreas(
        empleados,
        maestro=maestro,
        cargar_procedencia=lambda: {"registros": {}},
    )


# ---------------------------------------------------------------------------
# Repositorio falso: metadatos y filas por empresa
# ---------------------------------------------------------------------------
@dataclass
class EsquemaFalso:
    columnas: dict[str, list[str]]
    filas: list[dict[str, Any]] = field(default_factory=list)
    error_metadatos: Exception | None = None
    error_consulta: Exception | None = None


def _empresa_de(sql: str, parametros: list[Any], esquemas: dict[str, EsquemaFalso]) -> str:
    if "INFORMATION_SCHEMA.COLUMNS" in sql:
        return str(parametros[0]) if parametros else ""
    for empresa in esquemas:
        if f"[{empresa}]" in sql:
            return empresa
    return ""


def repositorio_falso(
    config: ConfiguracionSQL,
    esquemas: dict[str, EsquemaFalso],
    contador_metadatos: dict[str, int] | None = None,
) -> RepositorioEmpleados:
    @contextmanager
    def conexion():
        yield object()

    def fabrica(_config):
        return conexion()

    def ejecutor(_conexion, sql, parametros):
        empresa = _empresa_de(sql, parametros, esquemas)
        esquema = esquemas.get(empresa)
        if esquema is None:
            return []
        if "INFORMATION_SCHEMA.COLUMNS" in sql:
            if contador_metadatos is not None:
                contador_metadatos[empresa] = contador_metadatos.get(empresa, 0) + 1
            if esquema.error_metadatos is not None:
                raise esquema.error_metadatos
            return [
                {"TABLE_NAME": tabla, "COLUMN_NAME": columna}
                for tabla, columnas in esquema.columnas.items()
                for columna in columnas
            ]
        if esquema.error_consulta is not None:
            raise esquema.error_consulta
        return list(esquema.filas)

    return RepositorioEmpleados(
        config, fabrica_conexion=fabrica, ejecutor=ejecutor
    )


def config_dos_empresas(lote: int = 50) -> ConfiguracionSQL:
    return ConfiguracionSQL(
        habilitado=True,
        servidor="10.0.0.1",
        base_datos="EXACTUS",
        autenticacion="windows",
        empresas=["SERVIAM", "FILASUR"],
        lote=lote,
    )


def columnas_simples() -> dict[str, list[str]]:
    return {
        "EMPLEADO": ["EMPLEADO", "NOMBRE", "DEPARTAMENTO", "ACTIVO"],
    }


# ===========================================================================
# Escenarios 1 a 6: consulta adaptativa según columnas disponibles
# ===========================================================================
class PruebasConsultaAdaptativa(unittest.TestCase):
    def test_01_empresa_con_todas_las_columnas(self) -> None:
        sql = sql_de("SERVIAM", COLUMNAS_COMPLETAS)
        self.assertIn("E.[U_CORREO_CORP] AS U_CORREO_CORP", sql)
        self.assertIn("E.[E_MAIL] AS CORREO", sql)
        self.assertIn("E.[ACTIVO] AS ACTIVO", sql)
        self.assertIn("D.[DESCRIPCION] AS DEPARTAMENTO", sql)
        self.assertIn("P.[DESCRIPCION] AS PUESTO", sql)
        self.assertIn("LEFT JOIN [EXACTUS].[SERVIAM].[DEPARTAMENTO] D", sql)
        self.assertIn("LEFT JOIN [EXACTUS].[SERVIAM].[PUESTO] P", sql)

    def test_02_empresa_sin_u_correo_corp(self) -> None:
        sql = sql_de(
            "ANDINA",
            {
                "EMPLEADO": {
                    "EMPLEADO",
                    "NOMBRE",
                    "DEPARTAMENTO",
                    "PUESTO",
                    "ACTIVO",
                    "E_MAIL",
                }
            },
        )
        self.assertNotIn("U_CORREO_CORP", sql)
        self.assertIn("E.[E_MAIL] AS CORREO", sql)

    def test_03_empresa_sin_puesto(self) -> None:
        sql = sql_de("CALCA", {"EMPLEADO": {"EMPLEADO", "NOMBRE", "ACTIVO"}})
        self.assertNotIn("E.[PUESTO]", sql)
        self.assertNotIn(".[PUESTO]", sql.split("FROM")[1] if "FROM" in sql else "")
        self.assertIn("NULL AS PUESTO", sql)

    def test_04_empresa_sin_activo(self) -> None:
        sql = sql_de("CALCA", {"EMPLEADO": {"EMPLEADO", "NOMBRE"}})
        self.assertIn("NULL AS ACTIVO", sql)
        self.assertNotIn("E.[ACTIVO]", sql)

    def test_05_empresa_sin_departamento(self) -> None:
        sql = sql_de("CALCA", {"EMPLEADO": {"EMPLEADO", "NOMBRE", "ACTIVO"}})
        self.assertIn("NULL AS DEPARTAMENTO_CODIGO", sql)
        self.assertIn("NULL AS DEPARTAMENTO", sql)
        self.assertNotIn("D.[DESCRIPCION]", sql)
        self.assertNotIn("LEFT JOIN [EXACTUS].[CALCA].[DEPARTAMENTO]", sql)

    def test_06_tabla_sin_campos_suficientes(self) -> None:
        config = config_dos_empresas()
        repo = repositorio_falso(
            config,
            {
                "SERVIAM": EsquemaFalso(
                    {"EMPLEADO": ["EMPLEADO", "ACTIVO"]},
                ),
            },
        )
        resultado = repo.obtener_todos(["SERVIAM"])
        self.assertEqual(resultado.empleados, [])
        self.assertEqual(len(resultado.errores), 1)
        self.assertEqual(
            resultado.errores[0]["categoria"], "campos_insuficientes"
        )
        self.assertIn("NOMBRE", resultado.errores[0]["mensaje"])


# ===========================================================================
# Escenarios 7 a 9, 21 y 22: aislamiento entre empresas
# ===========================================================================
class PruebasAislamientoEmpresas(unittest.TestCase):
    def test_07_tabla_empleado_no_existe(self) -> None:
        repo = repositorio_falso(
            config_dos_empresas(),
            {"SERVIAM": EsquemaFalso({"OTRA_TABLA": ["X"]})},
        )
        resultado = repo.obtener_todos(["SERVIAM"])
        self.assertEqual(resultado.empleados, [])
        self.assertEqual(resultado.empresas_sin_esquema, ["SERVIAM"])
        self.assertEqual(len(resultado.errores), 1)
        self.assertEqual(resultado.errores[0]["categoria"], "tabla")
        self.assertIn("No existe la tabla EMPLEADO", resultado.errores[0]["mensaje"])

    def test_08_empresa_con_error_de_conexion(self) -> None:
        repo = repositorio_falso(
            config_dos_empresas(),
            {
                "SERVIAM": EsquemaFalso(
                    columnas_simples(),
                    error_metadatos=ErrorConexionSQL(
                        "No se pudo conectar con el servidor", "servidor"
                    ),
                )
            },
        )
        resultado = repo.obtener_todos(["SERVIAM"])
        self.assertEqual(resultado.empleados, [])
        self.assertEqual(len(resultado.errores), 1)
        self.assertEqual(resultado.errores[0]["empresa"], "SERVIAM")
        self.assertEqual(resultado.errores[0]["categoria"], "servidor")

    def test_09_consulta_cero_empleados_no_es_error(self) -> None:
        repo = repositorio_falso(
            config_dos_empresas(),
            {"SERVIAM": EsquemaFalso(columnas_simples(), filas=[])},
        )
        resultado = repo.obtener_todos(["SERVIAM"])
        self.assertEqual(resultado.empleados, [])
        self.assertEqual(resultado.errores, [])
        self.assertEqual(resultado.empresas_sin_datos, ["SERVIAM"])
        self.assertEqual(resultado.empresas_consultadas, ["SERVIAM"])

    def test_21_una_empresa_falla_y_las_demas_continuan(self) -> None:
        repo = repositorio_falso(
            config_dos_empresas(),
            {
                "SERVIAM": EsquemaFalso(
                    columnas_simples(),
                    filas=[
                        {
                            "EMPLEADO": "100",
                            "NOMBRE": "ANA TORRES",
                            "DEPARTAMENTO": "CONTABILIDAD",
                            "ACTIVO": 1,
                        }
                    ],
                ),
                "FILASUR": EsquemaFalso(
                    columnas_simples(),
                    error_consulta=RuntimeError(
                        "Invalid column name 'FUENTE'."
                    ),
                ),
            },
        )
        resultado = repo.buscar_por_nombres(["ANA TORRES"])
        self.assertEqual(len(resultado.empleados), 1)
        self.assertEqual(resultado.empleados[0].empresa, "SERVIAM")
        self.assertEqual(len(resultado.errores), 1)
        self.assertEqual(resultado.errores[0]["empresa"], "FILASUR")
        self.assertEqual(resultado.errores[0]["categoria"], "columna")
        self.assertIn("SERVIAM", resultado.empresas_consultadas)

    def test_22_no_repite_advertencias_en_cada_lote(self) -> None:
        contador: dict[str, int] = {}
        repo = repositorio_falso(
            config_dos_empresas(lote=1),
            {
                "SERVIAM": EsquemaFalso(
                    columnas_simples(),
                    filas=[
                        {
                            "EMPLEADO": "100",
                            "NOMBRE": "ANA TORRES",
                            "DEPARTAMENTO": "CONTABILIDAD",
                            "ACTIVO": 1,
                        }
                    ],
                ),
                "FILASUR": EsquemaFalso(
                    columnas_simples(),
                    error_metadatos=ErrorConexionSQL(
                        "Servidor no disponible", "servidor"
                    ),
                ),
            },
            contador_metadatos=contador,
        )
        resultado = repo.buscar_por_nombres(
            ["ANA TORRES", "LUIS PEREZ", "CARLOS RUIZ"]
        )
        # Tres lotes de un nombre, pero los metadatos se leen una sola vez por
        # empresa y la advertencia de FILASUR no se duplica.
        self.assertEqual(contador.get("FILASUR"), 1)
        self.assertEqual(len(resultado.errores), 1)


# ===========================================================================
# Escenarios 10 a 18: identidad, fuentes y selección manual
# ===========================================================================
class PruebasIdentidadYSeleccion(unittest.TestCase):
    def test_10_mismo_empleado_en_dos_empresas(self) -> None:
        repo = repositorio_falso(
            config_dos_empresas(),
            {
                "SERVIAM": EsquemaFalso(
                    columnas_simples(),
                    filas=[
                        {
                            "EMPLEADO": "100",
                            "NOMBRE": "ANA TORRES",
                            "DEPARTAMENTO": "CONTABILIDAD",
                            "ACTIVO": 1,
                        }
                    ],
                ),
                "FILASUR": EsquemaFalso(
                    columnas_simples(),
                    filas=[
                        {
                            "EMPLEADO": "100",
                            "NOMBRE": "ANA TORRES",
                            "DEPARTAMENTO": "OPERACIONES",
                            "ACTIVO": 1,
                        }
                    ],
                ),
            },
        )
        consulta = repo.buscar_por_nombres(["ANA TORRES"])
        self.assertEqual(len(consulta.empleados), 2)
        resultado = resolver(consulta.empleados).resolver("ANA TORRES")
        self.assertEqual(resultado.caso, CASO_B)
        self.assertEqual(resultado.estado, ESTADO_AMBIGUO)
        self.assertFalse(resultado.asignable)
        self.assertFalse(resultado.area)
        self.assertEqual(
            {candidato.empresa for candidato in resultado.candidatos},
            {"SERVIAM", "FILASUR"},
        )

    def test_11_dos_empleados_distintos_mismo_nombre(self) -> None:
        empleados = [
            Empleado(
                empresa="SERVIAM",
                codigo="200",
                nombre="ANA TORRES",
                departamento="CONTABILIDAD",
                activo=True,
            ),
            Empleado(
                empresa="SERVIAM",
                codigo="201",
                nombre="ANA TORRES",
                departamento="SISTEMAS",
                activo=True,
            ),
        ]
        resultado = resolver(empleados).resolver("ANA TORRES")
        self.assertEqual(resultado.caso, CASO_B)
        self.assertEqual(len(resultado.candidatos), 2)
        self.assertEqual(
            {candidato.codigo for candidato in resultado.candidatos},
            {"200", "201"},
        )
        self.assertFalse(resultado.area)

    def test_12_usuario_en_dos_fuentes_de_la_misma_empresa(self) -> None:
        uno = candidato(
            "ANA TORRES",
            "SERVIAM",
            "CONTABILIDAD",
            codigo="100",
            fuente="EMPLEADO",
        )
        dos = candidato(
            "ANA TORRES",
            "SERVIAM",
            "CONTABILIDAD",
            codigo="100",
            fuente="VISTA_EMPLEADO",
        )
        # La identidad incluye empresa, fuente y código: no se fusionan.
        self.assertNotEqual(sel.identidad(uno), sel.identidad(dos))
        self.assertEqual(uno["fuente"], "EMPLEADO")
        self.assertEqual(dos["fuente"], "VISTA_EMPLEADO")
        visibles = sel.filtrar_candidatos([uno, dos], nombre="ANA")
        self.assertEqual(len(visibles), 2)
        self.assertEqual(
            {sel.valor(item, "fuente") for item in visibles},
            {"EMPLEADO", "VISTA_EMPLEADO"},
        )

    def test_13_activo_en_una_empresa_e_inactivo_en_otra(self) -> None:
        activo = Empleado(
            empresa="SERVIAM",
            codigo="100",
            nombre="ANA TORRES",
            departamento="CONTABILIDAD",
            activo=True,
        )
        inactivo = Empleado(
            empresa="FILASUR",
            codigo="100",
            nombre="ANA TORRES",
            departamento="OPERACIONES",
            activo=False,
            estado_empleado="CESADO",
        )
        resultado = resolver([activo, inactivo]).resolver("ANA TORRES")
        self.assertEqual(resultado.caso, CASO_B)
        etiquetas = {
            sel.valor(candidato.como_dict(), "activo")
            for candidato in resultado.candidatos
        }
        self.assertEqual(etiquetas, {"Activo", "Inactivo"})

        elegido = resolver([activo, inactivo]).resolver_candidato(
            "ANA TORRES", inactivo
        )
        self.assertEqual(elegido.caso, CASO_E)
        self.assertEqual(elegido.estado, ESTADO_INACTIVO)
        self.assertIn("inactivo", elegido.motivo)

    def test_14_seleccion_del_registro_de_serviam(self) -> None:
        serviam = candidato("ANA TORRES", "SERVIAM", "CONTABILIDAD", codigo="100")
        filasur = candidato("ANA TORRES", "FILASUR", "OPERACIONES", codigo="100")
        resultado = resolver([]).resolver_candidato("ANA TORRES", serviam)
        self.assertEqual(resultado.caso, CASO_A)
        self.assertEqual(resultado.estado, ESTADO_VALIDADO)
        self.assertEqual(resultado.area, "CONTABILIDAD")
        self.assertNotEqual(resultado.area, filasur["departamento"])

    def test_15_seleccion_del_registro_de_filasur(self) -> None:
        serviam = candidato("ANA TORRES", "SERVIAM", "CONTABILIDAD", codigo="100")
        filasur = candidato("ANA TORRES", "FILASUR", "OPERACIONES", codigo="100")
        resultado = resolver([]).resolver_candidato("ANA TORRES", filasur)
        self.assertEqual(resultado.caso, CASO_A)
        self.assertEqual(resultado.area, "OPERACIONES")
        self.assertNotEqual(resultado.area, serviam["departamento"])

    def test_16_cancelar_la_subventana_no_registra_seleccion(self) -> None:
        self.assertIsNone(sel.seleccion_desde_candidato(None))
        revision = svc_revision.Revision(
            id_ejecucion="CANCELAR",
            registros=[registro_revision()],
        )
        registro = revision.buscar("ANA TORRES")
        self.assertEqual(registro["seleccion"], {})
        self.assertEqual(registro["seleccionado"], {})
        self.assertFalse(registro["validado"])

    def test_17_usuario_sin_departamento(self) -> None:
        sin_depto = candidato("ANA TORRES", "CONDOR", "", codigo="100")
        resultado = resolver([]).resolver_candidato("ANA TORRES", sin_depto)
        self.assertEqual(resultado.caso, CASO_D)
        self.assertEqual(resultado.estado, ESTADO_SIN_DEPARTAMENTO)
        self.assertFalse(resultado.area)
        self.assertIn("departamento", resultado.motivo)

    def test_18_departamento_sin_equivalencia_no_asigna_area(self) -> None:
        # El registro se conserva, pero al no existir equivalencia válida el
        # área queda vacía y no se valida automáticamente.
        revision = svc_revision.Revision(
            id_ejecucion="SIN_EQUIVALENCIA",
            registros=[registro_revision()],
        )
        candidato_serviam = candidato(
            "ANA TORRES", "SERVIAM", "DEPARTAMENTO SIN MAPEO", codigo="100"
        )
        registro = revision.registrar_seleccion(
            "ANA TORRES",
            candidato_serviam,
            area="",
            estado=ESTADO_SIN_DEPARTAMENTO,
            motivo="El departamento no tiene una equivalencia de área.",
        )
        self.assertEqual(registro["seleccion"]["empresa"], "SERVIAM")
        self.assertEqual(registro["seleccion"]["departamento"], "DEPARTAMENTO SIN MAPEO")
        self.assertEqual(registro["area_actual"], "")
        self.assertFalse(registro["validado"])
        self.assertEqual(registro["estado"], ESTADO_SIN_DEPARTAMENTO)
        self.assertIn("equivalencia", registro["motivo"])


# ===========================================================================
# Escenarios 19 y 20: persistencia y validez de la selección guardada
# ===========================================================================
def registro_revision(usuario: str = "ANA TORRES") -> dict[str, Any]:
    from database.normalizador import normalizar_nombre

    return {
        "usuario": usuario,
        "clave": normalizar_nombre(usuario),
        "empresa": "",
        "ticket": "",
        "area_actual": "",
        "area_sugerida": "",
        "estado": ESTADO_AMBIGUO,
        "caso": CASO_B,
        "motivo": "Varias coincidencias.",
        "candidatos": [],
        "seleccionado": {},
        "seleccion": {},
        "trazabilidad": {},
        "origen": "",
        "validado": False,
        "filas": [],
        "historial": [],
    }


class PruebasPersistenciaSeleccion(unittest.TestCase):
    def test_19_seleccion_manual_se_conserva_para_otra_ejecucion(self) -> None:
        candidato_serviam = candidato(
            "ANA TORRES",
            "SERVIAM",
            "CONTABILIDAD",
            codigo="100",
            puesto="ANALISTA",
            correo="ana@serviam.pe",
        )
        revision = svc_revision.Revision(
            id_ejecucion="SEL_19",
            registros=[registro_revision()],
        )
        revision.registrar_seleccion(
            "ANA TORRES",
            candidato_serviam,
            area="CONTABILIDAD",
            motivo="Selección manual de SERVIAM.",
            tipo_resolucion="manual",
        )
        with tempfile.TemporaryDirectory(prefix="seleccion_") as carpeta:
            ruta = revision.guardar(Path(carpeta) / "revision_SEL_19.json")
            recuperada = svc_revision.Revision.cargar(ruta)
        registro = recuperada.buscar("ANA TORRES")
        self.assertIsNotNone(registro)
        self.assertEqual(registro["seleccion"]["empresa"], "SERVIAM")
        self.assertEqual(registro["seleccion"]["fuente"], "EMPLEADO")
        self.assertEqual(registro["seleccion"]["empleado"], "100")
        self.assertEqual(registro["trazabilidad"]["departamento"], "CONTABILIDAD")
        self.assertEqual(registro["trazabilidad"]["area"], "CONTABILIDAD")
        self.assertEqual(registro["trazabilidad"]["tipo_resolucion"], "manual")
        self.assertTrue(registro["trazabilidad"]["fecha"])
        self.assertTrue(recuperada.seleccion_guardada_valida("ANA TORRES"))

    def test_20_seleccion_guardada_deja_de_ser_valida_si_cambia_el_registro(self) -> None:
        original = candidato(
            "ANA TORRES", "SERVIAM", "CONTABILIDAD", codigo="100"
        )
        revision = svc_revision.Revision(
            id_ejecucion="SEL_20",
            registros=[registro_revision()],
        )
        revision.registrar_seleccion(
            "ANA TORRES",
            original,
            area="CONTABILIDAD",
            motivo="Selección inicial.",
        )
        self.assertTrue(revision.seleccion_guardada_valida("ANA TORRES"))

        otro_departamento = candidato(
            "ANA TORRES", "SERVIAM", "OPERACIONES", codigo="100"
        )
        self.assertFalse(
            revision.seleccion_guardada_valida("ANA TORRES", otro_departamento)
        )

        otra_empresa = candidato(
            "ANA TORRES", "FILASUR", "CONTABILIDAD", codigo="100"
        )
        self.assertFalse(
            revision.seleccion_guardada_valida("ANA TORRES", otra_empresa)
        )


# ===========================================================================
# Escenarios 23 y 24: hoja Tickets del archivo de auditoría
# ===========================================================================
class PruebasHojaTickets(unittest.TestCase):
    def test_23_hoja_tickets_no_existe(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tickets_") as carpeta:
            ruta = Path(carpeta) / "sin_tickets.xlsx"
            libro = Workbook()
            hoja = libro.active
            hoja.title = "DATOS"
            hoja.append(["OTRO_CAMPO"])
            hoja.append(["SIN SOLICITANTE"])
            libro.save(ruta)

            diagnostico = MaestroTickets.inspeccionar_hojas(ruta)
            self.assertEqual(diagnostico["hojas"], ["DATOS"])
            self.assertEqual(diagnostico["hoja_esperada"], "Tickets")
            self.assertIn("sin_tickets.xlsx", diagnostico["problema"])
            self.assertIn("Tickets", diagnostico["problema"])

            with self.assertRaises(ErrorEstructura) as ctx:
                MaestroTickets.leer_hoja_detalle(ruta)
            mensaje = str(ctx.exception)
            self.assertIn("sin_tickets.xlsx", mensaje)
            self.assertIn("Tickets", mensaje)

            # Si otra hoja contiene la columna obligatoria, la auditoría puede
            # continuar con una advertencia explícita (nunca en silencio).
            alternativa = Path(carpeta) / "alternativa.xlsx"
            libro = Workbook()
            hoja = libro.active
            hoja.title = "DATOS"
            hoja.append([C_SOLICITANTE])
            hoja.append(["ANA TORRES"])
            libro.save(alternativa)
            df, nombre, aviso = MaestroTickets.leer_hoja_detalle(alternativa)
            self.assertEqual(nombre, "DATOS")
            self.assertIn(C_SOLICITANTE, df.columns)
            self.assertIn("Tickets", aviso)

    def test_24_hoja_tickets_existe_y_se_audita(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tickets_") as carpeta:
            ruta = Path(carpeta) / "con_tickets.xlsx"
            libro = Workbook()
            hoja = libro.active
            hoja.title = "Tickets"
            hoja.append([C_SOLICITANTE, "Area"])
            hoja.append(["ANA TORRES", "SISTEMAS"])
            libro.save(ruta)

            diagnostico = MaestroTickets.inspeccionar_hojas(ruta)
            self.assertEqual(diagnostico["hoja_encontrada"], "Tickets")
            self.assertEqual(diagnostico["problema"], "")

            df, nombre_hoja, aviso = MaestroTickets.leer_hoja_detalle(ruta)
            self.assertEqual(nombre_hoja, "Tickets")
            self.assertEqual(aviso, "")
            self.assertIn(C_SOLICITANTE, df.columns)
            self.assertEqual(df.iloc[0][C_SOLICITANTE], "ANA TORRES")


if __name__ == "__main__":
    unittest.main()
