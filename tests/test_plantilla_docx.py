"""Pruebas de la plantilla Word del Anexo 5 (FASE 1).

Verifican que la plantilla oficial y su copia estandarizada son idénticas,
que el paquete DOCX es válido y que la estructura esperada (párrafos,
tablas, textos resaltados, gráficos y relaciones) no cambió.

Estas pruebas son de solo lectura sobre la plantilla. La prueba de copia
trabaja en una carpeta temporal y nunca escribe en `plantillas/`.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from lxml import etree

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import docx  # noqa: E402

PLANTILLAS_DIR = BASE_DIR / "plantillas"
PLANTILLA_ORIGINAL = (
    PLANTILLAS_DIR
    / "Anexo_5_Servicio_de_Tecnologías_de_la_Información_MES_2026_PLANTILLA.docx"
)
PLANTILLA_COPIA = PLANTILLAS_DIR / "anexo_5_plantilla.docx"

HASH_ESPERADO = "4e378346a062e9e864297f93af7dd82bd60a360e7b49c5656d4c095e3644f242"
TAMANO_ESPERADO = 154442

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}


def sha256(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def abrir_documento(ruta: Path = PLANTILLA_COPIA):
    return docx.Document(str(ruta))


def tabla_por_encabezado(documento, encabezados):
    """Devuelve la primera tabla cuyos encabezados coinciden exactamente."""
    for tabla in documento.tables:
        if not tabla.rows:
            continue
        reales = [celda.text.strip() for celda in tabla.rows[0].cells]
        if reales == encabezados:
            return tabla
    raise AssertionError(f"No se encontró tabla con encabezados {encabezados!r}")


def resaltados(parrafo):
    return [
        run.text
        for run in parrafo.runs
        if run.font.highlight_color is not None and run.text.strip()
    ]


def leer_xml_paquete(zf: zipfile.ZipFile, nombre: str):
    return etree.fromstring(zf.read(nombre))


class TestArchivosPlantilla(unittest.TestCase):
    """Existencia, integridad y conservación del original."""

    def test_original_existe(self):
        self.assertTrue(
            PLANTILLA_ORIGINAL.is_file(),
            f"No existe la plantilla original: {PLANTILLA_ORIGINAL}",
        )

    def test_copia_estandarizada_existe(self):
        self.assertTrue(
            PLANTILLA_COPIA.is_file(),
            f"No existe la copia estandarizada: {PLANTILLA_COPIA}",
        )

    def test_tamano_esperado(self):
        self.assertEqual(PLANTILLA_ORIGINAL.stat().st_size, TAMANO_ESPERADO)
        self.assertEqual(PLANTILLA_COPIA.stat().st_size, TAMANO_ESPERADO)

    def test_hash_del_original_no_cambio(self):
        self.assertEqual(sha256(PLANTILLA_ORIGINAL), HASH_ESPERADO)

    def test_hash_de_la_copia_es_identico_al_original(self):
        self.assertEqual(sha256(PLANTILLA_COPIA), sha256(PLANTILLA_ORIGINAL))
        self.assertEqual(sha256(PLANTILLA_COPIA), HASH_ESPERADO)

    def test_paquete_zip_valido(self):
        for ruta in (PLANTILLA_ORIGINAL, PLANTILLA_COPIA):
            with zipfile.ZipFile(ruta) as zf:
                self.assertIsNone(zf.testzip(), f"Paquete corrupto: {ruta}")

    def test_python_docx_abre_ambos_archivos(self):
        for ruta in (PLANTILLA_ORIGINAL, PLANTILLA_COPIA):
            documento = abrir_documento(ruta)
            self.assertIsNotNone(documento.element.body)

    def test_leer_no_modifica_el_hash_del_original(self):
        antes = sha256(PLANTILLA_ORIGINAL)
        abrir_documento(PLANTILLA_ORIGINAL)
        with zipfile.ZipFile(PLANTILLA_ORIGINAL) as zf:
            zf.namelist()
        self.assertEqual(sha256(PLANTILLA_ORIGINAL), antes)
        self.assertEqual(antes, HASH_ESPERADO)


class TestEstructuraDocumento(unittest.TestCase):
    """Conteos y propiedades generales del documento."""

    @classmethod
    def setUpClass(cls):
        cls.documento = abrir_documento()

    def test_cantidad_de_parrafos(self):
        self.assertEqual(len(self.documento.paragraphs), 36)

    def test_cantidad_de_tablas(self):
        self.assertEqual(len(self.documento.tables), 5)

    def test_una_sola_seccion_vertical(self):
        self.assertEqual(len(self.documento.sections), 1)
        from docx.enum.section import WD_ORIENT

        self.assertEqual(self.documento.sections[0].orientation, WD_ORIENT.PORTRAIT)

    def test_cuatro_formas_inline_de_tipo_grafico(self):
        from docx.enum.shape import WD_INLINE_SHAPE

        self.assertEqual(len(self.documento.inline_shapes), 4)
        for forma in self.documento.inline_shapes:
            self.assertEqual(forma.type, WD_INLINE_SHAPE.CHART)

    def test_estilos_de_titulos_de_seccion(self):
        titulos = ["OBJETIVO", "RESUMEN", "PROYECTOS", "MESA DE AYUDA TI",
                   "DETALLE DE ACTIVIDADES"]
        por_texto = {
            p.text.strip(): p.style.name
            for p in self.documento.paragraphs
            if p.text.strip()
        }
        for titulo in titulos:
            self.assertIn(titulo, por_texto)
            self.assertEqual(por_texto[titulo], "List Paragraph")

    def test_textos_de_encabezados_de_seccion(self):
        textos = [p.text.strip() for p in self.documento.paragraphs if p.text.strip()]
        for esperado in (
            "ANEXO 5:  SERVICIOS DE TECNOLOGIA DE LA INFORMACIÓN",
            "INKA CROPS PERIODO: agosto 2026",
            "Atenciones por Proyectos",
            "Tickets por Tipo de Actividad",
            "Tickets por Área",
        ):
            self.assertIn(esperado, textos)


class TestTextosResaltados(unittest.TestCase):
    """Marcadores variables que la generación deberá reemplazar."""

    @classmethod
    def setUpClass(cls):
        cls.parrafos = abrir_documento().paragraphs

    def test_resaltados_del_titulo(self):
        self.assertEqual(resaltados(self.parrafos[1]), ["agosto 2026"])

    def test_resaltados_del_objetivo(self):
        self.assertEqual(resaltados(self.parrafos[4]), ["agosto de 2026"])

    def test_resaltados_del_resumen(self):
        self.assertEqual(
            resaltados(self.parrafos[7]), ["agosto de 2026", "479.5 horas"]
        )

    def test_resaltados_de_proyectos(self):
        self.assertEqual(
            resaltados(self.parrafos[13]), ["agosto de 2026", "9 proyectos"]
        )

    def test_resaltados_de_mesa_de_ayuda(self):
        self.assertEqual(
            resaltados(self.parrafos[20]), ["de agosto de 2026", "129 tickets"]
        )

    def test_todos_los_resaltados_son_amarillos(self):
        from docx.enum.text import WD_COLOR_INDEX

        for parrafo in self.parrafos:
            for run in parrafo.runs:
                if run.font.highlight_color is not None:
                    self.assertEqual(
                        run.font.highlight_color, WD_COLOR_INDEX.YELLOW
                    )


class TestTablas(unittest.TestCase):
    """Identificación de tablas por encabezado y dimensiones."""

    @classmethod
    def setUpClass(cls):
        cls.documento = abrir_documento()

    def test_tabla_horas_por_tipo(self):
        tabla = tabla_por_encabezado(
            self.documento, ["TIPO DE ATENCION", "TIEMPO(H)", "%"]
        )
        self.assertEqual((len(tabla.rows), len(tabla.columns)), (5, 3))
        self.assertEqual(tabla.rows[-1].cells[0].text.strip(), "TOTAL")
        self.assertEqual(tabla.rows[-1].cells[1].text.strip(), "479.5")

    def test_tabla_proyectos(self):
        tabla = tabla_por_encabezado(
            self.documento, ["PROYECTOS", "TIEMPO(H)", "%"]
        )
        self.assertEqual((len(tabla.rows), len(tabla.columns)), (11, 3))
        self.assertEqual(tabla.rows[-1].cells[0].text.strip(), "TOTAL GENERAL")
        self.assertEqual(tabla.rows[-1].cells[1].text.strip(), "237.5")

    def test_tabla_tickets_por_actividad(self):
        tabla = tabla_por_encabezado(
            self.documento, ["TIPO DE ACTIVIDAD", "TICKETS", "%"]
        )
        self.assertEqual((len(tabla.rows), len(tabla.columns)), (4, 3))
        self.assertEqual(tabla.rows[-1].cells[0].text.strip(), "TOTAL GENERAL")
        self.assertEqual(tabla.rows[-1].cells[1].text.strip(), "129")

    def test_tabla_tickets_por_area(self):
        tabla = tabla_por_encabezado(
            self.documento, ["ÁREAS", "TICKETS", "%"]
        )
        self.assertEqual((len(tabla.rows), len(tabla.columns)), (22, 3))
        self.assertEqual(tabla.rows[-1].cells[0].text.strip(), "TOTAL GENERAL")
        self.assertEqual(tabla.rows[-1].cells[1].text.strip(), "189")

    def test_tabla_detalle(self):
        tabla = tabla_por_encabezado(
            self.documento,
            [
                "TIPO DE ATENCION",
                "DETALLE",
                "FECHA",
                "USUARIO",
                "AREA_USUARIO",
                "HORAS",
            ],
        )
        self.assertEqual((len(tabla.rows), len(tabla.columns)), (190, 6))
        self.assertEqual(tabla.style.name, "List Table 3 Accent 5")

    def test_las_dos_tablas_con_tipo_de_atencion_se_distinguen(self):
        candidatas = [
            t
            for t in self.documento.tables
            if t.rows and t.rows[0].cells[0].text.strip() == "TIPO DE ATENCION"
        ]
        self.assertEqual(len(candidatas), 2)
        self.assertEqual({len(t.columns) for t in candidatas}, {3, 6})

    def test_ninguna_tabla_tiene_celdas_combinadas(self):
        for numero, tabla in enumerate(self.documento.tables, 1):
            for fila in tabla.rows:
                for celda in fila.cells:
                    tc_pr = celda._tc.tcPr
                    if tc_pr is None:
                        continue
                    self.assertIsNone(
                        tc_pr.gridSpan, f"gridSpan en tabla {numero}"
                    )
                    self.assertIsNone(
                        tc_pr.vMerge, f"vMerge en tabla {numero}"
                    )


class TestGraficos(unittest.TestCase):
    """Gráficos nativos, series y relación con el libro externo."""

    @classmethod
    def setUpClass(cls):
        cls.zf = zipfile.ZipFile(PLANTILLA_COPIA)
        cls.documento = abrir_documento()

    @classmethod
    def tearDownClass(cls):
        cls.zf.close()

    def chart(self, numero):
        return leer_xml_paquete(self.zf, f"word/charts/chart{numero}.xml")

    def chart_rels(self, numero):
        return leer_xml_paquete(
            self.zf, f"word/charts/_rels/chart{numero}.xml.rels"
        )

    def series(self, numero):
        return self.chart(numero).xpath(".//c:ser", namespaces=NS)

    def test_existen_cuatro_partes_de_grafico(self):
        partes = [
            nombre
            for nombre in self.zf.namelist()
            if nombre.startswith("word/charts/chart") and nombre.endswith(".xml")
        ]
        self.assertEqual(len(partes), 4)

    def test_los_cuatro_son_graficos_de_pastel(self):
        for numero in range(1, 5):
            pasteles = self.chart(numero).xpath(".//c:pieChart", namespaces=NS)
            self.assertEqual(
                len(pasteles), 1, f"chart{numero} no es un único pastel"
            )

    def test_cada_grafico_tiene_una_serie(self):
        for numero in range(1, 5):
            self.assertEqual(len(self.series(numero)), 1)

    def test_grafico_horas_por_tipo(self):
        serie = self.series(1)[0]
        self.assertEqual(
            "".join(serie.xpath(".//c:tx//c:v/text()", namespaces=NS)),
            "TIEMPO(H)",
        )
        self.assertEqual(
            serie.xpath(".//c:cat//c:pt/c:v/text()", namespaces=NS),
            ["Incidencia", "Proyecto", "Solicitud"],
        )
        self.assertEqual(
            serie.xpath(".//c:val//c:pt/c:v/text()", namespaces=NS),
            ["136.5", "237.5", "105.5"],
        )

    def test_grafico_horas_por_proyecto(self):
        serie = self.series(2)[0]
        self.assertEqual(
            "".join(serie.xpath(".//c:tx//c:v/text()", namespaces=NS)),
            "TIEMPO(H)",
        )
        self.assertEqual(
            len(serie.xpath(".//c:cat//c:pt/c:v", namespaces=NS)), 9
        )

    def test_grafico_tickets_por_actividad(self):
        serie = self.series(3)[0]
        self.assertEqual(
            "".join(serie.xpath(".//c:tx//c:v/text()", namespaces=NS)),
            "TICKETS",
        )
        self.assertEqual(
            serie.xpath(".//c:cat//c:pt/c:v/text()", namespaces=NS),
            ["Incidencias", "Solicitud"],
        )
        self.assertEqual(
            serie.xpath(".//c:val//c:pt/c:v/text()", namespaces=NS),
            ["87", "42"],
        )

    def test_grafico_tickets_por_area(self):
        serie = self.series(4)[0]
        self.assertEqual(
            "".join(serie.xpath(".//c:tx//c:v/text()", namespaces=NS)),
            "TICKETS",
        )
        self.assertEqual(
            len(serie.xpath(".//c:cat//c:pt/c:v", namespaces=NS)), 20
        )

    def test_coherencia_entre_tablas_y_graficos(self):
        tabla_horas = tabla_por_encabezado(
            self.documento, ["TIPO DE ATENCION", "TIEMPO(H)", "%"]
        )
        valores_grafico = [
            float(v)
            for v in self.series(1)[0].xpath(
                ".//c:val//c:pt/c:v/text()", namespaces=NS
            )
        ]
        valores_tabla = [
            float(tabla_horas.rows[i].cells[1].text)
            for i in range(1, len(tabla_horas.rows) - 1)
        ]
        self.assertEqual(sum(valores_grafico), sum(valores_tabla))
        self.assertEqual(
            round(sum(valores_grafico), 1),
            float(tabla_horas.rows[-1].cells[1].text),
        )

    def test_los_graficos_referencian_un_libro_externo(self):
        # Riesgo documentado en FASE 1: no hay libros incrustados.
        for numero in range(1, 5):
            chart = self.chart(numero)
            self.assertEqual(
                chart.xpath(".//c:externalData/@r:id", namespaces=NS), ["rId1"]
            )
            relaciones = self.chart_rels(numero)
            self.assertEqual(len(relaciones), 1)
            rel = relaciones[0]
            self.assertEqual(rel.get("Id"), "rId1")
            self.assertEqual(rel.get("TargetMode"), "External")
            self.assertTrue(rel.get("Target", "").lower().endswith(".xlsx"))
            self.assertIn("sharepoint.com", rel.get("Target", ""))

    def test_no_hay_libros_incrustados(self):
        incrustados = [
            nombre
            for nombre in self.zf.namelist()
            if nombre.startswith("word/embeddings/")
        ]
        self.assertEqual(incrustados, [])

    def test_hay_una_imagen_de_encabezado(self):
        imagenes = [
            nombre
            for nombre in self.zf.namelist()
            if nombre.startswith("word/media/")
        ]
        self.assertEqual(imagenes, ["word/media/image1.jpg"])

    def test_relaciones_de_los_graficos_en_el_documento(self):
        relaciones = leer_xml_paquete(self.zf, "word/_rels/document.xml.rels")
        destinos = {
            rel.get("Id"): rel.get("Target")
            for rel in relaciones
            if rel.get("Type", "").endswith("/chart")
        }
        self.assertEqual(
            destinos,
            {
                "rId8": "charts/chart1.xml",
                "rId9": "charts/chart2.xml",
                "rId10": "charts/chart3.xml",
                "rId11": "charts/chart4.xml",
            },
        )


class TestCopiaSegura(unittest.TestCase):
    """La generación parte de una copia limpia y no toca el original."""

    def test_copiar_la_plantilla_conserva_hash_y_estructura(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "copia_de_trabajo.docx"
            shutil.copy2(PLANTILLA_ORIGINAL, destino)

            self.assertEqual(sha256(destino), HASH_ESPERADO)
            copia = abrir_documento(destino)
            self.assertEqual(len(copia.paragraphs), 36)
            self.assertEqual(len(copia.tables), 5)
            self.assertEqual(len(copia.inline_shapes), 4)

        # La copia temporal se elimina y el original sigue intacto.
        self.assertEqual(sha256(PLANTILLA_ORIGINAL), HASH_ESPERADO)


if __name__ == "__main__":
    unittest.main()
