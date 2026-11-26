"""Proyección de estadísticas y generación integrada con plantilla Anexo 5."""

from __future__ import annotations

from datetime import date
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from zipfile import ZipFile

from docx import Document
from openpyxl import Workbook

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.actualizador_tablas_docx import localizar_tablas_docx
from core.estadisticas_informe_docx import calcular_estadisticas_informe
from core.generador_docx import generar_anexo5_docx
from core.lector_informe_docx import leer_excel_informe
from core.proyector_anexo5_docx import ErrorProyeccionAnexo5, preparar_contenido_anexo5
from core.esquema import COLUMNAS_OFICIALES


PLANTILLA = BASE_DIR / "plantillas" / "Anexo5_año_mes.docx"
EXCEL_REAL = BASE_DIR / "output" / "consulta_septiembre_2026_procesado.xlsx"
EXCEL_AGOSTO = BASE_DIR / "output" / "consulta_agosto_2026_procesado.xlsx"


def fila(**cambios):
    valores = {
        "Detalle": "Detalle sintético",
        "Fecha": date(2026, 9, 1),
        "Solicitante": "Usuario",
        "Area": "Área A",
        "Atención": "Incidencia",
        "Requerimiento": "",
        "Responsable": "Responsable",
        "Subtipo": "",
        "Proyecto": "",
        "Horas": 1,
        "Actividad": "Incidencia",
    }
    valores.update(cambios)
    return valores


def crear_excel(ruta: Path, registros):
    libro = Workbook()
    hoja = libro.active
    hoja.title = "Consulta"
    hoja.append(COLUMNAS_OFICIALES)
    for registro in registros:
        hoja.append([registro.get(nombre) for nombre in COLUMNAS_OFICIALES])
    libro.save(ruta)


