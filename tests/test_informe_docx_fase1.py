"""Pruebas sintéticas de lectura/modelo Anexo 5 (fase 1, sin SQL)."""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from openpyxl import Workbook

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.estadisticas_informe_docx import calcular_estadisticas_informe
from core.lector_informe_docx import leer_excel_informe
from core.modelo_informe_docx import ErrorLecturaInforme, ErrorPeriodoInforme
from core.esquema import COLUMNAS_OFICIALES


def fila(**cambios):
    valores = {
        "Detalle": "Detalle sintético",
        "Fecha": date(2026, 9, 1),
        "Solicitante": "Usuario sintético",
        "Area": "Área A",
        "Atención": "Incidencia",
        "Requerimiento": "",
        "Responsable": "Responsable sintético",
        "Subtipo": "",
        "Proyecto": "",
        "Horas": 1,
        "Actividad": "Incidencia",
    }
    valores.update(cambios)
    return valores


def crear_excel(ruta: Path, filas, *, hoja="Consulta", desplazamiento=0, segunda_hoja=None, columnas=None):
    libro = Workbook()
    ws = libro.active
    ws.title = hoja
    encabezados = list(columnas or COLUMNAS_OFICIALES)
    for _ in range(desplazamiento):
        ws.append([None] * len(encabezados))
    ws.append(encabezados)
    for registro in filas:
        ws.append([registro.get(nombre) for nombre in encabezados])
    if segunda_hoja:
        otro = libro.create_sheet(segunda_hoja)
        otro.append(list(COLUMNAS_OFICIALES))
    libro.save(ruta)


