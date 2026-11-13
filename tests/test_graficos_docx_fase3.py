"""Pruebas OOXML de gráficos nativos; los DOCX de salida son temporales."""

from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile

from docx import Document
from lxml import etree
from openpyxl import load_workbook
from io import BytesIO

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.actualizador_graficos_docx import (
    ErrorGraficosDocx,
    SerieGraficoDocx,
    actualizar_graficos_docx,
)


PLANTILLA = BASE_DIR / "plantillas" / "anexo_5_plantilla.docx"
NS = {"c": "http://schemas.openxmlformats.org/drawingml/2006/chart"}


def series_sinteticas():
    return {
        1: SerieGraficoDocx(("Incidencia", "Proyecto", "Solicitud"), (Decimal("1.25"), Decimal("2.5"), Decimal("3.75"))),
        2: SerieGraficoDocx((), (), "TIEMPO(H)"),
        3: SerieGraficoDocx(("Incidencias", "Solicitudes"), (88, 57), "TICKETS"),
        4: SerieGraficoDocx(tuple(f"Área {i:02d}" for i in range(22)), tuple(range(22, 0, -1)), "TICKETS"),
    }


class TestGraficosDocxFase3(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="anexo5-graficos-")
        self.addCleanup(self.tmp.cleanup)
        self.salida = Path(self.tmp.name) / "anexo_generado.docx"
        self.hash_plantilla = sha256(PLANTILLA.read_bytes()).hexdigest()

    def test_incrusta_libros_actualiza_cache_y_conserva_estilo_nativo(self):
        with ZipFile(PLANTILLA) as antes:
            estilo_antes = {
                i: {
                    path: [etree.tostring(x) for x in etree.fromstring(antes.read(f"word/charts/chart{i}.xml")).xpath(path, namespaces=NS)]
                    for path in ("//c:legend", "//c:pieChart/c:varyColors", "//c:spPr", "//c:txPr")
                }
                for i in range(1, 5)
            }

        resultado = actualizar_graficos_docx(PLANTILLA, self.salida, series_sinteticas())

        self.assertEqual(sha256(PLANTILLA.read_bytes()).hexdigest(), self.hash_plantilla)
        self.assertEqual(dict(resultado.puntos_por_grafico), {1: 3, 2: 0, 3: 2, 4: 22})
        self.assertEqual(len(resultado.libros_incrustados), 4)
        with ZipFile(self.salida) as generado:
            self.assertIsNone(generado.testzip())
            nombres = set(generado.namelist())
            for i, serie in series_sinteticas().items():
                chart_path = f"word/charts/chart{i}.xml"
                root = etree.fromstring(generado.read(chart_path))
                rels = etree.fromstring(generado.read(f"word/charts/_rels/chart{i}.xml.rels"))
                external_id = root.xpath("//c:externalData/@r:id", namespaces={**NS, "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"})[0]
                rel = next(r for r in rels if r.get("Id") == external_id)
                self.assertTrue(rel.get("Type").endswith("/package"))
                self.assertIsNone(rel.get("TargetMode"))
                self.assertNotIn("sharepoint.com", rel.get("Target", "").casefold())
                libro_path = f"word/embeddings/Anexo5_grafico_{i}.xlsx"
                self.assertEqual(f"word/charts/{rel.get('Target')}".replace("word/charts/../", "word/"), libro_path)

                ser = root.xpath("//c:pieChart/c:ser", namespaces=NS)[0]
                self.assertEqual(ser.xpath("./c:cat/c:strRef/c:f/text()", namespaces=NS)[0], {
                    1: "RESUMEN", 2: "PROYECTOS", 3: "ACTIVIDAD", 4: "AREA"
                }[i] + ("!$A$2:$A$2" if not serie.categorias else f"!$A$2:$A${len(serie.categorias)+1}"))
                self.assertEqual(ser.xpath("./c:cat/c:strRef/c:strCache/c:pt/c:v/text()", namespaces=NS), list(serie.categorias))
                self.assertEqual(
                    [Decimal(x) for x in ser.xpath("./c:val/c:numRef/c:numCache/c:pt/c:v/text()", namespaces=NS)],
                    [Decimal(str(x)) for x in serie.valores],
                )
                self.assertEqual(
                    estilo_antes[i],
                    {
                        path: [etree.tostring(x) for x in root.xpath(path, namespaces=NS)]
                        for path in estilo_antes[i]
                    },
                )
                wb = load_workbook(BytesIO(generado.read(libro_path)), read_only=True, data_only=True)
                try:
                    sheet = {1: "RESUMEN", 2: "PROYECTOS", 3: "ACTIVIDAD", 4: "AREA"}[i]
                    self.assertEqual(list(wb[sheet].iter_rows(min_row=2, values_only=True)), list(zip(serie.categorias, [float(x) for x in serie.valores])))
                finally:
                    wb.close()
            self.assertEqual(sum(n.startswith("word/charts/chart") and n.endswith(".xml") for n in nombres), 4)
            self.assertEqual(sum(n.startswith("word/embeddings/") and n.endswith(".xlsx") for n in nombres), 4)
            self.assertFalse(any("sharepoint.com" in generado.read(n).decode("utf-8", errors="ignore").casefold() for n in nombres if n.startswith("word/charts/")))
        self.assertEqual(len(Document(self.salida).inline_shapes), 4)

    def test_exige_cuatro_series_y_categoria_valor_alineados(self):
        con_falta = series_sinteticas()
        del con_falta[4]
        with self.assertRaises(ErrorGraficosDocx):
            actualizar_graficos_docx(PLANTILLA, self.salida, con_falta)
        con_falta = series_sinteticas()
        con_falta[1] = SerieGraficoDocx(("A",), (1, 2))
        with self.assertRaises(ErrorGraficosDocx):
            actualizar_graficos_docx(PLANTILLA, self.salida, con_falta)
        self.assertFalse(self.salida.exists())

    def test_no_modifica_entrada_ni_sobrescribe_salida(self):
        with self.assertRaises(ErrorGraficosDocx):
            actualizar_graficos_docx(PLANTILLA, PLANTILLA, series_sinteticas())
        actualizar_graficos_docx(PLANTILLA, self.salida, series_sinteticas())
        antes = sha256(self.salida.read_bytes()).hexdigest()
        with self.assertRaises(FileExistsError):
            actualizar_graficos_docx(PLANTILLA, self.salida, series_sinteticas())
        self.assertEqual(sha256(self.salida.read_bytes()).hexdigest(), antes)
        self.assertEqual(sha256(PLANTILLA.read_bytes()).hexdigest(), self.hash_plantilla)


if __name__ == "__main__":
    unittest.main()