class TestProyectorAnexo5Docx(unittest.TestCase):
    def test_generacion_integrada_agosto_coincidente_con_plantilla(self):
        if not EXCEL_AGOSTO.is_file():
            self.skipTest("No está disponible el Excel procesado de agosto.")
        datos = leer_excel_informe(EXCEL_AGOSTO)
        stats = calcular_estadisticas_informe(datos)
        contenido = preparar_contenido_anexo5(datos, stats)
        self.assertEqual(stats.periodo, (2026, 8))

        with tempfile.TemporaryDirectory(prefix="anexo5-integracion-agosto-") as carpeta:
            plantilla = Path(carpeta) / "plantilla.docx"
            shutil.copy2(PLANTILLA, plantilla)
            hash_plantilla = hashlib.sha256(plantilla.read_bytes()).hexdigest()
            salida = Path(carpeta) / "Anexo5_agosto.docx"
            resultado = generar_anexo5_docx(
                plantilla,
                salida,
                reemplazos_texto=contenido.reemplazos_texto,
                filas_por_tabla=contenido.filas_por_tabla,
                totales_por_tabla=contenido.totales_por_tabla,
                series_por_grafico=contenido.series_por_grafico,
            )
            doc = Document(salida)
            tablas = localizar_tablas_docx(doc)
            self.assertEqual(len(doc.inline_shapes), 3)
            self.assertEqual(
                set(tablas),
                {
                    "resumen_tipo_atencion",
                    "responsables",
                    "proyectos",
                    "tickets_tipo",
                    "areas",
                    "detalle",
                },
            )
            for nombre, filas in contenido.filas_por_tabla.items():
                self.assertEqual(len(tablas[nombre].rows), len(filas) + 1 + (nombre != "detalle"))
                esperadas = list(filas)
                if nombre != "detalle":
                    esperadas.append(contenido.totales_por_tabla[nombre])
                actuales = [tuple(celda.text for celda in fila.cells) for fila in tablas[nombre].rows[1:]]
                self.assertEqual(actuales, [tuple(str(valor) for valor in fila) for fila in esperadas])
            self.assertEqual(len(resultado.puntos_por_grafico), 3)
            for numero, puntos in resultado.puntos_por_grafico:
                self.assertEqual(
                    puntos, len(contenido.series_por_grafico[numero].categorias)
                )

            texto = "\n".join(p.text for p in doc.paragraphs)
            self.assertIn("AGOSTO 2026", texto)
            self.assertIn("agosto de 2026", texto)
            self.assertNotIn("septiembre", texto.casefold())
            with ZipFile(salida) as paquete:
                self.assertIsNone(paquete.testzip())
                xml_textos = [
                    paquete.read(nombre).decode("utf-8", errors="ignore").casefold()
                    for nombre in paquete.namelist()
                    if nombre.endswith((".xml", ".rels"))
                ]
                self.assertEqual(sum(n.startswith("word/charts/chart") and n.endswith(".xml") for n in paquete.namelist()), 3)
                self.assertFalse(any("septiembre 2026" in xml for xml in xml_textos))
            self.assertEqual(hashlib.sha256(plantilla.read_bytes()).hexdigest(), hash_plantilla)

    def test_real_septiembre_incluye_las_21_areas_de_ticket_y_las_179_filas(self):
        if not EXCEL_REAL.is_file():
            self.skipTest("No está disponible el Excel procesado de septiembre.")
        datos = leer_excel_informe(EXCEL_REAL)
        stats = calcular_estadisticas_informe(datos)
        contenido = preparar_contenido_anexo5(datos, stats)

        self.assertEqual(stats.filas_analizadas, 179)
        self.assertEqual(len(contenido.filas_por_tabla["areas"]), 21)
        self.assertEqual(len(contenido.filas_por_tabla["detalle"]), 179)
        self.assertEqual(len(contenido.filas_por_tabla["proyectos"]), 7)
        self.assertEqual(len(contenido.series_por_grafico[4].categorias), 21)
        self.assertEqual(sum(int(fila[1]) for fila in contenido.filas_por_tabla["areas"]), 145)
        self.assertEqual(contenido.reemplazos_texto["9 proyectos"], "7 proyectos")
        self.assertEqual(contenido.reemplazos_texto["129 tickets"], "145 tickets")
        self.assertEqual(contenido.reemplazos_texto["479.5 horas"], "426.75 horas")
        self.assertEqual(contenido.advertencias, ())

    def test_aplica_filas_explicitas_horas_parciales_y_exclusiones(self):
        with tempfile.TemporaryDirectory(prefix="anexo5-proyeccion-") as carpeta:
            source = Path(carpeta) / "sintetico.xlsx"
            crear_excel(source, [
                fila(Horas=None, Area=None),
                fila(Atención="Solicitud", Actividad="Solicitud", Horas="inválida", Area="Área A"),
                fila(Actividad="Mantenimiento especial", Horas=1, Area="Área B"),
                fila(Actividad="Proyecto", Proyecto="", Horas=2),
                fila(Fecha=None, Actividad="Proyecto", Proyecto="Proyecto sin fecha", Horas=4),
            ])
            datos = leer_excel_informe(source)
            stats = calcular_estadisticas_informe(datos)
            contenido = preparar_contenido_anexo5(datos, stats)

        self.assertEqual(len(contenido.filas_por_tabla["detalle"]), 4)
        self.assertEqual(contenido.filas_por_tabla["detalle"][0][-2:], ("(SIN ÁREA)", ""))
        self.assertIn(("Sin clasificar: Mantenimiento especial", "1", "33.3%"), contenido.filas_por_tabla["tickets_tipo"])
        self.assertIn(("(SIN ÁREA)", "1", "33.3%"), contenido.filas_por_tabla["areas"])
        self.assertIn(("AREA B", "1", "33.3%"), contenido.filas_por_tabla["areas"])
        self.assertEqual(contenido.totales_por_tabla["tickets_tipo"][1], "3")
        self.assertEqual(contenido.totales_por_tabla["areas"][1], "3")
        self.assertEqual(contenido.reemplazos_texto["129 tickets"], "3 tickets")
        self.assertEqual(contenido.filas_por_tabla["proyectos"], [])
        self.assertEqual(contenido.series_por_grafico[2].categorias, ())
        self.assertIn("suma parcial", contenido.reemplazos_texto["479.5 horas"])
        self.assertTrue(any("sin fecha válida" in alerta for alerta in contenido.advertencias))
        self.assertTrue(any("sin nombre" in alerta for alerta in contenido.advertencias))
        self.assertTrue(any("horas vacías o inválidas" in alerta for alerta in contenido.advertencias))

    def test_etiquetas_de_categoria_en_tablas_y_graficos_son_plurales(self):
        with tempfile.TemporaryDirectory(prefix="anexo5-etiquetas-") as carpeta:
            source = Path(carpeta) / "categorias.xlsx"
            crear_excel(source, [
                fila(Actividad="Incidencia", Horas=2),
                fila(Actividad="Solicitud", Atención="Solicitud", Horas=3),
                fila(Actividad="Proyecto", Proyecto="Portal", Atención="Proyectos", Horas=4),
            ])
            datos = leer_excel_informe(source)
            stats = calcular_estadisticas_informe(datos)
            contenido = preparar_contenido_anexo5(datos, stats)

        self.assertEqual(
            [fila[0] for fila in contenido.filas_por_tabla["resumen_tipo_atencion"]],
            ["Incidencias", "Solicitudes", "Proyectos"],
        )
        self.assertEqual(
            [fila[1] for fila in contenido.filas_por_tabla["resumen_tipo_atencion"]],
            ["2.00", "3.00", "4.00"],
        )
        self.assertEqual(
            [fila[0] for fila in contenido.filas_por_tabla["tickets_tipo"]],
            ["Incidencias", "Solicitudes"],
        )
        self.assertEqual(
            contenido.series_por_grafico[1].categorias,
            ("Incidencias", "Solicitudes", "Proyectos"),
        )
        self.assertEqual(
            contenido.series_por_grafico[3].categorias,
            ("Incidencias", "Solicitudes"),
        )
        self.assertEqual(
            [fila[0] for fila in contenido.filas_por_tabla["detalle"]],
            ["Incidencias", "Solicitudes", "Proyectos"],
        )
        with tempfile.TemporaryDirectory(prefix="anexo5-graficos-") as carpeta:
            salida = Path(carpeta) / "Anexo5_etiquetas.docx"
            generar_anexo5_docx(
                PLANTILLA,
                salida,
                reemplazos_texto=contenido.reemplazos_texto,
                filas_por_tabla=contenido.filas_por_tabla,
                totales_por_tabla=contenido.totales_por_tabla,
                series_por_grafico=contenido.series_por_grafico,
            )
            with ZipFile(salida) as paquete:
                grafico_horas = paquete.read("word/charts/chart1.xml").decode("utf-8")
                grafico_actividad = paquete.read("word/charts/chart3.xml").decode("utf-8")
                self.assertIn("Incidencias", grafico_horas)
                self.assertIn("Solicitudes", grafico_horas)
                self.assertIn("Proyectos", grafico_horas)
                self.assertIn("Incidencias", grafico_actividad)
                self.assertIn("Solicitudes", grafico_actividad)

    def test_no_proyecta_varios_meses_sin_seleccion(self):
        with tempfile.TemporaryDirectory(prefix="anexo5-meses-") as carpeta:
            source = Path(carpeta) / "meses.xlsx"
            crear_excel(source, [fila(), fila(Fecha=date(2026, 10, 1))])
            datos = leer_excel_informe(source)
            stats = calcular_estadisticas_informe(datos)
            with self.assertRaises(ErrorProyeccionAnexo5):
                preparar_contenido_anexo5(datos, stats)

    def test_generacion_integrada_con_excel_real_en_temporal(self):
        if not EXCEL_REAL.is_file():
            self.skipTest("No está disponible el Excel procesado de septiembre.")
        datos = leer_excel_informe(EXCEL_REAL)
        stats = calcular_estadisticas_informe(datos)
        contenido = preparar_contenido_anexo5(datos, stats)
        with tempfile.TemporaryDirectory(prefix="anexo5-integracion-") as carpeta:
            salida = Path(carpeta) / "Anexo5_septiembre.docx"
            generar_anexo5_docx(
                PLANTILLA,
                salida,
                reemplazos_texto=contenido.reemplazos_texto,
                filas_por_tabla=contenido.filas_por_tabla,
                totales_por_tabla=contenido.totales_por_tabla,
                series_por_grafico=contenido.series_por_grafico,
            )
            doc = Document(salida)
            tablas = localizar_tablas_docx(doc)
            self.assertEqual(len(doc.inline_shapes), 3)
            self.assertEqual(len(doc.tables), 6)
            for titulo in ("Atenciones por Proyectos", "Tickets por Tipo de Actividad", "Tickets por Área"):
                parrafo = next(p for p in doc.paragraphs if p.text.strip() == titulo)
                self.assertTrue(parrafo.runs)
                self.assertTrue(all(run.font.underline is False for run in parrafo.runs))
                self.assertTrue(all(run.bold is True for run in parrafo.runs))
            self.assertEqual(len(tablas["areas"].rows), 23)
            self.assertEqual(len(tablas["detalle"].rows), 180)
            resumen_horas = {fila.cells[0].text: fila.cells[1].text for fila in tablas["resumen_tipo_atencion"].rows[1:]}
            self.assertEqual(resumen_horas["Incidencias"], "148.50")
            self.assertEqual(resumen_horas["Proyectos"], "83.50")
            self.assertEqual(resumen_horas["Solicitudes"], "194.75")
            self.assertEqual(resumen_horas["TOTAL"], "426.75")
            tickets = {fila.cells[0].text: fila.cells[1].text for fila in tablas["tickets_tipo"].rows[1:]}
            self.assertEqual(tickets["Incidencias"], "88")
            self.assertEqual(tickets["Solicitudes"], "57")
            self.assertEqual(tickets["TOTAL GENERAL"], "145")
            self.assertEqual(tablas["areas"].rows[-1].cells[1].text, "145")
            responsables = {
                fila.cells[0].text: fila.cells[1].text
                for fila in tablas["responsables"].rows[1:]
            }
            self.assertEqual(responsables["MARCO CCANTO"], "136.50")
            self.assertEqual(responsables["GIALINA MACHADO"], "13.75")
            self.assertEqual(responsables["TOTAL"], "426.75")
            tamanos_detalle = {
                run.font.size.pt
                for fila in tablas["detalle"].rows
                for celda in fila.cells
                for parrafo in celda.paragraphs
                for run in parrafo.runs
            }
            self.assertEqual(tamanos_detalle, {6.0})
            self.assertFalse(
                any(
                    run.font.bold
                    for fila in tablas["detalle"].rows
                    for celda in fila.cells
                    for parrafo in celda.paragraphs
                    for run in parrafo.runs
                )
            )
            texto = "\n".join(parrafo.text for parrafo in doc.paragraphs)
            self.assertNotIn("agosto 2026", texto)
            self.assertNotIn("agosto de 2026", texto)
            self.assertNotIn("479.5 horas", texto)
            self.assertNotIn("9 proyectos", texto)
            self.assertNotIn("129 tickets", texto)
            self.assertNotIn("ENERO 2025", texto)
            self.assertNotIn("enero de 2025", texto)
            self.assertNotIn("701.00 horas", texto)
            self.assertNotIn("431 tickets", texto)
            self.assertNotIn("6 proyectos", texto)
            with ZipFile(salida) as paquete:
                textos_xml = [
                    paquete.read(nombre).decode("utf-8", errors="ignore").casefold()
                    for nombre in paquete.namelist()
                    if nombre.endswith((".xml", ".rels"))
                ]
            self.assertFalse(any("agosto" in texto_xml for texto_xml in textos_xml))


if __name__ == "__main__":
    unittest.main()