class TestLectorInformeDocxFase1(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="informe-docx-fase1-")
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)

    def test_lee_filas_originales_categorias_areas_y_proyectos(self):
        registros = [
            fila(Fecha=date(2026, 9, 3), Area="Área A", Solicitante="Ana", Horas=1.25),
            fila(Fecha=date(2026, 9, 4), Actividad="Solicitud", Atención="Solicitud", Area=None, Solicitante="Bruno", Horas=2),
            fila(Fecha=date(2026, 9, 5), Actividad="Proyecto", Proyecto="Portal", Area="Área B", Horas=0.5),
            fila(Fecha=date(2026, 9, 6), Actividad="Proyecto", Proyecto="Portal ", Area="Área B ", Horas=1),
        ]
        ruta = self.dir / "corregido.xlsx"
        crear_excel(ruta, registros, desplazamiento=2)
        antes = hashlib.sha256(ruta.read_bytes()).hexdigest()

        datos = leer_excel_informe(ruta)
        stats = calcular_estadisticas_informe(datos)

        self.assertEqual(datos.hoja, "Consulta")
        self.assertEqual(datos.fila_encabezado, 3)
        self.assertEqual([r.fila_origen for r in datos.registros], [4, 5, 6, 7])
        self.assertEqual(stats.periodo, (2026, 9))
        por_categoria = {f.categoria: f for f in stats.filas_por_categoria}
        self.assertEqual(por_categoria["Incidencia"].cantidad_registros, 1)
        self.assertEqual(por_categoria["Solicitud"].cantidad_registros, 1)
        self.assertEqual(stats.actividades_proyecto, 2)
        # Se conserva como métrica cruda; la regla de unicidad de negocio queda abierta.
        self.assertEqual(stats.nombres_proyecto_distintos_exactos, ("Portal", "Portal "))
        self.assertEqual(stats.filas_sin_area, 1)
        self.assertEqual(stats.usuarios_sin_area, (("Bruno", 1),))
        self.assertEqual(len(stats.areas_todos_los_registros), 4)
        self.assertEqual(len(stats.areas_incidencias_solicitudes), 2)
        self.assertEqual(stats.horas_totales_completas, 4.75)
        self.assertEqual(hashlib.sha256(ruta.read_bytes()).hexdigest(), antes)

    def test_respeta_alias_del_esquema_y_cambios_del_archivo_seleccionado(self):
        alias = [
            "DETALLE", "FECHA", "USUARIO", "AREA_USUARIO", "TIPO_ATENCION",
            "REQUERIMIENTO", "RESPONSABLE", "SUBTIPO", "NOMBRE_PROYECTO", "HORAS", "TIPO_ACTIVIDAD",
        ]
        registro = {
            "DETALLE": "Corrección manual",
            "FECHA": date(2026, 9, 20),
            "USUARIO": "Usuario corregido",
            "AREA_USUARIO": "Área corregida",
            "TIPO_ATENCION": "Solicitud",
            "REQUERIMIENTO": "",
            "RESPONSABLE": "",
            "SUBTIPO": "",
            "NOMBRE_PROYECTO": "",
            "HORAS": "2.25",
            "TIPO_ACTIVIDAD": "Solicitudes",
        }
        ruta = self.dir / "manual.xlsx"
        crear_excel(ruta, [registro], columnas=alias)

        datos = leer_excel_informe(ruta)

        self.assertEqual(datos.columnas_oficiales, tuple(COLUMNAS_OFICIALES))
        self.assertEqual(datos.registros[0].detalle, "Corrección manual")
        self.assertEqual(datos.registros[0].area_original, "Área corregida")
        self.assertEqual(datos.registros[0].horas, 2.25)

    def test_periodos_multiples_no_se_elige_silenciosamente(self):
        ruta = self.dir / "meses.xlsx"
        crear_excel(ruta, [fila(Fecha=date(2026, 9, 1)), fila(Fecha=date(2026, 10, 1))])
        datos = leer_excel_informe(ruta)

        sin_seleccion = calcular_estadisticas_informe(datos)
        septiembre = calcular_estadisticas_informe(datos, (2026, 9))
        self.assertTrue(sin_seleccion.periodo_indeterminado)
        self.assertEqual(sin_seleccion.filas_analizadas, 2)
        self.assertEqual(septiembre.filas_analizadas, 1)
        self.assertEqual(septiembre.filas_excluidas_por_periodo, 1)
        with self.assertRaises(ErrorPeriodoInforme):
            calcular_estadisticas_informe(datos, (2026, 8))

    def test_filas_sin_fecha_no_se_asignan_al_unico_mes_detectado(self):
        ruta = self.dir / "un_mes_y_sin_fecha.xlsx"
        crear_excel(ruta, [fila(Fecha=date(2026, 9, 1)), fila(Fecha=None)])
        stats = calcular_estadisticas_informe(leer_excel_informe(ruta))
        self.assertEqual(stats.periodo, (2026, 9))
        self.assertEqual(stats.filas_analizadas, 1)
        self.assertEqual(stats.filas_excluidas_por_periodo, 1)
        self.assertEqual(stats.filas_fecha_vacia, 1)

    def test_horas_vacias_e_invalidas_no_se_convierten_en_total_completo(self):
        ruta = self.dir / "horas.xlsx"
        crear_excel(ruta, [
            fila(Horas=None), fila(Horas="no numérico"), fila(Horas=-1),
        ])
        datos = leer_excel_informe(ruta)
        stats = calcular_estadisticas_informe(datos)

        incidencia = next(f for f in stats.filas_por_categoria if f.categoria == "Incidencia")
        self.assertEqual(incidencia.filas_sin_horas, 1)
        self.assertEqual(incidencia.filas_con_horas_invalidas, 2)
        self.assertIsNone(stats.horas_totales_completas)
        self.assertIsNone(stats.porcentajes_horas_por_categoria)
        self.assertEqual(sum(r.horas is None for r in datos.registros), 3)

    def test_fecha_invalida_y_categoria_desconocida_se_exponen(self):
        ruta = self.dir / "desconocidos.xlsx"
        crear_excel(ruta, [
            fila(Fecha="fecha mala", Actividad="Mantenimiento especial"),
            fila(Fecha=46000, Actividad="Incidencia"),
        ])
        datos = leer_excel_informe(ruta)
        stats = calcular_estadisticas_informe(datos)

        self.assertIsNone(datos.registros[0].fecha)
        self.assertIsNone(datos.registros[0].categoria)
        self.assertEqual(datos.registros[0].estado_fecha, "invalido")
        self.assertEqual(datos.registros[1].estado_fecha, "invalido")
        codigos = {hallazgo.codigo for hallazgo in datos.hallazgos + stats.hallazgos}
        self.assertIn("FECHA_INVALIDA", codigos)
        self.assertIn("ACTIVIDAD_DESCONOCIDA", codigos)
        self.assertTrue(stats.periodo_indeterminado)

    def test_no_elige_entre_consulta_y_tickets_ni_acepta_esquema_incompleto(self):
        ruta = self.dir / "dos_hojas.xlsx"
        crear_excel(ruta, [fila()], segunda_hoja="Tickets")
        with self.assertRaises(ErrorLecturaInforme):
            leer_excel_informe(ruta)
        self.assertEqual(leer_excel_informe(ruta, "Tickets").hoja, "Tickets")

        incompleto = self.dir / "incompleto.xlsx"
        crear_excel(incompleto, [fila()], columnas=COLUMNAS_OFICIALES[:-1])
        with self.assertRaises(ErrorLecturaInforme):
            leer_excel_informe(incompleto)

    def test_permite_sin_registros_sin_inventar_periodo(self):
        ruta = self.dir / "vacio.xlsx"
        crear_excel(ruta, [])
        datos = leer_excel_informe(ruta)
        stats = calcular_estadisticas_informe(datos)
        self.assertEqual(stats.filas_fuente, 0)
        self.assertIsNone(stats.periodo)
        self.assertTrue(stats.periodo_indeterminado)
        self.assertEqual(stats.horas_totales_completas, 0)


if __name__ == "__main__":
    unittest.main()
