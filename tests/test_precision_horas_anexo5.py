"""Precisión decimal de horas en lectura, estadísticas y proyección Anexo 5."""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

from openpyxl import Workbook

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.actualizador_tablas_docx import localizar_tablas_docx
from core.esquema import COLUMNAS_OFICIALES
from core.estadisticas_informe_docx import calcular_estadisticas_informe
from core.generador_docx import generar_anexo5_docx
from core.lector_informe_docx import _horas, leer_excel_informe
from core.modelo_informe_docx import formatear_horas, normalizar_horas
from core.proyector_anexo5_docx import _numero, preparar_contenido_anexo5

from docx import Document


PLANTILLA = BASE_DIR / "plantillas" / "anexo_5_plantilla.docx"
PATRON_HORAS = re.compile(r"^\d+\.\d{2}$")


def fila(**cambios):
    valores = {
        "Detalle": "Detalle sintético",
        "Fecha": date(2026, 2, 10),
        "Solicitante": "Usuario sintético",
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


def crear_excel(ruta: Path, registros) -> None:
    libro = Workbook()
    hoja = libro.active
    hoja.title = "Consulta"
    hoja.append(COLUMNAS_OFICIALES)
    for registro in registros:
        hoja.append([registro.get(nombre) for nombre in COLUMNAS_OFICIALES])
    libro.save(ruta)


def registros_febrero():
    """Residuos reales de febrero de 2026 más el resto necesario para 833.00."""

    registros = [
        fila(Horas=0.999999999999999) for _ in range(18)
    ]
    registros.extend(
        fila(Horas=0.499999999999998, Atención="Solicitud", Actividad="Solicitud")
        for _ in range(3)
    )
    registros.extend(
        fila(
            Horas=0.500000000000001,
            Atención="Proyectos",
            Actividad="Proyecto",
            Proyecto="Portal",
        )
        for _ in range(2)
    )
    registros.append(fila(Horas=812.5))
    return registros


class PruebasNormalizacionDecimal(unittest.TestCase):
    def test_redondeo_a_dos_decimales_con_half_up(self) -> None:
        casos = {
            Decimal("0.999999999999999"): Decimal("1.00"),
            Decimal("0.499999999999998"): Decimal("0.50"),
            Decimal("0.500000000000001"): Decimal("0.50"),
            Decimal("1.256789"): Decimal("1.26"),
            Decimal("2.345678"): Decimal("2.35"),
            Decimal("3"): Decimal("3.00"),
        }
        for original, esperado in casos.items():
            with self.subTest(original=original):
                self.assertEqual(normalizar_horas(original), esperado)

    def test_half_up_no_usa_redondeo_bancario(self) -> None:
        self.assertEqual(normalizar_horas(Decimal("1.005")), Decimal("1.01"))
        self.assertEqual(normalizar_horas(Decimal("2.675")), Decimal("2.68"))

    def test_formato_siempre_con_dos_decimales(self) -> None:
        casos = (
            (Decimal("833"), "833.00"),
            (Decimal("266.5"), "266.50"),
            (Decimal("565.5"), "565.50"),
            (Decimal("832.999999999999978"), "833.00"),
            (3, "3.00"),
            (None, ""),
        )
        for valor, esperado in casos:
            with self.subTest(valor=valor):
                self.assertEqual(formatear_horas(valor), esperado)
                self.assertEqual(_numero(valor), esperado)

    def test_horas_vacias_e_invalidas_no_se_convierten_en_cero(self) -> None:
        for valor in (None, "", "   "):
            with self.subTest(valor=valor):
                self.assertEqual(_horas(valor), (None, "vacio"))
        for valor in ("no numérico", -1, True):
            with self.subTest(valor=valor):
                self.assertEqual(_horas(valor), (None, "invalido"))


class PruebasAcumulacionAnexo5(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="precision-horas-anexo5-")
        self.addCleanup(self.temp.cleanup)
        self.carpeta = Path(self.temp.name)

    def _leer_febrero(self):
        ruta = self.carpeta / "febrero_2026.xlsx"
        crear_excel(ruta, registros_febrero())
        datos = leer_excel_informe(ruta)
        estadisticas = calcular_estadisticas_informe(datos, periodo=(2026, 2))
        return ruta, datos, estadisticas

    def test_cada_registro_se_redondea_antes_de_acumular(self) -> None:
        ruta = self.carpeta / "residuos.xlsx"
        crear_excel(
            ruta,
            [
                fila(Horas=0.999999999999999),
                fila(Horas=0.499999999999998),
                fila(Horas=0.500000000000001),
            ],
        )
        datos = leer_excel_informe(ruta)

        self.assertEqual(
            [registro.horas for registro in datos.registros],
            [Decimal("1.00"), Decimal("0.50"), Decimal("0.50")],
        )
        self.assertEqual(
            Decimal(str(datos.registros[0].horas_original)),
            Decimal("0.999999999999999"),
        )

    def test_febrero_2026_totaliza_exactamente_833_00(self) -> None:
        _, _, estadisticas = self._leer_febrero()

        self.assertEqual(
            estadisticas.horas_totales_completas, Decimal("833.00")
        )
        self.assertEqual(
            dict(estadisticas.horas_conocidas_por_categoria),
            {
                "Incidencia": Decimal("830.50"),
                "Solicitud": Decimal("1.50"),
                "Proyecto": Decimal("1.00"),
            },
        )
        self.assertEqual(
            sum(
                (horas for _, horas in estadisticas.horas_conocidas_por_categoria),
                Decimal("0"),
            ),
            Decimal("833.00"),
        )
        self.assertEqual(
            formatear_horas(estadisticas.horas_totales_completas), "833.00"
        )

    def test_vacios_e_invalidos_siguen_siendo_parciales(self) -> None:
        ruta = self.carpeta / "parcial.xlsx"
        crear_excel(
            ruta,
            [fila(Horas=0.999999999999999), fila(Horas=None), fila(Horas="inválida")],
        )
        estadisticas = calcular_estadisticas_informe(leer_excel_informe(ruta))

        self.assertIsNone(estadisticas.horas_totales_completas)
        incidencia = next(
            f
            for f in estadisticas.filas_por_categoria
            if f.categoria == "Incidencia"
        )
        self.assertEqual(incidencia.horas_conocidas, Decimal("1.00"))
        self.assertEqual(incidencia.filas_sin_horas, 1)
        self.assertEqual(incidencia.filas_con_horas_invalidas, 1)

    def test_proyeccion_y_graficos_igualan_los_registros_normalizados(self) -> None:
        _, datos, estadisticas = self._leer_febrero()
        contenido = preparar_contenido_anexo5(datos, estadisticas)

        filas = contenido.filas_por_tabla["resumen_tipo_atencion"]
        self.assertEqual(
            [(categoria, horas) for categoria, horas, _ in filas],
            [("Incidencia", "830.50"), ("Solicitud", "1.50"), ("Proyecto", "1.00")],
        )
        for _, horas, _ in filas:
            self.assertRegex(horas, PATRON_HORAS)
        self.assertEqual(
            contenido.totales_por_tabla["resumen_tipo_atencion"][1], "833.00"
        )
        self.assertEqual(contenido.reemplazos_texto["479.5 horas"], "833.00 horas")
        self.assertEqual(
            contenido.filas_por_tabla["proyectos"][0][1], "1.00"
        )
        self.assertEqual(
            contenido.series_por_grafico[1].valores,
            (Decimal("830.50"), Decimal("1.50"), Decimal("1.00")),
        )
        self.assertEqual(
            contenido.series_por_grafico[2].valores, (Decimal("1.00"),)
        )
        for fila_detalle in contenido.filas_por_tabla["detalle"]:
            self.assertRegex(fila_detalle[-1], PATRON_HORAS)

    def test_word_usa_las_mismas_horas_y_graficos_que_la_proyeccion(self) -> None:
        _, datos, estadisticas = self._leer_febrero()
        contenido = preparar_contenido_anexo5(datos, estadisticas)
        salida = self.carpeta / "Anexo5_febrero.docx"

        generar_anexo5_docx(
            PLANTILLA,
            salida,
            reemplazos_texto=contenido.reemplazos_texto,
            filas_por_tabla=contenido.filas_por_tabla,
            totales_por_tabla=contenido.totales_por_tabla,
            series_por_grafico=contenido.series_por_grafico,
        )

        documento = Document(salida)
        tablas = localizar_tablas_docx(documento)
        resumen = {
            fila.cells[0].text: fila.cells[1].text
            for fila in tablas["resumen_tipo_atencion"].rows[1:]
        }
        self.assertEqual(
            resumen,
            {
                "Incidencia": "830.50",
                "Solicitud": "1.50",
                "Proyecto": "1.00",
                "TOTAL": "833.00",
            },
        )
        texto = "\n".join(parrafo.text for parrafo in documento.paragraphs)
        self.assertIn("833.00 horas", texto)
        self.assertNotIn("832.999", texto)

        with ZipFile(salida) as paquete:
            grafico_1 = paquete.read("word/charts/chart1.xml").decode("utf-8")
            grafico_2 = paquete.read("word/charts/chart2.xml").decode("utf-8")
        for valor in ("830.50", "1.50", "1.00"):
            self.assertIn(f">{valor}<", grafico_1)
        self.assertIn(">1.00<", grafico_2)


if __name__ == "__main__":
    unittest.main()
