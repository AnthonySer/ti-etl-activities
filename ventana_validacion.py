"""Ventana independiente de Validación de Usuarios y Áreas.

Se abre después del procesamiento cuando existen áreas vacías, usuarios no
encontrados, coincidencias ambiguas, empleados sin departamento o conflictos
con el maestro local. No reemplaza ninguna pestaña: es una ventana ``Toplevel``.

Toda acción se guarda de inmediato en la revisión persistente, de modo que
cerrar la ventana o la aplicación no hace perder el trabajo.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable, Iterable

from config import configuracion as cfg
from config.sqlserver import EMPRESAS, cargar_configuracion
from core import acciones_revision as acciones
from core import asignacion_asistida as asistida
from core import busqueda_solicitantes as busqueda_core
from core import catalogo_areas
from core import coincidencias_vista
from core import eventos
from core import procedencia
from core import revision as svc_revision
from core import seguimiento_consultas as seguimiento
from core import seleccion_empleados as sel
from core.maestro import Maestro
from database.connection import controlador_disponible
from database.resolucion import (
    CASO_A,
    CASO_B,
    ESTADO_AMBIGUO,
    ESTADO_CONFLICTO,
    ESTADO_INACTIVO,
    ESTADO_MANUAL_PROTEGIDO,
    ESTADO_SIN_COINCIDENCIAS,
    ESTADO_SIN_DEPARTAMENTO,
    ESTADO_VALIDADO,
    ResolverAreas,
)
from database.normalizador import normalizar_nombre
from database.synchronizer import Sincronizador, cargar_empleados, resumen_sincronizacion
import ui_tema
from ui_tema import (
    AZUL_MARINO,
    AZUL_PRINCIPAL,
    BLANCO,
    BORDE,
    EncabezadoDegradado,
    FONDO,
    NARANJA,
    Pill,
    ROJO,
    SUPERFICIE,
    TarjetaKPI,
    TEXTO,
    TEXTO_SECUNDARIO,
    VERDE,
)

LOGGER = logging.getLogger("automatizador")

AZUL_SUAVE = "#EAF2FF"


def _texto(valor: Any) -> str:
    return "" if valor is None else str(valor)


def _huella_candidato(candidato: dict[str, Any]) -> tuple[str, ...]:
    """Identidad estable para combinar candidatos de la revisión y de SQL."""

    empresa, fuente, empleado = sel.identidad(candidato)
    nombre = normalizar_nombre(
        candidato.get("nombre") or candidato.get("NOMBRE") or ""
    )
    area = normalizar_nombre(
        candidato.get("departamento")
        or candidato.get("DEPARTAMENTO")
        or candidato.get("area")
        or candidato.get("AREA")
        or ""
    )
    return (
        str(candidato.get("origen") or candidato.get("ORIGEN") or ""),
        empresa,
        fuente,
        empleado,
        nombre,
        area,
    )


def _combinar_candidatos(
    previos: Iterable[dict[str, Any]] | None,
    nuevos: Iterable[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Une candidatos previos y nuevos sin perder los ya disponibles.

    Los resultados nuevos tienen prioridad, pero un duplicado solo completa los
    campos que el candidato nuevo no traía. Si la lista nueva está vacía, se
    conserva íntegra la anterior. La consolidación para mostrar se hace después
    con ``coincidencias_vista.consolidar``.
    """

    salida: list[dict[str, Any]] = []
    posiciones: dict[tuple[str, ...], int] = {}
    for grupo in (nuevos, previos):
        for candidato in grupo or []:
            if not isinstance(candidato, dict):
                continue
            datos = dict(candidato)
            huella = _huella_candidato(datos)
            posicion = posiciones.get(huella)
            if posicion is None:
                posiciones[huella] = len(salida)
                salida.append(datos)
                continue
            base = salida[posicion]
            for clave, valor in datos.items():
                if clave not in base or base.get(clave) in (None, ""):
                    base[clave] = valor
    return salida


def filtrar_areas(
    areas: Iterable[str], texto: str = "", *, contiene: bool = False
) -> list[str]:
    """Deduplica y filtra áreas del maestro con coincidencia parcial o por subcadena."""

    unicas: dict[str, str] = {}
    for area in areas:
        nombre = str(area or "").strip()
        if nombre:
            unicas.setdefault(nombre.casefold(), nombre)
    consulta = str(texto or "").strip().casefold()
    if not consulta:
        return sorted(unicas.values(), key=str.casefold)

    if contiene:
        empiezan = [
            nombre for clave, nombre in unicas.items() if clave.startswith(consulta)
        ]
        resto = [
            nombre
            for clave, nombre in unicas.items()
            if consulta in clave and not clave.startswith(consulta)
        ]
        return sorted(empiezan, key=str.casefold) + sorted(resto, key=str.casefold)

    return sorted(
        (
            nombre
            for clave, nombre in unicas.items()
            if clave.startswith(consulta)
        ),
        key=str.casefold,
    )


class VentanaDecisionArea(tk.Toplevel):
    """Confirma si se reutiliza un área, se crea o se corrige su nombre."""

    def __init__(
        self,
        padre: tk.Misc,
        *,
        usuario: str,
        area: str,
        resultado: catalogo_areas.ResultadoArea,
        areas: list[str],
    ) -> None:
        super().__init__(padre)
        self.title("Confirmar nombre del área")
        self.geometry("620x390")
        self.resizable(False, False)
        self.configure(bg=FONDO)
        self.transient(padre)
        self.grab_set()
        self.decision: tuple[str, str] = ("cancelar", "")
        self._resultado = resultado
        self._var_area = tk.StringVar(value=area)
        opciones = list(dict.fromkeys(
            [resultado.area_reutilizable, *(item.nombre for item in resultado.similares), *areas]
        ))
        opciones = [item for item in opciones if item]
        sugerida = resultado.area_reutilizable or (
            resultado.similares[0].nombre if resultado.similares else ""
        )
        self._var_existente = tk.StringVar(
            value=sugerida or (opciones[0] if opciones else "")
        )

        ttk.Label(
            self,
            text="Revise el nombre del área antes de guardarlo",
            style="Seccion.TLabel",
            wraplength=550,
        ).pack(anchor="w", padx=18, pady=(16, 6))
        ttk.Label(
            self,
            text=f"Usuario: {usuario}",
            style="Subtitulo.TLabel",
        ).pack(anchor="w", padx=18, pady=(0, 10))
        ttk.Label(self, text="Nombre ingresado:").pack(anchor="w", padx=18)
        ttk.Entry(self, textvariable=self._var_area).pack(
            fill="x", padx=18, pady=(3, 8)
        )
        mensaje = resultado.mensaje
        if resultado.similares:
            mensaje += "\nSugerencia: " + ", ".join(
                f"{item.nombre} ({item.similitud:.0%})"
                for item in resultado.similares[:4]
            )
        ttk.Label(
            self,
            text=mensaje,
            style="Subtitulo.TLabel",
            wraplength=550,
            justify="left",
        ).pack(anchor="w", padx=18, pady=(0, 8))
        fila_existente = ttk.Frame(self)
        fila_existente.pack(fill="x", padx=18, pady=(0, 10))
        ttk.Label(fila_existente, text="Área que se reutilizará:").pack(
            side="left", padx=(0, 8)
        )
        self._combo_existente = ttk.Combobox(
            fila_existente,
            textvariable=self._var_existente,
            values=opciones,
            state="readonly",
        )
        self._combo_existente.pack(side="left", fill="x", expand=True)
        self._btn_existente = ttk.Button(
            self,
            text="Área ya existente",
            command=self._usar_existente,
            state="normal" if opciones else "disabled",
        )
        self._btn_existente.pack(fill="x", padx=18, pady=3)
        self._btn_nueva = ttk.Button(
            self,
            text="Sí, nueva área",
            style="Principal.TButton",
            command=self._usar_nueva,
            state="disabled" if resultado.existente else "normal",
        )
        self._btn_nueva.pack(fill="x", padx=18, pady=3)
        ttk.Button(
            self,
            text="Corregir nombre de área",
            command=self._corregir,
        ).pack(fill="x", padx=18, pady=3)
        ttk.Button(
            self, text="Cancelar", command=self.destroy
        ).pack(anchor="e", padx=18, pady=(4, 12))
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _usar_existente(self) -> None:
        area = self._var_existente.get().strip()
        if area:
            self.decision = ("existente", area)
            self.destroy()

    def _usar_nueva(self) -> None:
        area = self._var_area.get().strip()
        if area and not self._resultado.existente:
            self.decision = ("nueva", area)
            self.destroy()

    def _corregir(self) -> None:
        area = self._var_area.get().strip()
        if not area:
            messagebox.showwarning(
                "Nombre requerido", "Escriba el nombre corregido del área.", parent=self
            )
            return
        self.decision = ("corregir", area)
        self.destroy()


def pedir_decision_area(
    padre: tk.Misc, usuario: str, area: str, maestro: Maestro
) -> str | None:
    """Solicita reutilizar, crear o confirmar un área de forma directa y clara."""

    actual = str(area or "").strip()
    if not actual:
        messagebox.showwarning("Área vacía", "El nombre del área no puede estar vacío.", parent=padre)
        return None

    resultado = catalogo_areas.validar_area(actual, maestro=maestro)
    if not resultado.valido:
        messagebox.showwarning("Área no válida", resultado.mensaje, parent=padre)
        return None
    if resultado.existente:
        return resultado.area_reutilizable or resultado.nombre

    if resultado.similares:
        mejor = resultado.similares[0]
        respuesta = messagebox.askyesnocancel(
            "Área similar detectada",
            f"Para el usuario '{usuario}', escribió el área:\n  • \"{actual}\"\n\n"
            f"En el catálogo ya existe un área similar:\n  • \"{mejor.nombre}\" ({mejor.similitud:.0%} de coincidencia)\n\n"
            f"¿Desea usar el área existente '{mejor.nombre}'?\n\n"
            f"• SÍ: Usa '{mejor.nombre}' (recomendado para no duplicar áreas).\n"
            f"• NO: Registra '{actual}' como una NUEVA área.\n"
            f"• Cancelar: Vuelve atrás sin guardar cambios.",
            parent=padre,
        )
        if respuesta is True:
            return mejor.nombre
        if respuesta is False:
            return actual
        return None

    if messagebox.askyesno(
        "Registrar nueva área",
        f"El área \"{actual}\" no existe en el catálogo.\n\n"
        f"¿Confirma que desea crearla y asignarla a '{usuario}'?",
        parent=padre,
    ):
        return actual
    return None


class VentanaValidacion(tk.Toplevel):
    """Revisión asistida de usuarios y áreas antes de generar el Excel."""

    def __init__(
        self,
        padre: tk.Misc,
        revision: svc_revision.Revision,
        maestro: Maestro | None = None,
        *,
        al_asignar: Callable[[str, str, str], None] | None = None,
        al_generar: Callable[[str, bool], Any] | None = None,
        al_guardar_datos: Callable[[list[dict[str, Any]]], dict[str, Any] | None]
        | None = None,
    ) -> None:
        super().__init__(padre)
        self.title("Validación de Usuarios y Áreas")
        self.geometry("1280x800")
        self.minsize(1024, 640)
        self.configure(bg=FONDO)
        self.transient(padre)

        self.revision = revision
        self._padre = padre
        self.maestro = maestro or Maestro()
        self._al_asignar = al_asignar
        self._al_generar = al_generar
        self._al_guardar_datos = al_guardar_datos
        self._ocupado = False
        self._generando = False
        self._iids: dict[str, dict[str, Any]] = {}
        self._refresco_tabla_en_curso = False
        self._refresco_tabla_version = 0
        self._refresco_tabla_pendiente: tuple[int, list[dict[str, Any]], str, str, int, Any] | None = None
        self._pintado_tabla_after_id: str | None = None

        self._var_busqueda = tk.StringVar()
        self._var_estado = tk.StringVar(value="Todos")
        self._var_area_filtro = tk.StringVar(value="Todas")
        self._var_modo = tk.StringVar(value="Excel normal")
        self._var_resumen = tk.StringVar(value="")
        self._var_conexion = tk.StringVar(value="")
        self._var_seleccion = tk.StringVar(value="0 de 0 seleccionados")

        self._construir_cabecera()
        self._construir_kpis()
        self._construir_filtros()
        self._construir_tabla()
        self._construir_acciones()
        self._refrescar()

        self.protocol("WM_DELETE_WINDOW", self._cerrar)
        self.bind("<Escape>", lambda _e: self._cerrar())

    # ------------------------------------------------------------------
    # Construcción visual
    # ------------------------------------------------------------------
    def _construir_cabecera(self) -> None:
        encabezado = EncabezadoDegradado(self, alto=92)
        encabezado.pack(fill="x")
        contenido = encabezado.contenido
        contenido.configure(bg=AZUL_MARINO)

        bloque = tk.Frame(contenido, bg=AZUL_MARINO)
        bloque.pack(side="left", padx=22, pady=14)
        tk.Label(
            bloque,
            text="Validación de Usuarios y Áreas",
            bg=AZUL_MARINO,
            fg=BLANCO,
            font=(ui_tema.FUENTE, 17, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            bloque,
            text=(
                "Revise y complete la información de los usuarios que "
                "requieren atención."
            ),
            bg=AZUL_MARINO,
            fg="#C7DBFA",
            font=(ui_tema.FUENTE, 9),
            anchor="w",
        ).pack(anchor="w", pady=(2, 0))

        estado = tk.Frame(contenido, bg=AZUL_MARINO)
        estado.pack(side="right", padx=20)
        resumen = resumen_sincronizacion()
        self._pill_sql = Pill(
            estado,
            "SQL Server: desconectado",
            estilo="neutral",
            punto=True,
            fuente=ui_tema.FUENTE,
        )
        self._pill_sql.pack(anchor="e")
        tk.Label(
            estado,
            text=(
                "Última sincronización: "
                + (resumen.get("fecha") or "nunca")
            ),
            bg=AZUL_MARINO,
            fg="#C7DBFA",
            font=(ui_tema.FUENTE, 8),
            anchor="e",
        ).pack(anchor="e", pady=(4, 0))

    def _construir_kpis(self) -> None:
        marco = tk.Frame(self, bg=FONDO)
        marco.pack(fill="x", padx=18, pady=(12, 4))
        for indice in range(3):
            marco.columnconfigure(indice, weight=1, uniform="kpi")

        self._kpi_total = TarjetaKPI(
            marco,
            titulo="Total de registros",
            valor="0",
            detalle="Procesamiento actual",
            acento=AZUL_PRINCIPAL,
            icono="▤",
        )
        self._kpi_total.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        self._kpi_validadas = TarjetaKPI(
            marco,
            titulo="Áreas validadas",
            valor="0",
            detalle="Listas para generar",
            acento=VERDE,
            icono="✓",
        )
        self._kpi_validadas.grid(row=0, column=1, sticky="nsew", padx=8)

        self._kpi_pendientes = TarjetaKPI(
            marco,
            titulo="Por revisar",
            valor="0",
            detalle="Requieren atención",
            acento=NARANJA,
            icono="!",
        )
        self._kpi_pendientes.grid(row=0, column=2, sticky="nsew", padx=(8, 0))

    def _construir_filtros(self) -> None:
        marco = ttk.Frame(self)
        marco.pack(fill="x", padx=18, pady=(8, 4))

        ttk.Label(marco, text="Buscar:").pack(side="left")
        entrada = ttk.Entry(marco, textvariable=self._var_busqueda, width=34)
        entrada.pack(side="left", padx=(6, 14))
        self._filtro_after_id = None
        entrada.bind("<KeyRelease>", self._programar_refresco_filtro)

        ttk.Label(marco, text="Estado:").pack(side="left")
        self._combo_estado = ttk.Combobox(
            marco,
            textvariable=self._var_estado,
            values=(
                "Todos",
                ESTADO_VALIDADO,
                ESTADO_AMBIGUO,
                ESTADO_SIN_COINCIDENCIAS,
                ESTADO_SIN_DEPARTAMENTO,
                ESTADO_INACTIVO,
                ESTADO_CONFLICTO,
                ESTADO_MANUAL_PROTEGIDO,
                "Área vacía",
            ),
            state="readonly",
            width=24,
        )
        self._combo_estado.pack(side="left", padx=(6, 14))
        self._combo_estado.bind("<<ComboboxSelected>>", lambda _e: self._refrescar())

        ttk.Label(marco, text="Área:").pack(side="left")
        self._combo_area_filtro = ttk.Combobox(
            marco,
            textvariable=self._var_area_filtro,
            values=["Todas"],
            state="readonly",
            width=20,
        )
        self._combo_area_filtro.pack(side="left", padx=(6, 14))
        self._combo_area_filtro.bind("<<ComboboxSelected>>", lambda _e: self._refrescar())

        ttk.Button(
            marco, text="Actualizar", command=self._refrescar
        ).pack(side="left", padx=(0, 8))

        ttk.Button(
            marco,
            text="Revisar áreas repetidas",
            command=self._abrir_revision_areas_repetidas,
        ).pack(side="left")

        self._pill_revision = Pill(
            marco, "0 pendientes", estilo="warning", punto=True
        )
        self._pill_revision.pack(side="right")

    def _programar_refresco_filtro(self, _evento: Any = None) -> None:
        if self._filtro_after_id is not None:
            try:
                self.after_cancel(self._filtro_after_id)
            except tk.TclError:
                pass
        self._filtro_after_id = self.after(250, self._refrescar_filtro)

    def _refrescar_filtro(self) -> None:
        self._filtro_after_id = None
        self._refrescar()

    def _construir_tabla(self) -> None:
        barra_seleccion = ttk.Frame(self)
        barra_seleccion.pack(fill="x", padx=18, pady=(4, 0))
        ttk.Button(
            barra_seleccion,
            text="Seleccionar todos",
            command=self._seleccionar_todos,
        ).pack(side="left")
        ttk.Button(
            barra_seleccion,
            text="Limpiar selección",
            command=self._limpiar_seleccion,
        ).pack(side="left", padx=(6, 0))
        ttk.Label(
            barra_seleccion,
            textvariable=self._var_seleccion,
            style="Subtitulo.TLabel",
        ).pack(side="left", padx=(12, 0))
        ttk.Label(
            barra_seleccion,
            text="(Ctrl+A selecciona todas las filas visibles)",
            style="Subtitulo.TLabel",
        ).pack(side="right")

        marco = ttk.LabelFrame(
            self, text="Registros que necesitan revisión", padding=6
        )
        marco.pack(fill="both", expand=True, padx=18, pady=(6, 4))

        columnas = (
            "numero",
            "solicitante",
            "empresa",
            "ticket",
            "area",
            "estado",
            "acciones",
        )
        self._tabla = ttk.Treeview(
            marco,
            columns=columnas,
            show="headings",
            selectmode="extended",
        )
        titulos = {
            "numero": ("N.º", 55),
            "solicitante": ("Solicitante", 230),
            "empresa": ("Empresa", 120),
            "ticket": ("Ticket", 100),
            "area": ("Área actual", 260),
            "estado": ("Estado", 190),
            "acciones": ("Acciones", 230),
        }
        for columna, (titulo, ancho) in titulos.items():
            self._tabla.heading(columna, text=titulo)
            self._tabla.column(
                columna,
                width=ancho,
                anchor="w",
                stretch=columna in {"solicitante", "area", "acciones"},
            )

        barra_y = ttk.Scrollbar(marco, orient="vertical", command=self._tabla.yview)
        barra_x = ttk.Scrollbar(marco, orient="horizontal", command=self._tabla.xview)
        self._tabla.configure(
            yscrollcommand=barra_y.set, xscrollcommand=barra_x.set
        )
        self._tabla.grid(row=0, column=0, sticky="nsew")
        barra_y.grid(row=0, column=1, sticky="ns")
        barra_x.grid(row=1, column=0, sticky="ew")
        marco.rowconfigure(0, weight=1)
        marco.columnconfigure(0, weight=1)

        self._tabla.tag_configure("validado", background="#E7F7F0")
        self._tabla.tag_configure("ambiguo", background="#FDECEC")
        self._tabla.tag_configure("pendiente", background="#FEF3E2")
        self._tabla.tag_configure("conflicto", background="#F0ECFD")

        self._tabla.bind("<Double-1>", self._doble_clic)
        self._tabla.bind("<Button-1>", self._clic_acciones)
        self._tabla.bind("<Button-3>", self._mostrar_menu)
        self._tabla.bind("<<TreeviewSelect>>", self._al_cambiar_seleccion)
        self._tabla.bind("<Control-a>", self._atajo_seleccionar_todos)
        self._tabla.bind("<Control-A>", self._atajo_seleccionar_todos)
        self._menu = tk.Menu(self, tearoff=0)

    def _construir_acciones(self) -> None:
        marco = ttk.Frame(self)
        marco.pack(fill="x", padx=18, pady=(4, 12))

        izquierda = ttk.Frame(marco)
        izquierda.pack(side="left")
        self._btn_sql = ttk.Button(
            izquierda, text="Consultar SQL", command=self._consultar_sql
        )
        self._btn_sql.pack(side="left")
        ttk.Button(
            izquierda,
            text="Buscar en Excel maestro",
            command=self._buscar_en_maestro,
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            izquierda,
            text="Buscar otro nombre…",
            command=self._buscar_otro_nombre,
        ).pack(side="left", padx=(6, 0))
        self._btn_asignar = ttk.Button(
            izquierda, text="Asignar área", command=self._asignar_area
        )
        self._btn_asignar.pack(side="left", padx=6)
        ttk.Button(
            izquierda, text="Revisar coincidencias", command=self._revisar_coincidencias
        ).pack(side="left", padx=6)
        ttk.Button(
            izquierda, text="Ver detalles", command=self._ver_detalles
        ).pack(side="left", padx=6)
        ttk.Button(
            izquierda, text="Validar seleccionados", command=self._validar_seleccionados
        ).pack(side="left", padx=6)
        self._btn_guardar_data = ttk.Button(
            izquierda,
            text="Guardar áreas en data",
            command=self._guardar_areas_en_data,
        )
        self._btn_guardar_data.pack(side="left", padx=6)

        derecha = ttk.Frame(marco)
        derecha.pack(side="right")
        ttk.Label(derecha, text="Formato:").pack(side="left")
        ttk.Combobox(
            derecha,
            textvariable=self._var_modo,
            values=("Excel normal", "Excel a detalle"),
            state="readonly",
            width=17,
        ).pack(side="left", padx=(6, 12))
        self._btn_completo = ttk.Button(
            derecha,
            text="Generar Excel completo",
            style="Verde.TButton",
            command=self._generar_completo,
        )
        self._btn_completo.pack(side="left", padx=(0, 6))
        self._btn_parcial = ttk.Button(
            derecha,
            text="Generar Excel con pendientes",
            style="Naranja.TButton",
            command=self._generar_parcial,
        )
        self._btn_parcial.pack(side="left", padx=(0, 6))
        ttk.Button(
            derecha, text="Guardar y cerrar", command=self._cerrar
        ).pack(side="left")

        self._var_resumen.set(
            "Seleccione una o varias filas. Use «Seleccionar todos» para "
            "asignar un área de forma masiva o clic derecho para las acciones "
            "por fila."
        )
        ttk.Label(
            self,
            textvariable=self._var_resumen,
            style="Subtitulo.TLabel",
            anchor="w",
        ).pack(fill="x", padx=18, pady=(0, 8))

    # ------------------------------------------------------------------
    # Tabla y estado
    # ------------------------------------------------------------------
    @staticmethod
    def _filtrar_registros(
        registros: list[dict[str, Any]],
        busqueda: str,
        estado: str,
        filtro_area: str = "Todas",
    ) -> list[dict[str, Any]]:
        busqueda = busqueda.strip().lower()
        salida = []
        for registro in registros:
            if estado != "Todos" and str(registro.get("estado")) != estado:
                continue
            if filtro_area != "Todas":
                area_actual = str(registro.get("area_actual") or "").strip()
                if filtro_area == "(Sin área)":
                    if area_actual:
                        continue
                elif area_actual != filtro_area:
                    continue
            if busqueda:
                texto = " ".join(
                    str(registro.get(campo) or "")
                    for campo in ("usuario", "empresa", "ticket", "area_actual", "estado")
                ).lower()
                if busqueda not in texto:
                    continue
            salida.append(registro)
        return salida

    def _refrescar(self) -> None:
        self._refresco_tabla_version += 1
        version = self._refresco_tabla_version
        if self._pintado_tabla_after_id is not None:
            try:
                self.after_cancel(self._pintado_tabla_after_id)
            except tk.TclError:
                pass
            self._pintado_tabla_after_id = None
        self._tabla.delete(*self._tabla.get_children())
        self._iids.clear()
        self._var_resumen.set("Actualizando resultados…")
        snapshot = [dict(registro) for registro in self.revision.registros]
        total_registros = self.revision.total_registros
        validado = self.revision.validado
        filtro_area = self._var_area_filtro.get() if hasattr(self, "_var_area_filtro") else "Todas"
        self._refresco_tabla_pendiente = (
            version, snapshot, self._var_busqueda.get(), self._var_estado.get(),
            filtro_area, total_registros, validado,
        )
        if not self._refresco_tabla_en_curso:
            self._iniciar_refresco_tabla()

    def _iniciar_refresco_tabla(self) -> None:
        solicitud = self._refresco_tabla_pendiente
        if solicitud is None or self._refresco_tabla_en_curso:
            return
        self._refresco_tabla_pendiente = None
        self._refresco_tabla_en_curso = True
        version, snapshot, busqueda, estado, filtro_area, total_registros, validado = solicitud

        def preparar() -> tuple[int, list[dict[str, Any]], Any, dict[str, int], float, int, list[str]]:
            inicio = time.perf_counter()
            filtrados = self._filtrar_registros(snapshot, busqueda, estado, filtro_area)
            config = cargar_configuracion()
            cantidad_validadas = sum(1 for registro in snapshot if validado(registro))
            resumen = {
                "total_registros": total_registros,
                "areas_validadas": cantidad_validadas,
                "por_revisar": len(snapshot) - cantidad_validadas,
                "total_usuarios": len(snapshot),
            }
            areas_disponibles = sorted(
                {str(r.get("area_actual") or "").strip() for r in snapshot if str(r.get("area_actual") or "").strip()}
            )
            duracion = time.perf_counter() - inicio
            LOGGER.info(
                "Rendimiento filtro_validacion: preparacion=%.3fs leidos=%s procesados=%s mostrados=%s consultas=0 errores=0",
                duracion, len(snapshot), len(snapshot), len(filtrados),
            )
            return version, filtrados, config, resumen, duracion, len(snapshot), areas_disponibles

        def tarea() -> Any:
            inicio_error = time.perf_counter()
            try:
                return True, preparar()
            except Exception as exc:  # noqa: BLE001
                return False, (version, exc, time.perf_counter() - inicio_error, len(snapshot))

        ejecutar = getattr(self._padre, "_ejecutar_hilo", None)
        if ejecutar is None:
            # El padre normal es AplicacionTk y siempre proporciona este método.
            self._refresco_tabla_en_curso = False
            LOGGER.error("No existe el trabajador compartido para refrescar validación")
            return
        ejecutar(tarea, self._recibir_refresco_tabla, "filtro de validación")

    def _recibir_refresco_tabla(self, resultado: Any) -> None:
        exito, contenido = resultado
        self._refresco_tabla_en_curso = False
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        if exito:
            version, registros, config, resumen, _duracion, _leidos, areas_disponibles = contenido
            if version == self._refresco_tabla_version:
                if hasattr(self, "_combo_area_filtro"):
                    valores_actuales = list(self._combo_area_filtro["values"])
                    nuevos_valores = ["Todas", "(Sin área)", *areas_disponibles]
                    if valores_actuales != nuevos_valores:
                        self._combo_area_filtro["values"] = nuevos_valores
                self._pintar_registros_filtrados(registros, config, resumen)
        else:
            version, exc, duracion, leidos = contenido
            LOGGER.error(
                "Falló filtro_validacion (versión=%s duración=%.3fs leídos=%s errores=1 tipo=%s)",
                version, duracion, leidos, type(exc).__name__,
            )
            if version == self._refresco_tabla_version:
                self._var_resumen.set("No se pudo actualizar la lista de validación.")
        if self._refresco_tabla_pendiente is not None:
            self._iniciar_refresco_tabla()

    def _pintar_registros_filtrados(
        self, registros: list[dict[str, Any]], config: Any, resumen: dict[str, int]
    ) -> None:
        self._tabla.delete(*self._tabla.get_children())
        self._iids.clear()
        version = self._refresco_tabla_version
        filas: list[tuple[str, dict[str, Any], tuple[Any, ...], str]] = []
        for indice, registro in enumerate(registros, start=1):
            iid = f"r{indice}"
            self._iids[iid] = registro
            estado = str(registro.get("estado") or "")
            if registro.get("validado") or estado == ESTADO_VALIDADO:
                tag = "validado"
            elif "Conflicto" in estado:
                tag = "conflicto"
            elif estado in {ESTADO_AMBIGUO, ESTADO_SIN_COINCIDENCIAS}:
                tag = "ambiguo"
            else:
                tag = "pendiente"
            filas.append((iid, registro, (
                    indice,
                    _texto(registro.get("usuario")),
                    _texto(registro.get("empresa")),
                    _texto(registro.get("ticket")),
                    _texto(registro.get("area_actual")) or "—",
                    estado,
                    acciones.texto_acciones(registro),
                ), tag))
        inicio_visual = time.perf_counter()
        lote = 150

        def insertar(desde: int = 0) -> None:
            if version != self._refresco_tabla_version:
                return
            hasta = min(desde + lote, len(filas))
            for iid, _registro, valores, tag in filas[desde:hasta]:
                self._tabla.insert("", "end", iid=iid, values=valores, tags=(tag,))
            if hasta < len(filas):
                self._pintado_tabla_after_id = self.after(1, insertar, hasta)
                return
            self._pintado_tabla_after_id = None
            self._actualizar_kpis(config, resumen)
            self._actualizar_seleccion()
            LOGGER.info(
                "Rendimiento tabla_validacion: actualizacion_visual=%.3fs procesados=%s mostrados=%s",
                time.perf_counter() - inicio_visual, resumen["total_usuarios"], len(registros),
            )

        self._pintado_tabla_after_id = self.after_idle(insertar)

    def _actualizar_kpis(
        self, config: Any = None, resumen: dict[str, int] | None = None
    ) -> None:
        if resumen is None:
            resumen = self.revision.resumen()
        self._kpi_total.actualizar(
            valor=str(resumen["total_registros"]),
            detalle=f"{resumen['total_usuarios']} usuarios únicos",
        )
        self._kpi_validadas.actualizar(valor=str(resumen["areas_validadas"]))
        self._kpi_pendientes.actualizar(valor=str(resumen["por_revisar"]))
        pendientes = resumen["por_revisar"]
        self._pill_revision.actualizar(
            f"{pendientes} pendiente{'s' if pendientes != 1 else ''}",
            "success" if pendientes == 0 else "warning",
        )
        self._btn_completo.configure(
            state="normal" if pendientes == 0 else "disabled"
        )
        self._btn_parcial.configure(
            state="normal" if pendientes > 0 else "disabled"
        )
        if config is None:
            config = cargar_configuracion()
        if config.habilitado and config.configurada:
            if config.ultimo_estado == "conectado":
                self._pill_sql.actualizar("SQL Server: conectado", "success")
            else:
                self._pill_sql.actualizar("SQL Server: configurado", "info")
        else:
            self._pill_sql.actualizar("SQL Server: desconectado", "neutral")

    def _seleccionados(self) -> list[dict[str, Any]]:
        return [
            self._iids[iid]
            for iid in self._tabla.selection()
            if iid in self._iids
        ]

    def _seleccionar_todos(self) -> None:
        """Selecciona todas las filas visibles para operaciones masivas."""

        filas = self._tabla.get_children()
        if not filas:
            self._var_resumen.set(
                "No hay registros visibles para seleccionar. Ajuste los filtros."
            )
            return
        self._tabla.selection_set(*filas)
        self._tabla.focus(filas[0])
        self._actualizar_seleccion()
        self._var_resumen.set(
            f"{len(filas)} registros seleccionados. Use «Asignar área» para "
            "aplicar una asignación masiva."
        )

    def _limpiar_seleccion(self) -> None:
        seleccion = self._tabla.selection()
        if seleccion:
            self._tabla.selection_remove(*seleccion)
        self._actualizar_seleccion()

    def _atajo_seleccionar_todos(self, _evento: Any = None) -> str:
        self._seleccionar_todos()
        return "break"

    def _al_cambiar_seleccion(self, _evento: Any = None) -> None:
        self._actualizar_seleccion()

    def _actualizar_seleccion(self) -> None:
        if not hasattr(self, "_var_seleccion"):
            return
        cantidad = len(self._tabla.selection())
        total = len(self._tabla.get_children())
        self._var_seleccion.set(
            f"{cantidad} de {total} seleccionados"
        )
        boton = getattr(self, "_btn_asignar", None)
        if boton is not None:
            try:
                boton.configure(
                    text=(
                        "Asignar área (masiva)"
                        if cantidad > 1
                        else "Asignar área"
                    )
                )
            except tk.TclError:
                pass

    def _un_registro(self) -> dict[str, Any] | None:
        seleccionados = self._seleccionados()
        if len(seleccionados) != 1:
            messagebox.showinfo(
                "Seleccione una fila",
                "Elija exactamente un registro para esta acción.",
            )
            return None
        return seleccionados[0]

    def _doble_clic(self, evento) -> None:
        if self._tabla.identify_column(evento.x) == "#7":
            self._abrir_menu_fila(evento)
            return
        self._ver_detalles()

    def _clic_acciones(self, evento) -> None:
        """La celda «Acciones» abre el menú real de la fila seleccionada."""

        if self._tabla.identify_column(evento.x) != "#7":
            return
        self._abrir_menu_fila(evento)

    def _mostrar_menu(self, evento) -> None:
        self._abrir_menu_fila(evento)

    def _abrir_menu_fila(self, evento) -> None:
        iid = self._tabla.identify_row(evento.y)
        if not iid or iid not in self._iids:
            return
        self._tabla.selection_set(iid)
        self._tabla.focus(iid)
        self._mostrar_acciones(self._iids[iid], evento.x_root, evento.y_root)

    def _mostrar_acciones(
        self, registro: dict[str, Any], x: int, y: int
    ) -> None:
        menu = tk.Menu(self, tearoff=0)
        disponibles = acciones.acciones_disponibles(registro)
        if not disponibles:
            menu.add_command(label="Sin acciones disponibles", state="disabled")
        for accion in disponibles:
            menu.add_command(
                label=accion.etiqueta,
                command=lambda a=accion: self._ejecutar_accion(
                    a.identificador, registro
                ),
            )
        self._menu = menu
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _ejecutar_accion(
        self, identificador: str, registro: dict[str, Any]
    ) -> None:
        """Despacha una acción del menú de fila a su operación real."""

        if identificador == acciones.ACCION_CONSULTAR:
            self._consultar_sql()
        elif identificador == acciones.ACCION_ASIGNAR:
            self._asignar_area()
        elif identificador == acciones.ACCION_REVISAR:
            self._abrir_seleccion(registro)
        elif identificador == acciones.ACCION_DETALLES:
            VentanaDetalleRegistro(self, registro)
        elif identificador == acciones.ACCION_VALIDAR:
            self._validar_registros([registro])
        else:  # pragma: no cover - acción desconocida
            messagebox.showinfo(
                "Acción no disponible",
                "Esta acción no tiene una operación asociada.",
            )

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------
    def _buscar_en_maestro(self) -> None:
        registro = self._un_registro()
        if registro is None:
            return
        VentanaBuscarExcelMaestro(
            self,
            usuario=str(registro.get("usuario") or ""),
            maestro=self.maestro,
            al_elegir=lambda candidato: self._elegir_candidato(
                registro, candidato
            ),
        )

    def _buscar_otro_nombre(self) -> None:
        registro = self._un_registro()
        if registro is None:
            return
        VentanaConsultaSqlManual(
            self,
            usuario=str(registro.get("usuario") or ""),
            al_elegir=lambda candidato: self._elegir_candidato(
                registro, candidato
            ),
        )

    def _consultar_sql(self) -> None:
        registros = self._seleccionados()
        if not registros:
            messagebox.showinfo(
                "Sin selección", "Seleccione uno o varios registros."
            )
            return
        if self._ocupado:
            return
        config = cargar_configuracion()
        if not (config.habilitado and config.configurada):
            messagebox.showwarning(
                "SQL Server no configurado",
                "Configure la conexión desde Configuración → Conexión SQL Server.",
            )
            return
        if not controlador_disponible():
            messagebox.showerror(
                "pyodbc no disponible",
                "Instale pyodbc y el controlador ODBC para consultar SQL Server.",
            )
            return

        # Planificar antes de abrir la ventana de seguimiento. Para tres o más
        # palabras se pide el orden; al cancelar, ese registro no consulta SQL.
        planes: list[dict[str, Any]] = []
        vistos: set[str] = set()
        omitidos: list[str] = []
        for registro in registros:
            usuario = str(registro.get("usuario") or "").strip()
            if not usuario:
                continue
            plan = busqueda_core.plan_busqueda(usuario)
            if not plan.terminos:
                omitidos.append(usuario)
                continue
            if plan.requiere_orden_manual:
                orden = VentanaOrdenTerminos.pedir(self, plan)
                if orden is None:
                    omitidos.append(usuario)
                    continue
                try:
                    plan = plan.con_orden(orden)
                except ValueError as exc:
                    messagebox.showwarning("Orden no válido", str(exc))
                    omitidos.append(usuario)
                    continue
            if plan.clave in vistos:
                continue
            vistos.add(plan.clave)
            planes.append(plan.como_dict())

        if not planes:
            self._var_resumen.set(
                "No se ejecutó ninguna consulta: no hay términos válidos o se "
                "canceló el ordenamiento."
            )
            return

        ventana = VentanaSeguimientoConsulta(
            self,
            solicitantes=[str(plan.get("solicitante") or "") for plan in planes],
            ordenes=[
                busqueda_core.descripcion_orden(plan.get("terminos") or [])
                for plan in planes
            ],
            empresas=list(EMPRESAS),
        )
        self._ocupado = True
        self._btn_sql.configure(state="disabled")
        self._var_resumen.set("Consultando SQL Server por lotes…")

        def tarea():
            from database.repository import RepositorioEmpleados

            repositorio = RepositorioEmpleados(config)
            consulta = repositorio.buscar_por_consultas(
                planes,
                al_progreso=ventana.encolar,
                debe_cancelar=lambda: ventana.debe_cancelar,
            )
            if consulta.empleados:
                try:
                    Sincronizador(
                        config, repositorio=repositorio
                    ).actualizar_desde_busqueda(consulta.empleados)
                except Exception:  # noqa: BLE001 - la caché no bloquea la consulta
                    LOGGER.exception("No se pudo actualizar la caché tras la búsqueda")
            resolver = ResolverAreas(cargar_empleados(), maestro=self.maestro)
            resoluciones = {}
            for plan in planes:
                clave = str(plan.get("clave") or "")
                candidatos = consulta.por_consulta.get(clave) or []
                # Se resuelve exclusivamente con lo devuelto por la consulta.
                # Si no hay coincidencias, se informa; no se reutiliza una
                # coincidencia antigua de la caché local.
                resoluciones[clave] = resolver.resolver_candidatos(
                    plan.get("solicitante"), candidatos
                )
            return (
                resoluciones,
                list(consulta.errores),
                list(consulta.empresas_sin_datos),
                list(consulta.empresas_consultadas),
                consulta.resumen(),
                list(consulta.eventos),
                consulta.cancelada,
                omitidos,
            )

        def terminar(resultado):
            self._ocupado = False
            self._btn_sql.configure(state="normal")
            if isinstance(resultado, Exception):
                try:
                    ventana.finalizar(mensaje=f"Error: {resultado}")
                except tk.TclError:
                    pass
                self._var_resumen.set(f"Error al consultar SQL: {resultado}")
                messagebox.showerror("Error de consulta", str(resultado))
                return
            (
                resoluciones,
                errores,
                sin_datos,
                consultadas,
                resumen,
                _eventos,
                cancelada,
                omitidos_orden,
            ) = resultado
            asignados = 0
            ambiguos: list[dict[str, Any]] = []
            for registro in registros:
                clave = normalizar_nombre(registro.get("usuario"))
                resolucion = resoluciones.get(clave) or resoluciones.get(
                    str(registro.get("clave") or "")
                )
                if resolucion is None:
                    continue
                elegido = resolucion.empleado_dict()
                if elegido and resolucion.origen_area:
                    elegido["ORIGEN_AREA"] = resolucion.origen_area
                # Nunca se descartan candidatos válidos ya guardados. Si la
                # consulta no devuelve filas, se conserva lo que existía; si
                # devuelve una única coincidencia, también queda como candidato.
                nuevos = resolucion.candidatos_dict()
                if elegido:
                    nuevos = [dict(elegido)] + nuevos
                candidatos = _combinar_candidatos(
                    registro.get("candidatos"), nuevos
                )
                # Para una consulta individual, incluso una coincidencia
                # inequívoca queda pendiente hasta que el usuario confirme
                # explícitamente el área propuesta en el modal.
                confirmar_individual = (
                    len(registros) == 1
                    and resolucion.asignable
                    and resolucion.caso == CASO_A
                )
                if confirmar_individual:
                    actualizado = self.revision.actualizar(
                        registro.get("usuario"),
                        estado=resolucion.estado,
                        validado=False,
                        candidatos=candidatos,
                        seleccionado=elegido,
                        motivo=resolucion.motivo,
                        tipo_resolucion="automatica",
                        accion="consulta_sql_pendiente_confirmacion",
                    )
                    actualizado["area_sugerida"] = resolucion.area
                elif resolucion.asignable and resolucion.caso == CASO_A:
                    self.revision.actualizar(
                        registro.get("usuario"),
                        area=resolucion.area,
                        estado=ESTADO_VALIDADO,
                        validado=True,
                        candidatos=candidatos,
                        seleccionado=elegido,
                        origen=procedencia.SQL_SERVER,
                        motivo=resolucion.motivo,
                        tipo_resolucion="automatica",
                        accion="consulta_sql",
                    )
                    self._asignar_en_maestro(
                        str(registro.get("usuario") or ""),
                        resolucion.area,
                        procedencia.SQL_SERVER,
                        detalle=sel.registro_trazable(
                            elegido,
                            area=resolucion.area,
                            tipo_resolucion="automatica",
                        )
                        if elegido
                        else None,
                    )
                    asignados += 1
                elif resolucion.conflicto:
                    actualizado = self.revision.actualizar(
                        registro.get("usuario"),
                        estado=resolucion.estado,
                        validado=False,
                        candidatos=candidatos,
                        seleccionado=elegido or None,
                        motivo=resolucion.motivo,
                        tipo_resolucion="automatica",
                        accion="conflicto_sql",
                    )
                    actualizado["area_sugerida"] = resolucion.area
                else:
                    actualizado = self.revision.actualizar(
                        registro.get("usuario"),
                        estado=resolucion.estado,
                        validado=False,
                        candidatos=candidatos,
                        seleccionado=elegido or None,
                        motivo=resolucion.motivo,
                        tipo_resolucion="automatica",
                        accion="consulta_sql",
                    )
                    actualizado["area_sugerida"] = resolucion.area
                    if resolucion.caso == CASO_B:
                        ambiguos.append(registro)
            self.revision.guardar()
            self._refrescar()
            try:
                eventos.publicar(
                    eventos.CONSULTA_SQL_FINALIZADA,
                    origen="validacion",
                    consultadas=len(consultadas),
                    empleados=resumen.get("empleados", 0),
                    asignados=asignados,
                    ambiguos=len(ambiguos),
                    cancelada=bool(cancelada),
                    errores=len(errores),
                )
            except Exception as exc:  # noqa: BLE001 - no bloquea el resultado
                LOGGER.error("No se pudo publicar el fin de la consulta: %s", exc)
            mensaje = (
                f"Consulta {'cancelada' if cancelada else 'completada'} en "
                f"{len(consultadas)} empresa(s). "
                f"Empleados encontrados: {resumen.get('empleados', 0)}. "
                f"Áreas asignadas: {asignados}. "
                f"Requieren selección manual: {len(ambiguos)}."
            )
            if sin_datos:
                mensaje += f" Empresas sin datos: {len(sin_datos)}."
            if errores:
                detalle = "; ".join(
                    f"{item.get('empresa', '?')} ({item.get('categoria', 'error')}): "
                    f"{item.get('mensaje', '')}"
                    for item in errores[:5]
                )
                mensaje += (
                    f" Empresas con error: {len(errores)}. {detalle}"
                )
            if omitidos_orden:
                mensaje += (
                    " Sin consultar por orden cancelado: "
                    + ", ".join(omitidos_orden)
                    + "."
                )
            if cancelada:
                mensaje += " Se conservaron los resultados válidos obtenidos."
            self._var_resumen.set(mensaje)
            try:
                if ventana.winfo_exists():
                    ventana.finalizar(resumen=resumen, mensaje=mensaje)
            except tk.TclError:
                pass
            # En una búsqueda individual, si la revisión ya se persistió y
            # quedó asignada, liberar únicamente la ventana de seguimiento.
            # Las búsquedas por lote conservan el resumen visible.
            if (
                not cancelada
                and len(registros) == 1
                and asignados == 1
                and not ambiguos
            ):
                try:
                    ventana.cerrar_tras_asignacion()
                except tk.TclError:
                    pass
            # Una sola coincidencia con área disponible pero no asignada
            # (por ejemplo, empleado inactivo) requiere confirmación explícita.
            if not cancelada and len(registros) == 1 and asignados == 0 and not ambiguos:
                registro = registros[0]
                clave = normalizar_nombre(registro.get("usuario"))
                resolucion = resoluciones.get(clave) or resoluciones.get(
                    str(registro.get("clave") or "")
                )
                if resolucion and resolucion.caso != CASO_B and not resolucion.conflicto:
                    candidato = resolucion.empleado_dict()
                    if not candidato and resolucion.candidatos:
                        candidato = resolucion.candidatos[0].como_dict()
                    if candidato:
                        area_propuesta = (
                            resolucion.area
                            or candidato.get("DEPARTAMENTO")
                            or candidato.get("departamento")
                            or candidato.get("PUESTO")
                            or candidato.get("puesto")
                            or ""
                        )
                        VentanaConfirmarAsignacionArea(
                            self,
                            usuario=str(registro.get("usuario") or ""),
                            candidato=candidato,
                            area=str(area_propuesta or "Área no disponible"),
                            area_sql=str(area_propuesta or ""),
                            area_maestro=str(
                                self.maestro.buscar(registro.get("usuario")) or ""
                            ),
                            motivo=resolucion.motivo,
                            inactivo=resolucion.estado == ESTADO_INACTIVO,
                            al_confirmar=lambda: self._confirmar_y_cerrar_seguimiento(
                                ventana, registro, candidato, resolucion
                            ),
                        )
            # Una coincidencia ambigua se resuelve mediante selección manual.
            elif not cancelada and len(registros) == 1 and len(ambiguos) == 1:
                self.after(10, lambda: self._abrir_seleccion(ambiguos[0]))
            elif not cancelada and len(registros) == 1 and not resumen.get("empleados", 0):
                self.after(10, lambda: self._abrir_seleccion(registros[0]))

        # Iniciar el trabajador después de definir la tarea y su callback de
        # finalización. Si esta llamada queda dentro de otro método o detrás
        # de un return, la ventana aparece pero la consulta nunca se ejecuta.
        self._ejecutar(tarea, terminar)

    def _confirmar_y_cerrar_seguimiento(
        self,
        ventana: "VentanaSeguimientoConsulta",
        registro: dict[str, Any],
        candidato: dict[str, Any],
        resolucion: Any,
    ) -> bool:
        asignada = self._confirmar_o_pedir_area(registro, candidato, resolucion)
        if asignada and resolucion.asignable and resolucion.area:
            try:
                ventana.cerrar_tras_asignacion()
            except tk.TclError:
                pass
        return asignada

    def _confirmar_o_pedir_area(
        self, registro: dict[str, Any], candidato: dict[str, Any], resolucion: Any
    ) -> bool:
        if resolucion.asignable and resolucion.area:
            return bool(self._elegir_candidato(
                registro,
                candidato,
                permitir_inactivo=True,
                confirmacion_previa=True,
            ))
        VentanaAsignarArea(
            self,
            usuario=str(registro.get("usuario") or ""),
            areas=self._areas_disponibles(),
            al_guardar=lambda area: self._aplicar_asignacion(registro, area),
        )
        return True

    def _asignar_area(self) -> None:
        registros = self._seleccionados()
        if not registros:
            messagebox.showinfo(
                "Sin selección",
                "Seleccione uno o varios registros para asignar un área.",
            )
            return
        if len(registros) == 1:
            registro = registros[0]
            VentanaAsignarArea(
                self,
                usuario=str(registro.get("usuario") or ""),
                areas=self._areas_disponibles(),
                al_guardar=lambda area: self._aplicar_asignacion(registro, area),
            )
            return
        VentanaAsignarArea(
            self,
            usuario=f"{len(registros)} registros seleccionados",
            etiqueta=f"Asignación masiva: {len(registros)} registros",
            areas=self._areas_disponibles(),
            al_guardar=lambda area: self._aplicar_asignacion_masiva(
                registros, area
            ),
        )

    def _crear_area_desde_coincidencia(
        self, registro: dict[str, Any], candidato: dict[str, Any]
    ) -> None:
        """Permite crear o reutilizar un área desde una coincidencia."""

        usuario = str(registro.get("usuario") or "")
        sugerida = str(
            candidato.get("departamento")
            or candidato.get("DEPARTAMENTO")
            or candidato.get("area")
            or ""
        ).strip()
        VentanaAsignarArea(
            self,
            usuario=usuario,
            areas=self._areas_disponibles(),
            area_inicial=sugerida,
            al_guardar=lambda area: self._aplicar_area_nueva(
                registro, candidato, area
            ),
        )

    def _aplicar_area_nueva(
        self,
        registro: dict[str, Any],
        candidato: dict[str, Any],
        area: str,
    ) -> None:
        """Valida el área, pide confirmación y la asigna al usuario."""

        usuario = str(registro.get("usuario") or "")
        area_final = pedir_decision_area(self, usuario, area, self.maestro)
        if not area_final:
            return

        if not self._confirmar_asignacion_candidato(
            usuario, candidato, area_final
        ):
            return

        try:
            creacion = catalogo_areas.crear_area_para_usuario(
                self.maestro,
                usuario,
                area_final,
                confirmar=True,
                usuario_ejecutor=os.getenv("USERNAME", ""),
            )
        except Exception as exc:  # noqa: BLE001 - se informa sin cerrar la app
            LOGGER.exception("No se pudo registrar el área %s", area_final)
            messagebox.showerror(
                "No se pudo registrar el área", f"{usuario}: {exc}"
            )
            return
        if creacion.asignacion is None:
            messagebox.showwarning(
                "Área no registrada",
                creacion.mensaje or "No se modificó el maestro.",
            )
            return

        self.revision.registrar_seleccion(
            usuario,
            candidato,
            area=area_final,
            estado=ESTADO_VALIDADO,
            motivo="Área creada o reutilizada desde una coincidencia.",
            tipo_resolucion="manual",
        )
        self.revision.guardar()
        try:
            procedencia.registrar(
                usuario,
                area_final,
                procedencia.MANUAL,
                nota="Validación de usuarios y áreas.",
                detalle=sel.registro_trazable(
                    candidato, area=area_final, tipo_resolucion="manual"
                ),
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("No se pudo registrar la procedencia: %s", exc)
        self._refrescar()
        self._var_resumen.set(
            f"{usuario}: área '{area_final}' registrada y asignada."
        )

    def _confirmar_asignacion_candidato(
        self, usuario: str, candidato: dict[str, Any], area: str
    ) -> bool:
        """Confirma la asignación y advierte expresamente si está inactivo."""

        preparacion = asistida.preparar_asignacion(
            usuario, candidato, area=area
        )
        if not preparacion.area:
            messagebox.showwarning(
                "Sin área", preparacion.mensaje or "No hay un área asignable."
            )
            return False
        if preparacion.inactivo:
            return messagebox.askyesno(
                "Usuario inactivo",
                preparacion.mensaje
                + "\n\n¿Confirma que desea asignarle un área sin reactivar "
                "su cuenta?",
            )
        return messagebox.askyesno(
            "Confirmar asignación",
            f"{preparacion.mensaje}\n\n¿Desea continuar?",
        )

    def _aplicar_asignacion(self, registro: dict[str, Any], area: str) -> None:
        area = str(area or "").strip()
        if not area:
            messagebox.showwarning("Área vacía", "Indique un área válida.")
            return
        if not self._asignar_en_maestro(
            str(registro.get("usuario") or ""), area, procedencia.MANUAL
        ):
            return
        self.revision.asignar_manual(registro.get("usuario"), area)
        self.revision.guardar()
        self._refrescar()
        self._var_resumen.set(
            f"{registro.get('usuario')}: área asignada manualmente → {area}"
        )

    def _aplicar_asignacion_masiva(
        self, registros: Iterable[dict[str, Any]], area: str
    ) -> None:
        """Asigna la misma área a todos los registros seleccionados."""

        area = str(area or "").strip()
        registros = list(registros)
        if not area:
            messagebox.showwarning("Área vacía", "Indique un área válida.")
            return
        if not registros:
            messagebox.showinfo(
                "Sin selección", "No hay registros para asignar."
            )
            return
        total = len(registros)
        if not messagebox.askyesno(
            "Confirmar asignación masiva",
            f"Se asignará el área '{area}' a {total} registro(s).\n\n"
            "La operación actualizará el Excel maestro y la revisión actual.\n\n"
            "¿Desea continuar?",
            parent=self,
        ):
            return

        asignados = 0
        fallidos: list[str] = []
        for registro in registros:
            usuario = str(registro.get("usuario") or "").strip()
            if not usuario:
                continue
            if not self._asignar_en_maestro(usuario, area, procedencia.MANUAL):
                fallidos.append(usuario)
                continue
            try:
                self.revision.asignar_manual(usuario, area)
            except Exception as exc:  # noqa: BLE001 - un registro no detiene el lote
                LOGGER.error(
                    "No se pudo actualizar la revisión para %s: %s", usuario, exc
                )
                fallidos.append(usuario)
                continue
            asignados += 1

        if not asignados:
            self._var_resumen.set(
                f"Asignación masiva sin cambios: no se pudo asignar '{area}'."
            )
            return

        self.revision.guardar()
        self._refrescar()
        mensaje = (
            f"Asignación masiva: {asignados} de {total} registros → {area}"
        )
        if fallidos:
            mensaje += f". No asignados: {len(fallidos)}"
        self._var_resumen.set(mensaje)
        if fallidos:
            messagebox.showwarning(
                "Asignación masiva parcial",
                f"{mensaje}.\n\nRevise los registros no asignados antes de "
                "continuar.",
                parent=self,
            )

    def _asignar_en_maestro(
        self,
        usuario: str,
        area: str,
        origen: str,
        *,
        detalle: dict[str, Any] | None = None,
    ) -> bool:
        if self._al_asignar is not None:
            try:
                guardada = self._al_asignar(usuario, area, origen)
                if guardada is False:
                    return False
            except Exception as exc:  # noqa: BLE001
                LOGGER.error("No se pudo persistir la asignación: %s", exc)
                messagebox.showerror(
                    "No se pudo guardar el área",
                    f"La asignación de '{usuario}' no se guardó: {exc}",
                )
                return False
        try:
            procedencia.registrar(
                usuario,
                area,
                origen,
                nota="Validación de usuarios y áreas.",
                detalle=detalle,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("No se pudo registrar la procedencia: %s", exc)
        return True

    def _revisar_coincidencias(self) -> None:
        registro = self._un_registro()
        if registro is None:
            return
        self._abrir_seleccion(registro)

    def _abrir_seleccion(self, registro: dict[str, Any]) -> None:
        usuario = str(registro.get("usuario") or "")
        # El registro de la tabla puede ser una copia anterior a la última
        # consulta SQL. Se prefiere el registro vivo de la revisión, pero sin
        # descartar candidatos válidos que solo estén en la copia recibida.
        vigente = self.revision.buscar(usuario) or registro
        area_maestro = self.maestro.buscar(usuario) or ""
        candidatos = _combinar_candidatos(
            registro.get("candidatos"), vigente.get("candidatos")
        )
        if candidatos:
            candidatos = coincidencias_vista.consolidar(
                candidatos, area_maestro=area_maestro
            )
        if not candidatos:
            # Cuando SQL no devuelve filas, ofrecer el maestro como fallback
            # manual. Se usa ``usar_sql=False``: esta ruta nunca repite la
            # consulta SQL ni descarta resultados ya obtenidos.
            from database.coincidencias import ConsolidadorCoincidencias

            grupos = ConsolidadorCoincidencias(maestro=self.maestro).buscar(
                [usuario], usar_sql=False
            )
            grupo = next(iter(grupos.values()), None)
            maestro = [
                item
                for item in (grupo.candidatos if grupo else [])
                if str(item.get("origen") or "").upper() == "MAESTRO"
            ]
            candidatos = coincidencias_vista.consolidar(
                maestro, area_maestro=area_maestro
            )
            if not candidatos:
                if messagebox.askyesno(
                    "Sin coincidencias SQL ni maestro",
                    "No se encontró el usuario en SQL ni en el Excel maestro. ¿Desea abrir la consulta manual?",
                ):
                    VentanaConsultaSqlManual(
                        self, usuario=usuario,
                        al_elegir=lambda candidato: self._elegir_candidato(vigente, candidato),
                    )
                return
        VentanaCoincidencias(
            self,
            usuario=usuario,
            candidatos=candidatos,
            al_elegir=lambda candidato: self._elegir_candidato(vigente, candidato),
            al_asignar_inactivo=lambda candidato: self._elegir_candidato(
                vigente, candidato, permitir_inactivo=True
            ),
            al_crear_area=lambda candidato: self._crear_area_desde_coincidencia(
                vigente, candidato
            ),
        )

    def _elegir_candidato(
        self,
        registro: dict[str, Any],
        candidato: dict[str, Any],
        *,
        permitir_inactivo: bool = False,
        confirmacion_previa: bool = False,
    ) -> bool:
        """Resuelve el área usando exclusivamente el registro elegido."""

        usuario = str(registro.get("usuario") or "")
        resolver = ResolverAreas(cargar_empleados(), maestro=self.maestro)
        origen_candidato = str(
            candidato.get("origen") or candidato.get("ORIGEN") or ""
        ).strip().upper()
        if origen_candidato == "MAESTRO":
            area_maestro = str(
                candidato.get("AREA_MAESTRO")
                or candidato.get("area")
                or candidato.get("AREA")
                or ""
            ).strip()
            if not area_maestro:
                messagebox.showwarning(
                    "Área no disponible",
                    "El registro del Excel maestro no contiene un área.",
                )
                return False
            if not confirmacion_previa and not self._confirmar_asignacion_candidato(
                usuario, candidato, area_maestro
            ):
                return False
            if not self._asignar_en_maestro(
                usuario,
                area_maestro,
                procedencia.MANUAL,
                detalle=sel.registro_trazable(
                    candidato, area=area_maestro, tipo_resolucion="manual"
                ),
            ):
                return False
            self.revision.registrar_seleccion(
                usuario,
                candidato,
                area=area_maestro,
                estado=ESTADO_VALIDADO,
                motivo="Área seleccionada del Excel maestro.",
                tipo_resolucion="manual",
                origen="MAESTRO",
            )
            self.revision.guardar()
            self._refrescar()
            self._var_resumen.set(
                f"{usuario}: área del Excel maestro asignada → {area_maestro}."
            )
            return True
        resolucion = resolver.resolver_candidato(usuario, candidato)
        puede_asignar = resolucion.caso == CASO_A or (
            permitir_inactivo and resolucion.estado == ESTADO_INACTIVO
        )
        area = resolucion.area if puede_asignar and resolucion.asignable else ""
        estado = ESTADO_VALIDADO if area else resolucion.estado
        if area and not confirmacion_previa and not self._confirmar_asignacion_candidato(
            usuario, candidato, area
        ):
            return False
        if area:
            if not self._asignar_en_maestro(
                usuario,
                area,
                procedencia.MANUAL,
                detalle=sel.registro_trazable(
                    candidato, area=area, tipo_resolucion="manual"
                ),
            ):
                return False
            mensaje = (
                f"{usuario}: seleccionado {sel.valor(candidato, 'empresa')} / "
                f"{sel.valor(candidato, 'fuente')} → área {area}."
            )
        else:
            mensaje = (
                f"{usuario}: selección guardada sin asignar área. "
                f"{resolucion.motivo}"
            )
            messagebox.showwarning("Selección sin área", mensaje)
        self.revision.registrar_seleccion(
            usuario,
            candidato,
            area=area,
            estado=estado,
            motivo=resolucion.motivo,
            tipo_resolucion="manual",
        )
        self.revision.guardar()
        self._refrescar()
        self._var_resumen.set(mensaje)
        return bool(area)

    def _validar_seleccionados(self) -> None:
        registros = self._seleccionados()
        if not registros:
            messagebox.showinfo("Sin selección", "Seleccione al menos una fila.")
            return
        self._validar_registros(registros)

    def _validar_registros(self, registros: Iterable[dict[str, Any]]) -> None:
        registros = list(registros)
        resumen = acciones.resumen_validacion(registros)
        if not resumen["validables"]:
            if resumen.get("ya_validados"):
                messagebox.showinfo(
                    "Ya validados",
                    f"{resumen['ya_validados']} registro(s) ya están validados. "
                    "No se repitió ninguna operación.",
                )
            else:
                messagebox.showwarning(
                    "Sin área para validar",
                    "Los registros seleccionados no tienen área actual. Asigne "
                    "un área o consulte SQL antes de validarlos.",
                )
            return
        if resumen["omitidos"] and not messagebox.askyesno(
            "Validación parcial",
            f"{resumen['omitidos']} de {resumen['total']} registros no tienen "
            "área y no se validarán.\n\n¿Desea validar los "
            f"{resumen['validables']} registros que sí tienen área?",
        ):
            return
        validados = 0
        for registro in registros:
            if not acciones.accion_disponible(
                acciones.ACCION_VALIDAR, registro
            ):
                continue
            self.revision.actualizar(
                registro.get("usuario"),
                estado=ESTADO_VALIDADO,
                validado=True,
                accion="validacion_manual",
            )
            validados += 1
        if not validados:
            return
        self.revision.guardar()
        self._refrescar()
        mensaje = f"Registros marcados como validados: {validados}"
        if resumen["omitidos"]:
            mensaje += f". Omitidos sin área: {resumen['omitidos']}"
        if resumen.get("ya_validados"):
            mensaje += f". Ya validados: {resumen['ya_validados']}"
        self._var_resumen.set(mensaje)

    def _guardar_areas_en_data(self) -> None:
        """Persiste las áreas de la revisión en data sin generar los Excel."""

        registros: list[dict[str, Any]] = []
        vistos: set[str] = set()
        sin_area = 0
        for registro in self.revision.registros:
            usuario = str(registro.get("usuario") or "").strip()
            area = str(registro.get("area_actual") or "").strip()
            if not usuario:
                continue
            if not area:
                sin_area += 1
                continue
            clave = normalizar_nombre(usuario) or usuario.casefold()
            if clave in vistos:
                continue
            vistos.add(clave)
            registros.append({"usuario": usuario, "area": area})

        if not registros:
            messagebox.showinfo(
                "Sin áreas para guardar",
                "Todavía no hay áreas asignadas en esta revisión. Asigne un "
                "área antes de guardar en data.",
                parent=self,
            )
            return

        destino = str(cfg.MAESTRO_XLSX)
        if not messagebox.askyesno(
            "Guardar áreas en data",
            f"Se guardarán {len(registros)} área(s) en:\n{destino}\n\n"
            "Esta acción NO genera ni modifica el Excel de tickets; solo "
            "actualiza la base persistente de usuarios y áreas.\n\n"
            "¿Desea continuar?",
            parent=self,
        ):
            return

        resultado: dict[str, Any] | None
        if self._al_guardar_datos is not None:
            try:
                resultado = self._al_guardar_datos(registros)
            except Exception as exc:  # noqa: BLE001 - se informa sin cerrar
                LOGGER.exception("No se pudieron guardar las áreas en data")
                messagebox.showerror(
                    "No se pudo guardar en data",
                    f"Las áreas no se guardaron: {exc}",
                    parent=self,
                )
                return
        else:
            guardadas = 0
            fallidas: list[str] = []
            for item in registros:
                if self._asignar_en_maestro(
                    item["usuario"], item["area"], procedencia.MANUAL
                ):
                    guardadas += 1
                else:
                    fallidas.append(item["usuario"])
            resultado = {
                "guardadas": guardadas,
                "sin_cambios": 0,
                "fallidas": fallidas,
                "ruta": destino,
            }

        if not resultado:
            messagebox.showinfo(
                "Guardado no realizado",
                "No se guardó ningún cambio en data.",
                parent=self,
            )
            return

        error = str(resultado.get("error") or "").strip()
        if error:
            messagebox.showerror(
                "No se pudo guardar en data", error, parent=self
            )
            return

        guardadas = int(resultado.get("guardadas") or 0)
        nuevas = int(resultado.get("nuevas") or 0)
        actualizadas = int(resultado.get("actualizadas") or 0)
        sin_cambios = int(resultado.get("sin_cambios") or 0)
        fallidas = [str(usuario) for usuario in (resultado.get("fallidas") or [])]
        ruta = str(resultado.get("ruta") or destino)

        try:
            self.revision.guardar()
        except Exception as exc:  # noqa: BLE001 - data ya se guardó
            LOGGER.error(
                "Las áreas se guardaron en data, pero no se pudo guardar la "
                "revisión: %s",
                exc,
            )
        self._refrescar()

        partes = [f"Áreas guardadas en data: {guardadas}"]
        detalle = []
        if nuevas:
            detalle.append(f"nuevas: {nuevas}")
        if actualizadas:
            detalle.append(f"actualizadas: {actualizadas}")
        if sin_cambios:
            detalle.append(f"sin cambios: {sin_cambios}")
        if detalle:
            partes.append("(" + ", ".join(detalle) + ")")
        if sin_area:
            partes.append(f"omitidos sin área: {sin_area}")
        if fallidas:
            partes.append(f"fallidos: {len(fallidas)}")
        mensaje = ". ".join(partes) + f". Archivo: {ruta}"
        self._var_resumen.set(mensaje)

        if fallidas:
            messagebox.showwarning(
                "Guardado parcial en data",
                mensaje + "\n\nRevise los registros no guardados.",
                parent=self,
            )
        else:
            messagebox.showinfo(
                "Áreas guardadas en data",
                mensaje
                + "\n\nEl Excel de tickets no fue generado ni modificado.",
                parent=self,
            )

    def _ver_detalles(self) -> None:
        registro = self._un_registro()
        if registro is None:
            return
        VentanaDetalleRegistro(self, registro)

    # ------------------------------------------------------------------
    # Generación
    # ------------------------------------------------------------------
    def _modo(self) -> str:
        return cfg.MODO_DETALLE if "detalle" in self._var_modo.get().lower() else cfg.MODO_NORMAL

    def _generar_completo(self) -> None:
        if self.revision.cantidad_pendientes:
            messagebox.showerror(
                "Validación incompleta",
                "No se puede generar el Excel completo mientras existan "
                f"{self.revision.cantidad_pendientes} registros pendientes.",
            )
            return
        self._generar(False)

    def _generar_parcial(self) -> None:
        pendientes = self.revision.cantidad_pendientes
        if not pendientes:
            self._generar(False)
            return
        if not messagebox.askyesno(
            "Generar con pendientes",
            f"Existen {pendientes} registros sin área validada.\n\n"
            "Se generará un archivo parcial y quedará registrado qué usuarios "
            "no tienen área. ¿Desea continuar?",
        ):
            return
        self._generar(True)

    def _generar(self, permitir_pendientes: bool) -> None:
        if self._generando:
            return
        modo = self._modo()
        self.revision.generacion = {
            "modo": modo,
            "permitir_pendientes": permitir_pendientes,
            "fecha": svc_revision._ahora(),
            "pendientes": self.revision.cantidad_pendientes,
        }
        self.revision.guardar()
        if self._al_generar is None:
            messagebox.showinfo(
                "Sin generador",
                "La revisión se guardó, pero no hay un generador conectado.",
            )
            return
        self._generando = True
        self._var_resumen.set("Generando el Excel…")
        try:
            resultado = self._al_generar(modo, permitir_pendientes)
        except Exception as exc:  # noqa: BLE001
            self._generando = False
            messagebox.showerror("No se pudo generar", str(exc))
            return
        if resultado is True:
            # Generación asíncrona en curso: la interfaz principal llamará a
            # ``generacion_terminada`` cuando finalice.
            return
        self._generando = False
        if resultado is False:
            self._var_resumen.set(
                "No se inició la generación. Corrija el problema indicado e "
                "intente de nuevo."
            )
            return
        # Compatibilidad con generadores síncronos previos.
        self.destroy()

    def generacion_terminada(self, exito: bool) -> None:
        """La interfaz principal informa el resultado de una generación."""

        self._generando = False
        if exito:
            self.destroy()
            return
        self._var_resumen.set(
            "La generación falló. La revisión sigue disponible para reintentar."
        )

    def _abrir_revision_areas_repetidas(self) -> None:
        """Detecta áreas duplicadas o con nombres similares asignadas en la revisión."""
        registros = self.revision.registros
        conteo_areas: dict[str, list[dict[str, Any]]] = {}
        for r in registros:
            area = str(r.get("area_actual") or "").strip()
            if area:
                conteo_areas.setdefault(area, []).append(r)

        if not conteo_areas:
            messagebox.showinfo(
                "Sin áreas asignadas",
                "Todavía no hay áreas asignadas en esta revisión para analizar.",
                parent=self,
            )
            return

        grupos: list[dict[str, Any]] = []
        areas_procesadas: set[str] = set()
        lista_areas = list(conteo_areas.keys())

        def _son_similares(a: str, b: str) -> float:
            s1 = catalogo_areas.buscar_similares(a, [b], umbral=0.80)
            s2 = catalogo_areas.buscar_similares(b, [a], umbral=0.80)
            puntos = [s.similitud for s in s1 + s2]
            return max(puntos) if puntos else 0.0

        for i, area in enumerate(lista_areas):
            if area in areas_procesadas:
                continue
            variantes = [area]
            clave_norm = normalizar_area(area) or area.casefold()

            for otra in lista_areas[i + 1:]:
                if otra in areas_procesadas:
                    continue
                otra_norm = normalizar_area(otra) or otra.casefold()
                if clave_norm == otra_norm or _son_similares(area, otra) >= 0.80:
                    variantes.append(otra)

            if len(variantes) > 1:
                for v in variantes:
                    areas_procesadas.add(v)
                principal = max(
                    variantes,
                    key=lambda v: (len(conteo_areas.get(v, [])), v.isupper(), len(v)),
                )
                grupos.append({
                    "canonico": principal,
                    "variantes": variantes,
                    "conteos": {v: len(conteo_areas.get(v, [])) for v in variantes},
                    "total": sum(len(conteo_areas.get(v, [])) for v in variantes),
                })

        if not grupos:
            messagebox.showinfo(
                "Áreas unificadas",
                f"¡Excelente! No se encontraron áreas repetidas o con nombres similares.\n"
                f"Se revisaron {len(conteo_areas)} áreas asignadas en esta revisión.",
                parent=self,
            )
            return

        VentanaConsolidarAreas(
            self,
            grupos=grupos,
            al_unificar=self._aplicar_unificacion_areas,
        )

    def _aplicar_unificacion_areas(self, mapeo: dict[str, str]) -> None:
        """Aplica la unificación a los registros de la revisión."""
        mapa_limpio = {k.strip(): v.strip() for k, v in mapeo.items() if k.strip() and v.strip()}
        modificados = 0
        for registro in self.revision.registros:
            area_actual = str(registro.get("area_actual") or "").strip()
            if area_actual in mapa_limpio:
                nueva_area = mapa_limpio[area_actual]
                usuario = str(registro.get("usuario") or "")
                self.revision.actualizar(usuario, area=nueva_area)
                modificados += 1

        if modificados > 0:
            self.revision.guardar()
            self._refrescar()
            messagebox.showinfo(
                "Áreas unificadas",
                f"Se unificaron exitosamente {len(mapa_limpio)} variantes de áreas en {modificados} registros.",
                parent=self,
            )

    def destroy(self) -> None:
        for after_id in (
            getattr(self, "_filtro_after_id", None),
            getattr(self, "_pintado_tabla_after_id", None),
        ):
            if after_id is not None:
                try:
                    self.after_cancel(after_id)
                except (tk.TclError, AttributeError):
                    pass
        self._filtro_after_id = None
        self._pintado_tabla_after_id = None
        super().destroy()

    def _cerrar(self) -> None:
        try:
            self.revision.guardar()
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("No se pudo guardar la revisión al cerrar: %s", exc)
        self.destroy()

    # ------------------------------------------------------------------
    def _ejecutar(self, tarea, al_terminar) -> None:
        def ejecutar():
            try:
                resultado = tarea()
            except Exception as exc:  # noqa: BLE001
                resultado = exc
            try:
                self.after(0, lambda: self._finalizar(al_terminar, resultado))
            except tk.TclError:
                pass

        threading.Thread(target=ejecutar, daemon=True).start()

    def _finalizar(self, al_terminar, resultado) -> None:
        try:
            if self.winfo_exists():
                al_terminar(resultado)
        except tk.TclError:
            pass

    def _areas_disponibles(self) -> list[str]:
        areas: list[str] = []
        try:
            df = self.maestro.como_dataframe()
            if not df.empty:
                for valor in df[cfg.COLUMNA_AREA].tolist():
                    texto = str(valor or "").strip()
                    if texto:
                        areas.append(texto)
        except Exception:  # noqa: BLE001
            pass
        for registro in self.revision.registros:
            texto = str(registro.get("area_actual") or "").strip()
            if texto:
                areas.append(texto)
        return filtrar_areas(areas)


# ---------------------------------------------------------------------------
# Diálogos auxiliares
# ---------------------------------------------------------------------------
class VentanaConsolidarAreas(tk.Toplevel):
    """Permite revisar áreas repetidas o con nombres similares y unificarlas."""

    def __init__(
        self,
        padre: tk.Misc,
        *,
        grupos: list[dict[str, Any]],
        al_unificar: Callable[[dict[str, str]], None],
    ) -> None:
        super().__init__(padre)
        self.title("Revisión y unificación de áreas repetidas")
        self.geometry("740x500")
        self.minsize(620, 380)
        self.configure(bg=FONDO)
        self.transient(padre)
        self.grab_set()

        self._grupos = grupos
        self._al_unificar = al_unificar
        self._controles: list[tuple[dict[str, Any], tk.StringVar]] = []

        encabezado = ttk.Frame(self, padding=(18, 14, 18, 6))
        encabezado.pack(fill="x")
        ttk.Label(
            encabezado,
            text="Áreas repetidas o con nombres similares detectadas",
            style="Seccion.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            encabezado,
            text=(
                "Se detectaron áreas con variaciones de mayúsculas, tildes o abreviaturas similares. "
                "Seleccione el nombre oficial para unificar cada grupo:"
            ),
            style="Subtitulo.TLabel",
            wraplength=680,
        ).pack(anchor="w", pady=(4, 0))

        contenedor = ttk.Frame(self, padding=(18, 6))
        contenedor.pack(fill="both", expand=True)

        canvas = tk.Canvas(contenedor, bg=FONDO, highlightthickness=0)
        scrollbar = ttk.Scrollbar(contenedor, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda _e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        for idx, grupo in enumerate(grupos, start=1):
            cuadro = ttk.LabelFrame(
                scrollable_frame,
                text=f"Grupo {idx}: {grupo['total']} registros afectados",
                padding=10,
            )
            cuadro.pack(fill="x", expand=True, pady=6, padx=4)

            fila_canonico = ttk.Frame(cuadro)
            fila_canonico.pack(fill="x", pady=(0, 6))
            ttk.Label(fila_canonico, text="Nombre que se conservará:").pack(
                side="left", padx=(0, 8)
            )

            var_canonico = tk.StringVar(value=grupo["canonico"])
            combo_canonico = ttk.Combobox(
                fila_canonico,
                textvariable=var_canonico,
                values=grupo["variantes"],
                width=36,
            )
            combo_canonico.pack(side="left", fill="x", expand=True)
            self._controles.append((grupo, var_canonico))

            texto_variantes = "Variantes en revisión: " + " | ".join(
                f"\"{v}\" ({grupo['conteos'].get(v, 0)} reg.)" for v in grupo["variantes"]
            )
            ttk.Label(
                cuadro,
                text=texto_variantes,
                style="Subtitulo.TLabel",
                wraplength=640,
            ).pack(anchor="w")

        botones = ttk.Frame(self, padding=(18, 10, 18, 14))
        botones.pack(fill="x", side="bottom")

        ttk.Button(
            botones,
            text="Unificar y aplicar cambios",
            style="Principal.TButton",
            command=self._guardar,
        ).pack(side="right")
        ttk.Button(botones, text="Cancelar", command=self.destroy).pack(
            side="right", padx=(0, 8)
        )

    def _guardar(self) -> None:
        mapeo: dict[str, str] = {}
        for grupo, var in self._controles:
            canonico = var.get().strip()
            if not canonico:
                continue
            for v in grupo["variantes"]:
                if v != canonico:
                    mapeo[v] = canonico

        if not mapeo:
            messagebox.showinfo(
                "Sin cambios", "Todas las áreas ya tenían el mismo nombre.", parent=self
            )
            self.destroy()
            return

        self._al_unificar(mapeo)
        self.destroy()


class VentanaAsignarArea(tk.Toplevel):
    def __init__(
        self,
        padre: tk.Misc,
        *,
        usuario: str,
        areas: Iterable[str],
        al_guardar: Callable[[str], None],
        area_inicial: str = "",
        etiqueta: str | None = None,
    ) -> None:
        super().__init__(padre)
        self.title(etiqueta or "Asignar o agregar área")
        self.geometry("560x280")
        self.minsize(480, 250)
        self.configure(bg=FONDO)
        self.transient(padre)
        self.grab_set()
        self._al_guardar = al_guardar
        self._areas = filtrar_areas(areas)
        self._sugerencia_actual: str = ""

        ttk.Label(
            self,
            text=etiqueta or f"Usuario: {usuario}",
            style="Seccion.TLabel",
        ).pack(anchor="w", padx=18, pady=(16, 4))
        ttk.Label(
            self,
            text="Escriba para buscar en el catálogo o agregar una nueva área:",
            style="Subtitulo.TLabel",
        ).pack(anchor="w", padx=18)

        self._var_area = tk.StringVar(value=area_inicial)
        combo = ttk.Combobox(
            self,
            textvariable=self._var_area,
            values=self._areas,
            width=50,
        )
        combo.pack(fill="x", padx=18, pady=(8, 4))
        self._combo_area = combo

        fila_sug = ttk.Frame(self)
        fila_sug.pack(fill="x", padx=18, pady=(0, 8))
        self._var_estado_sugerencias = tk.StringVar(value="")
        ttk.Label(
            fila_sug,
            textvariable=self._var_estado_sugerencias,
            style="Subtitulo.TLabel",
        ).pack(side="left")

        self._btn_usar_sug = ttk.Button(
            fila_sug,
            text="Usar sugerencia",
            command=self._aplicar_sugerencia_rapida,
        )

        combo.bind("<KeyRelease>", self._filtrar_sugerencias)
        combo.bind("<<ComboboxSelected>>", self._seleccionar_sugerencia)
        combo.focus_set()

        botones = ttk.Frame(self)
        botones.pack(fill="x", padx=18, pady=(8, 16), side="bottom")
        ttk.Button(
            botones,
            text="Guardar asignación",
            style="Principal.TButton",
            command=self._guardar,
        ).pack(side="right")
        ttk.Button(botones, text="Cancelar", command=self.destroy).pack(
            side="right", padx=(0, 8)
        )
        self._filtrar_sugerencias()

    def _aplicar_sugerencia_rapida(self) -> None:
        if self._sugerencia_actual:
            self._var_area.set(self._sugerencia_actual)
            self._filtrar_sugerencias()

    def _filtrar_sugerencias(self, _evento=None) -> None:
        texto = self._var_area.get().strip()
        opciones = filtrar_areas(self._areas, texto, contiene=True)
        self._combo_area.configure(values=opciones)

        self._sugerencia_actual = ""
        self._btn_usar_sug.pack_forget()

        if not texto:
            self._var_estado_sugerencias.set("Seleccione una opción o escriba un nombre nuevo.")
            return

        match_exacto = next(
            (op for op in self._areas if op.casefold() == texto.casefold()), None
        )
        if match_exacto:
            self._var_estado_sugerencias.set(f"✓ Área existente: '{match_exacto}'")
            return

        similares = catalogo_areas.buscar_similares(texto, self._areas, umbral=0.75)
        if similares:
            mejor = similares[0]
            self._sugerencia_actual = mejor.nombre
            self._var_estado_sugerencias.set(
                f"Parecida a: '{mejor.nombre}' ({mejor.similitud:.0%})"
            )
            self._btn_usar_sug.pack(side="right")
            return

        self._var_estado_sugerencias.set("+ Se registrará como NUEVA área en el catálogo.")

    def _seleccionar_sugerencia(self, _evento=None) -> None:
        self._filtrar_sugerencias()

    def _guardar(self) -> None:
        area = self._var_area.get().strip()
        if not area:
            messagebox.showwarning("Área vacía", "Indique un área válida.", parent=self)
            return

        match_exacto = next(
            (op for op in self._areas if op.casefold() == area.casefold()), None
        )
        if match_exacto:
            area_final = match_exacto
        elif self._sugerencia_actual:
            resp = messagebox.askyesnocancel(
                "Área similar detectada",
                f"Escribió '{area}', pero ya existe '{self._sugerencia_actual}'.\n\n"
                f"¿Desea usar '{self._sugerencia_actual}' en su lugar?\n\n"
                f"• Sí: Usa '{self._sugerencia_actual}' (recomendado).\n"
                f"• No: Crea '{area}' como una nueva área.\n"
                f"• Cancelar: Vuelve a editar.",
                parent=self,
            )
            if resp is True:
                area_final = self._sugerencia_actual
            elif resp is False:
                area_final = area
            else:
                return
        else:
            if not messagebox.askyesno(
                "Registrar nueva área",
                f"El área '{area}' no existe en el catálogo.\n\n"
                f"¿Confirma que desea crearla y registrarla?",
                parent=self,
            ):
                return
            area_final = area

        self._al_guardar(area_final)
        self.destroy()


class VentanaBuscarExcelMaestro(tk.Toplevel):
    """Busca nombres y sugiere áreas existentes en el Excel maestro."""

    def __init__(
        self,
        padre: VentanaValidacion,
        *,
        usuario: str,
        maestro: Maestro,
        al_elegir: Callable[[dict[str, Any]], bool],
    ) -> None:
        super().__init__(padre)
        self.title("Buscar usuario en Excel maestro")
        self.geometry("760x500")
        self.minsize(620, 380)
        self.configure(bg=FONDO)
        self.transient(padre)
        self.grab_set()
        self._maestro = maestro
        self._al_elegir = al_elegir
        self._candidatos: list[dict[str, Any]] = []
        self._after_busqueda: str | None = None
        self._var_nombre = tk.StringVar(value=usuario)
        self._var_estado = tk.StringVar(value="Escriba el nombre para ver sugerencias.")

        ttk.Label(
            self, text="Buscar usuario en el Excel maestro", style="Seccion.TLabel"
        ).pack(anchor="w", padx=18, pady=(14, 4))
        ttk.Label(
            self,
            text=f"Usuario pendiente: {usuario}",
            style="Subtitulo.TLabel",
        ).pack(anchor="w", padx=18, pady=(0, 8))
        fila = ttk.Frame(self)
        fila.pack(fill="x", padx=18, pady=(0, 8))
        ttk.Label(fila, text="Nombre a buscar:").pack(side="left", padx=(0, 8))
        entrada = ttk.Entry(fila, textvariable=self._var_nombre)
        entrada.pack(side="left", fill="x", expand=True)
        entrada.bind("<KeyRelease>", self._programar_busqueda)
        entrada.bind("<Return>", lambda _evento: self._buscar())
        ttk.Button(fila, text="Buscar", command=self._buscar).pack(side="left", padx=(8, 0))

        contenedor = ttk.Frame(self)
        contenedor.pack(fill="both", expand=True, padx=18, pady=(0, 6))
        self._tabla = ttk.Treeview(
            contenedor,
            columns=("usuario", "area", "coincidencia"),
            show="headings",
            selectmode="browse",
        )
        for columna, titulo, ancho in (
            ("usuario", "USUARIO EN MAESTRO", 360),
            ("area", "ÁREA ASIGNADA", 250),
            ("coincidencia", "SIMILITUD", 100),
        ):
            self._tabla.heading(columna, text=titulo)
            self._tabla.column(columna, width=ancho, anchor="w")
        barra = ttk.Scrollbar(contenedor, orient="vertical", command=self._tabla.yview)
        self._tabla.configure(yscrollcommand=barra.set)
        self._tabla.pack(side="left", fill="both", expand=True)
        barra.pack(side="right", fill="y")
        self._tabla.bind("<Double-1>", lambda _evento: self._elegir())
        ttk.Label(self, textvariable=self._var_estado, style="Subtitulo.TLabel").pack(
            anchor="w", padx=18, pady=(0, 8)
        )
        botones = ttk.Frame(self)
        botones.pack(fill="x", padx=18, pady=(0, 14))
        self._btn_elegir = ttk.Button(
            botones,
            text="Asignar área seleccionada",
            style="Principal.TButton",
            command=self._elegir,
            state="disabled",
        )
        self._btn_elegir.pack(side="right")
        ttk.Button(botones, text="Cancelar", command=self.destroy).pack(
            side="right", padx=(0, 8)
        )
        self._tabla.bind("<<TreeviewSelect>>", self._actualizar_boton)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._buscar()
        entrada.focus_set()
        entrada.selection_range(0, tk.END)

    def _programar_busqueda(self, _evento=None) -> None:
        if self._after_busqueda:
            try:
                self.after_cancel(self._after_busqueda)
            except tk.TclError:
                pass
        self._after_busqueda = self.after(180, self._buscar)

    def _buscar(self) -> None:
        self._after_busqueda = None
        consulta = self._var_nombre.get().strip()
        self._tabla.delete(*self._tabla.get_children())
        self._candidatos = []
        try:
            from core import similitud
            from database.normalizador import normalizar_nombre

            df = self._maestro.como_dataframe()
            clave_consulta = normalizar_nombre(consulta)
            filas: list[tuple[float, dict[str, Any]]] = []
            for _, fila in df.iterrows():
                nombre = str(fila.get(cfg.COLUMNA_USUARIO) or "").strip()
                area = str(fila.get(cfg.COLUMNA_AREA) or "").strip()
                if not nombre or not area:
                    continue
                clave = normalizar_nombre(nombre)
                if not consulta:
                    puntaje = 0.0
                elif clave == clave_consulta:
                    puntaje = 1.0
                elif clave_consulta and clave_consulta in clave:
                    puntaje = 0.96
                elif clave and clave in clave_consulta:
                    puntaje = 0.92
                else:
                    puntaje = similitud.comparar(consulta, nombre)
                if not consulta or puntaje >= 0.28:
                    filas.append((puntaje, {
                        "origen": "MAESTRO",
                        "empresa": "MAESTRO",
                        "fuente": "MAESTRO",
                        "nombre": nombre,
                        "area": area,
                        "AREA": area,
                        "AREA_MAESTRO": area,
                        "activo": None,
                        "tipo": "exacta" if puntaje == 1.0 else "aproximada",
                        "similitud": puntaje,
                    }))
            filas.sort(key=lambda item: (item[0], item[1]["nombre"].casefold()), reverse=True)
            self._candidatos = [candidato for _, candidato in filas[:200]]
            for indice, candidato in enumerate(self._candidatos):
                self._tabla.insert(
                    "", "end", iid=str(indice),
                    values=(
                        candidato["nombre"], candidato["area"],
                        f"{candidato['similitud']:.0%}" if consulta else "—",
                    ),
                )
            self._var_estado.set(
                f"{len(self._candidatos)} sugerencia(s). Seleccione el usuario correcto."
                if self._candidatos
                else "No se encontraron sugerencias. Pruebe con otro nombre."
            )
        except Exception as exc:  # noqa: BLE001
            self._var_estado.set(f"No se pudo leer el Excel maestro: {exc}")
        self._actualizar_boton()

    def _actualizar_boton(self, _evento=None) -> None:
        self._btn_elegir.configure(
            state="normal" if self._tabla.selection() else "disabled"
        )

    def _elegir(self) -> None:
        seleccion = self._tabla.selection()
        if not seleccion:
            return
        candidato = self._candidatos[int(seleccion[0])]
        try:
            asignada = self._al_elegir(dict(candidato))
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("No se pudo asignar el área", str(exc), parent=self)
            return
        if asignada:
            self.destroy()


class VentanaOrdenTerminos(tk.Toplevel):
    """Organiza el orden de búsqueda cuando el solicitante tiene 3+ palabras.

    No modifica el valor del Excel: solo produce el orden de términos que se
    usará para construir el patrón ``LIKE`` de la consulta.
    """

    def __init__(self, padre: tk.Misc, plan: busqueda_core.PlanBusqueda) -> None:
        super().__init__(padre)
        self.title("Ordenar términos de búsqueda")
        self.geometry("660x510")
        self.minsize(560, 470)
        self.configure(bg=FONDO)
        self.transient(padre)
        self.grab_set()

        self._plan = plan
        self._terminos = list(plan.terminos)
        self._originales = list(plan.terminos_originales or plan.terminos)
        self._habilitados = set(self._originales)
        self._resultado: list[str] | None = None
        self._var_patron = tk.StringVar()

        ttk.Label(
            self, text="Orden de búsqueda", style="Seccion.TLabel"
        ).pack(anchor="w", padx=18, pady=(14, 4))
        ttk.Label(
            self,
            text=f"Solicitante original: {plan.solicitante or '—'}",
            style="Subtitulo.TLabel",
            wraplength=610,
            justify="left",
        ).pack(anchor="w", padx=18)
        ttk.Label(
            self,
            text=(
                "Ordene con ↑ / ↓. Seleccione un término y use Incluir / "
                "excluir para cambiar si participa en la búsqueda."
            ),
            style="Subtitulo.TLabel",
            wraplength=610,
            justify="left",
        ).pack(anchor="w", padx=18, pady=(2, 8))

        marco_lista = ttk.Frame(self)
        marco_lista.pack(fill="both", expand=True, padx=18, pady=(0, 8))
        self._lista = tk.Listbox(
            marco_lista,
            height=6,
            exportselection=False,
            bg=SUPERFICIE,
            fg=TEXTO,
            selectbackground=AZUL_SUAVE,
            selectforeground=TEXTO,
            font=(ui_tema.FUENTE, 11),
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDE,
            activestyle="none",
        )
        barra = ttk.Scrollbar(
            marco_lista, orient="vertical", command=self._lista.yview
        )
        self._lista.configure(yscrollcommand=barra.set)
        self._lista.pack(side="left", fill="both", expand=True)
        barra.pack(side="right", fill="y")
        self._lista.bind("<Double-1>", self._subir)

        controles = ttk.Frame(self)
        controles.pack(fill="x", padx=18, pady=(0, 8))
        ttk.Button(controles, text="↑ Subir", command=self._subir).pack(
            side="left"
        )
        ttk.Button(controles, text="↓ Bajar", command=self._bajar).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(
            controles,
            text="☑ Incluir / excluir término",
            command=self._alternar_termino,
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            controles,
            text="Restaurar orden original",
            command=self._restaurar,
        ).pack(side="left", padx=(6, 0))

        vista = ttk.LabelFrame(
            self, text="Vista previa del patrón SQL", padding=8
        )
        vista.pack(fill="x", padx=18, pady=(0, 8))
        ttk.Label(
            vista,
            textvariable=self._var_patron,
            font=(ui_tema.FUENTE_MONO, 11, "bold"),
            foreground=AZUL_PRINCIPAL,
            wraplength=590,
            justify="left",
        ).pack(anchor="w", padx=6, pady=4)
        ttk.Label(
            vista,
            text=(
                "Los términos se envían como parámetro y los comodines se "
                "escapan. El valor del solicitante en el Excel no se modifica."
            ),
            style="TarjetaSecundaria.TLabel",
            wraplength=590,
            justify="left",
        ).pack(anchor="w", padx=6)

        botones = ttk.Frame(self)
        botones.pack(fill="x", padx=18, pady=(0, 14))
        ttk.Button(
            botones,
            text="CONFIRMAR BÚSQUEDA",
            style="Principal.TButton",
            command=self._confirmar,
        ).pack(side="right")
        ttk.Button(botones, text="CANCELAR", command=self._cancelar).pack(
            side="right", padx=(0, 8)
        )

        self._actualizar()
        self.protocol("WM_DELETE_WINDOW", self._cancelar)
        self.bind("<Escape>", lambda _e: self._cancelar())
        self._lista.focus_set()

    @classmethod
    def pedir(
        cls, padre: tk.Misc, plan: busqueda_core.PlanBusqueda
    ) -> list[str] | None:
        """Muestra el organizador y devuelve el orden confirmado o ``None``."""

        try:
            ventana = cls(padre, plan)
        except tk.TclError:  # pragma: no cover - sin entorno gráfico
            return None
        try:
            padre.wait_window(ventana)
        except tk.TclError:  # pragma: no cover
            return None
        return ventana._resultado

    # ------------------------------------------------------------------
    def _seleccion(self) -> int | None:
        seleccion = self._lista.curselection()
        return int(seleccion[0]) if seleccion else None

    def _mover(self, origen: int, destino: int) -> None:
        if destino < 0 or destino >= len(self._terminos):
            return
        self._terminos[origen], self._terminos[destino] = (
            self._terminos[destino],
            self._terminos[origen],
        )
        self._actualizar()
        self._lista.selection_clear(0, "end")
        self._lista.selection_set(destino)
        self._lista.activate(destino)

    def _subir(self, _evento=None) -> None:
        indice = self._seleccion()
        if indice is None:
            if self._terminos:
                self._lista.selection_set(0)
            return
        self._mover(indice, indice - 1)

    def _bajar(self) -> None:
        indice = self._seleccion()
        if indice is None:
            return
        self._mover(indice, indice + 1)

    def _restaurar(self) -> None:
        self._terminos = list(self._originales)
        self._habilitados = set(self._originales)
        self._actualizar()

    def _alternar_termino(self) -> None:
        indice = self._seleccion()
        if indice is None or not self._terminos:
            return
        termino = self._terminos[indice]
        if termino in self._habilitados:
            self._habilitados.remove(termino)
        else:
            self._habilitados.add(termino)
        self._actualizar()
        self._lista.selection_set(indice)
        self._lista.activate(indice)

    def _actualizar(self) -> None:
        self._lista.delete(0, "end")
        for termino in self._terminos:
            marca = "☑" if termino in self._habilitados else "☐"
            self._lista.insert("end", f"{marca}  {termino}")
        seleccionados = [
            termino for termino in self._terminos if termino in self._habilitados
        ]
        self._var_patron.set(busqueda_core.patron_visible(seleccionados))

    def _confirmar(self) -> None:
        self._resultado = [
            termino for termino in self._terminos if termino in self._habilitados
        ]
        if not self._resultado:
            messagebox.showwarning(
                "Sin términos", "Incluya al menos un término para buscar."
            )
            return
        self.destroy()

    def _cancelar(self) -> None:
        self._resultado = None
        self.destroy()


class VentanaSeguimientoConsulta(tk.Toplevel):
    """Seguimiento visual y no bloqueante de la consulta SQL por empresa.

    El repositorio entrega eventos desde un hilo trabajador mediante
    ``encolar``. Este método solo escribe en una cola segura; los widgets se
    actualizan exclusivamente en el hilo principal con ``after``.
    """

    _COLUMNAS = (
        ("empresa", "Empresa", 120),
        ("estado", "Estado", 175),
        ("resultados", "Resultados", 95),
        ("tiempo", "Tiempo", 100),
        ("detalle", "Detalle", 380),
    )

    _TAG_POR_ESTADO = {
        seguimiento.ESTADO_PENDIENTE: "pendiente",
        seguimiento.ESTADO_CONSULTANDO: "consultando",
        seguimiento.ESTADO_COMPLETADA: "completada",
        seguimiento.ESTADO_SIN_RESULTADOS: "sin_resultados",
        seguimiento.ESTADO_SIN_ESQUEMA: "sin_esquema",
        seguimiento.ESTADO_ERROR_CONEXION: "error",
        seguimiento.ESTADO_ERROR_PERMISOS: "error",
        seguimiento.ESTADO_ERROR_CONSULTA: "error",
        seguimiento.ESTADO_CANCELADA: "cancelada",
    }

    _ESTILO_ESTADO = {
        seguimiento.ESTADO_COMPLETADA: "success",
        seguimiento.ESTADO_SIN_RESULTADOS: "info",
        seguimiento.ESTADO_SIN_ESQUEMA: "warning",
        seguimiento.ESTADO_ERROR_CONEXION: "danger",
        seguimiento.ESTADO_ERROR_PERMISOS: "danger",
        seguimiento.ESTADO_ERROR_CONSULTA: "danger",
        seguimiento.ESTADO_CANCELADA: "warning",
    }

    def __init__(
        self,
        padre: tk.Misc,
        *,
        solicitantes: Iterable[str],
        ordenes: Iterable[str],
        empresas: Iterable[str],
    ) -> None:
        super().__init__(padre)
        self.title("Seguimiento de consultas SQL")
        self.geometry("1040x660")
        self.minsize(820, 520)
        self.configure(bg=FONDO)
        self.transient(padre)

        self._empresas = list(
            dict.fromkeys(
                str(empresa).strip()
                for empresa in empresas
                if str(empresa or "").strip()
            )
        )
        self._resumen = seguimiento.ResumenSeguimiento(
            total_empresas=len(self._empresas)
        )
        self._cola: queue.Queue[dict[str, Any]] = queue.Queue()
        self._iids: dict[str, str] = {}
        self._corriendo = True
        self._cancelar_solicitado = False
        self._after_id: str | None = None

        lista_solicitantes = [
            str(item).strip() for item in solicitantes if str(item or "").strip()
        ]
        lista_ordenes = [str(item).strip() for item in ordenes if str(item or "").strip()]
        self._var_solicitantes = tk.StringVar(
            value=self._resumir_lista(lista_solicitantes, "Solicitantes")
        )
        self._var_orden = tk.StringVar(value=self._resumir_lista(lista_ordenes, "Orden"))
        self._var_estado = tk.StringVar(value="Consultando empresas…")
        self._var_total = tk.StringVar(value=str(len(self._empresas)))
        self._var_procesadas = tk.StringVar(value="0")
        self._var_pendientes = tk.StringVar(value=str(len(self._empresas)))
        self._var_porcentaje = tk.StringVar(value="0%")
        self._var_resumen = tk.StringVar(value="Preparando la consulta…")

        self._construir_cabecera()
        self._construir_progreso()
        self._construir_tabla()
        self._construir_resumen()
        self._construir_botones()

        self.protocol("WM_DELETE_WINDOW", self._cerrar)
        self.bind("<Escape>", lambda _e: self._cerrar())
        self._after_id = self.after(120, self._procesar_cola)

    # ------------------------------------------------------------------
    # Construcción visual
    # ------------------------------------------------------------------
    @staticmethod
    def _resumir_lista(items: list[str], etiqueta: str) -> str:
        if not items:
            return f"{etiqueta}: —"
        if len(items) <= 3:
            return f"{etiqueta}: " + " · ".join(items)
        return f"{etiqueta}: " + " · ".join(items[:3]) + f" (+{len(items) - 3})"

    def _construir_cabecera(self) -> None:
        encabezado = EncabezadoDegradado(self, alto=88)
        encabezado.pack(fill="x")
        contenido = encabezado.contenido
        contenido.configure(bg=AZUL_MARINO)
        ttk.Label(
            contenido,
            text="SEGUIMIENTO DE CONSULTAS SQL",
            style="Cabecera.TLabel",
        ).pack(anchor="w", padx=18, pady=(12, 0))
        ttk.Label(
            contenido,
            textvariable=self._var_solicitantes,
            style="CabeceraSecundaria.TLabel",
            wraplength=980,
            justify="left",
        ).pack(anchor="w", padx=18)
        ttk.Label(
            contenido,
            textvariable=self._var_orden,
            style="CabeceraSecundaria.TLabel",
            wraplength=980,
            justify="left",
        ).pack(anchor="w", padx=18, pady=(0, 10))

    def _construir_progreso(self) -> None:
        marco = ttk.Frame(self)
        marco.pack(fill="x", padx=18, pady=(12, 4))

        fila_estado = ttk.Frame(marco)
        fila_estado.pack(fill="x")
        ttk.Label(
            fila_estado, textvariable=self._var_estado, style="Seccion.TLabel"
        ).pack(side="left")
        self._pill_estado = Pill(fila_estado, "En curso", estilo="info", punto=True)
        self._pill_estado.pack(side="right")
        indicadores = ttk.Frame(marco)
        indicadores.pack(fill="x", pady=(4, 0))
        for etiqueta, variable in (
            ("Empresas", self._var_total),
            ("Procesadas", self._var_procesadas),
            ("Pendientes", self._var_pendientes),
            ("Progreso", self._var_porcentaje),
        ):
            bloque = ttk.Frame(indicadores)
            bloque.pack(side="left", padx=(0, 24))
            ttk.Label(bloque, text=etiqueta, style="Clave.TLabel").pack(anchor="w")
            ttk.Label(
                bloque,
                textvariable=variable,
                font=(ui_tema.FUENTE, 14, "bold"),
            ).pack(anchor="w")

        self._progreso = ttk.Progressbar(
            marco, orient="horizontal", mode="determinate", maximum=100
        )
        self._progreso.pack(fill="x", pady=(6, 0))

    def _construir_tabla(self) -> None:
        marco = ttk.LabelFrame(self, text="Empresas consultadas", padding=6)
        marco.pack(fill="both", expand=True, padx=18, pady=(4, 6))
        columnas = tuple(columna for columna, _titulo, _ancho in self._COLUMNAS)
        self._tabla = ttk.Treeview(
            marco, columns=columnas, show="headings", selectmode="browse"
        )
        for columna, titulo, ancho in self._COLUMNAS:
            self._tabla.heading(columna, text=titulo)
            self._tabla.column(
                columna,
                width=ancho,
                anchor="w",
                stretch=columna == "detalle",
            )
        barra_y = ttk.Scrollbar(marco, orient="vertical", command=self._tabla.yview)
        barra_x = ttk.Scrollbar(marco, orient="horizontal", command=self._tabla.xview)
        self._tabla.configure(
            yscrollcommand=barra_y.set, xscrollcommand=barra_x.set
        )
        self._tabla.grid(row=0, column=0, sticky="nsew")
        barra_y.grid(row=0, column=1, sticky="ns")
        barra_x.grid(row=1, column=0, sticky="ew")
        marco.rowconfigure(0, weight=1)
        marco.columnconfigure(0, weight=1)

        self._tabla.tag_configure("pendiente", background="#F1F5F9")
        self._tabla.tag_configure("consultando", background="#EAF2FF")
        self._tabla.tag_configure("completada", background="#E7F7F0")
        self._tabla.tag_configure("sin_resultados", background="#F4F7FC")
        self._tabla.tag_configure("sin_esquema", background="#FEF3E2")
        self._tabla.tag_configure("error", background="#FDECEC")
        self._tabla.tag_configure("cancelada", background="#FEF3E2")

        for empresa in self._empresas:
            iid = f"e{len(self._iids)}"
            self._iids[empresa] = iid
            self._tabla.insert(
                "",
                "end",
                iid=iid,
                values=(
                    empresa,
                    seguimiento.etiqueta(seguimiento.ESTADO_PENDIENTE),
                    "—",
                    "—",
                    "En espera",
                ),
                tags=("pendiente",),
            )
        # La búsqueda primaria es una sola sentencia UNION ALL. Se muestra
        # separada para distinguirla del enriquecimiento de cada empresa.
        self._iids["UNION ALL"] = "busqueda_consolidada"
        self._tabla.insert(
            "", 0, iid="busqueda_consolidada",
            values=(
                "BÚSQUEDA UNION ALL",
                seguimiento.etiqueta(seguimiento.ESTADO_PENDIENTE),
                "—", "—", "Esperando la búsqueda principal",
            ),
            tags=("pendiente",),
        )

    def _construir_resumen(self) -> None:
        marco = ttk.LabelFrame(self, text="Resumen", padding=6)
        marco.pack(fill="x", padx=18, pady=(0, 6))
        ttk.Label(
            marco,
            textvariable=self._var_resumen,
            style="TarjetaSecundaria.TLabel",
            justify="left",
            anchor="w",
            wraplength=960,
        ).pack(fill="x")

    def _construir_botones(self) -> None:
        botones = ttk.Frame(self)
        botones.pack(fill="x", padx=18, pady=(0, 14))
        self._btn_cerrar = ttk.Button(
            botones, text="Cerrar", command=self._cerrar
        )
        self._btn_cerrar.pack(side="right")
        self._btn_cancelar = ttk.Button(
            botones,
            text="Cancelar consulta",
            style="Naranja.TButton",
            command=self._solicitar_cancelacion,
        )
        self._btn_cancelar.pack(side="right", padx=(0, 8))

    # ------------------------------------------------------------------
    # Contrato con el repositorio (hilo trabajador)
    # ------------------------------------------------------------------
    def encolar(self, evento: dict[str, Any]) -> None:
        """Recibe un evento desde el hilo de consulta sin tocar Tkinter."""

        try:
            self._cola.put(dict(evento))
        except Exception:  # noqa: BLE001 - nunca tumbar la consulta
            LOGGER.debug("Evento de seguimiento descartado", exc_info=True)

    @property
    def debe_cancelar(self) -> bool:
        return bool(self._cancelar_solicitado)

    # ------------------------------------------------------------------
    # Actualización en el hilo principal
    # ------------------------------------------------------------------
    def _procesar_cola(self) -> None:
        self._drenar_cola()
        if not self._corriendo:
            return
        try:
            self._after_id = self.after(120, self._procesar_cola)
        except tk.TclError:  # pragma: no cover - ventana destruida
            self._after_id = None

    def _drenar_cola(self) -> None:
        while True:
            try:
                evento = self._cola.get_nowait()
            except queue.Empty:
                break
            self._registrar_evento(evento)

    def _registrar_evento(self, evento: dict[str, Any]) -> None:
        empresa = str(evento.get("empresa") or "")
        if not empresa:
            return
        if empresa == "UNION ALL":
            combinado = evento
        else:
            combinado = self._resumen.registrar(evento)
        iid = self._iids.get(empresa)
        if iid is None:
            return
        estado = str(combinado.get("estado") or seguimiento.ESTADO_PENDIENTE)
        resultados = combinado.get("resultados")
        duracion = combinado.get("duracion")
        detalle = str(combinado.get("detalle") or "")
        self._tabla.item(
            iid,
            values=(
                "BÚSQUEDA UNION ALL" if empresa == "UNION ALL" else empresa,
                seguimiento.etiqueta(estado),
                "—" if resultados is None else str(resultados),
                self._formato_tiempo(duracion),
                detalle or "—",
            ),
            tags=(self._TAG_POR_ESTADO.get(estado, "pendiente"),),
        )
        if empresa != "UNION ALL":
            self._actualizar_progreso()

    def _actualizar_progreso(self) -> None:
        datos = self._resumen.como_dict()
        self._var_total.set(str(datos["total_empresas"]))
        self._var_procesadas.set(str(datos["procesadas"]))
        self._var_pendientes.set(str(datos["pendientes"]))
        self._var_porcentaje.set(f"{datos['porcentaje']}%")
        self._progreso["value"] = datos["porcentaje"]
        self._var_resumen.set(self._texto_resumen(datos))

    @staticmethod
    def _formato_tiempo(valor: Any) -> str:
        if valor is None:
            return "—"
        try:
            segundos = float(valor)
        except (TypeError, ValueError):
            return "—"
        if segundos < 60:
            return f"{segundos:.2f} s"
        minutos = int(segundos // 60)
        return f"{minutos} min {segundos % 60:.1f} s"

    @staticmethod
    def _texto_resumen(datos: dict[str, Any]) -> str:
        lineas = [
            (
                f"Empresas: {datos['total_empresas']} · "
                f"Procesadas: {datos['procesadas']} · "
                f"Pendientes: {datos['pendientes']}"
            ),
            (
                f"Con resultados: {datos['con_resultados']} · "
                f"Sin resultados: {datos['sin_resultados']} · "
                f"Sin esquema: {datos['sin_esquema']}"
            ),
            (
                f"Errores: {datos['errores']} · "
                f"Canceladas: {datos['canceladas']} · "
                f"Empleados encontrados: {datos['empleados']}"
            ),
            f"Duración total: {VentanaSeguimientoConsulta._formato_tiempo(datos['duracion_total'])}",
        ]
        mensaje = str(datos.get("mensaje") or "")
        if mensaje:
            lineas.append(mensaje)
        return "\n".join(lineas)

    # ------------------------------------------------------------------
    # Finalización y cancelación
    # ------------------------------------------------------------------
    def finalizar(
        self,
        *,
        resumen: dict[str, Any] | None = None,
        mensaje: str = "",
    ) -> None:
        """Cierra el seguimiento con datos reales; la ventana queda visible."""

        self._drenar_cola()
        self._corriendo = False
        if isinstance(resumen, dict):
            try:
                if resumen.get("duracion_total") is not None:
                    self._resumen.duracion_total = float(
                        resumen.get("duracion_total")
                    )
            except (TypeError, ValueError):
                pass
            if resumen.get("cancelada"):
                self._resumen.cancelada = True
        self._resumen.mensaje = str(mensaje or "")
        datos = self._resumen.como_dict()
        self._var_resumen.set(self._texto_resumen(datos))
        self._progreso["value"] = datos["porcentaje"]
        self._var_procesadas.set(str(datos["procesadas"]))
        self._var_pendientes.set(str(datos["pendientes"]))
        self._var_porcentaje.set(f"{datos['porcentaje']}%")

        if datos["cancelada"]:
            estado = "Consulta cancelada"
            estilo = "warning"
        elif datos["errores"]:
            estado = "Consulta finalizada con errores"
            estilo = "danger"
        else:
            estado = "Consulta finalizada"
            estilo = "success"
        self._var_estado.set(estado)
        self._pill_estado.actualizar(estado, estilo)
        self._btn_cancelar.configure(state="disabled")
        self._btn_cerrar.configure(text="Cerrar")

    def _solicitar_cancelacion(self) -> None:
        if not self._corriendo or self._cancelar_solicitado:
            return
        if not messagebox.askyesno(
            "Cancelar consulta",
            "La consulta se detendrá al terminar la empresa actual. "
            "Se conservarán los resultados ya obtenidos.\n\n"
            "¿Desea cancelar?",
        ):
            return
        self._cancelar_solicitado = True
        self._btn_cancelar.configure(state="disabled")
        self._var_estado.set("Cancelando: esperando el cierre de la empresa actual…")

    def cerrar_tras_asignacion(self) -> None:
        """Libera solo esta ventana cuando la asignación ya fue guardada."""

        if getattr(self, "_cerrada_por_asignacion", False):
            return
        self._cerrada_por_asignacion = True
        self._corriendo = False
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:  # pragma: no cover - ventana destruida
                pass
            self._after_id = None
        self.destroy()

    def destroy(self) -> None:
        for after_id in (
            getattr(self, "_filtro_after_id", None),
            getattr(self, "_pintado_tabla_after_id", None),
        ):
            if after_id is not None:
                try:
                    self.after_cancel(after_id)
                except (tk.TclError, AttributeError):
                    pass
        self._filtro_after_id = None
        self._pintado_tabla_after_id = None
        super().destroy()

    def _cerrar(self) -> None:
        if self._corriendo and not messagebox.askyesno(
            "Cerrar seguimiento",
            "La consulta continuará en segundo plano y los resultados se "
            "aplicarán al finalizar.\n\n¿Desea cerrar esta ventana?",
        ):
            return
        self._corriendo = False
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:  # pragma: no cover
                pass
            self._after_id = None
        self.destroy()


class VentanaConsultaSqlManual(tk.Toplevel):
    """Búsqueda guiada manual de empleados y departamentos autorizados."""

    def __init__(
        self,
        padre: VentanaValidacion,
        *,
        usuario: str,
        al_elegir: Callable[[dict[str, Any]], None],
    ) -> None:
        super().__init__(padre)
        self.title("Consulta SQL manual de solo lectura")
        self.geometry("1000x700")
        self.minsize(760, 520)
        self.configure(bg=FONDO)
        self.transient(padre)
        self._padre = padre
        self._usuario = usuario
        self._al_elegir = al_elegir
        self._filas: list[dict[str, Any]] = []
        self._tipo_resultado = "empleado"
        self._consultando = False
        self._var_nombre = tk.StringVar(value=usuario)
        self._var_codigo_depto = tk.StringVar()
        self._var_empresa = tk.StringVar(value="ANDINA")
        self._var_estado = tk.StringVar(value="Ingrese el nombre y elija el origen de búsqueda.")

        ttk.Label(
            self,
            text="CONSULTA MANUAL",
            style="Seccion.TLabel",
        ).pack(anchor="w", padx=16, pady=(12, 4))
        self._seccion(self, "Búsqueda de empleados (18 empresas)", "#DCEBFF")
        fila_nombre = ttk.Frame(self)
        fila_nombre.pack(fill="x", padx=20, pady=(8, 4))
        ttk.Label(fila_nombre, text="Nombre del empleado:").pack(side="left", padx=(0, 12))
        entrada = ttk.Entry(fila_nombre, textvariable=self._var_nombre)
        entrada.pack(side="left", fill="x", expand=True)
        entrada.bind("<Return>", lambda _evento: self._buscar_todas())
        botones_busqueda = ttk.Frame(self)
        botones_busqueda.pack(fill="x", padx=20, pady=(4, 12))
        self._btn_buscar_todas = ttk.Button(
            botones_busqueda, text="Buscar en las 18 empresas",
            style="Principal.TButton", command=self._buscar_todas,
        )
        self._btn_buscar_todas.pack(side="left", expand=True, fill="x", padx=(0, 6))
        self._btn_buscar_serviam = ttk.Button(
            botones_busqueda, text="Buscar en SERVIAM",
            command=self._buscar_serviam,
        )
        self._btn_buscar_serviam.pack(side="left", expand=True, fill="x", padx=(6, 0))

        self._seccion(self, "Búsqueda de departamentos", "#E2F3E8")
        fila_depto = ttk.Frame(self)
        fila_depto.pack(fill="x", padx=20, pady=(8, 4))
        ttk.Label(fila_depto, text="Empresa:").pack(side="left", padx=(0, 6))
        ttk.Combobox(
            fila_depto, textvariable=self._var_empresa,
            values=list(EMPRESAS),
            state="readonly", width=18,
        ).pack(side="left", padx=(0, 16))
        ttk.Label(fila_depto, text="Código de departamento:").pack(side="left", padx=(0, 6))
        ttk.Entry(fila_depto, textvariable=self._var_codigo_depto, width=20).pack(side="left")
        self._btn_buscar_departamento = ttk.Button(
            fila_depto, text="Buscar departamento", command=self._buscar_departamento,
        )
        self._btn_buscar_departamento.pack(side="left", padx=(12, 0))
        ttk.Label(self, textvariable=self._var_estado, style="Subtitulo.TLabel").pack(
            anchor="w", padx=20, pady=(0, 6)
        )
        contenedor = ttk.Frame(self)
        contenedor.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        self._tabla = ttk.Treeview(contenedor, show="headings", selectmode="browse")
        self._tabla.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(contenedor, orient="vertical", command=self._tabla.yview)
        scroll.pack(side="right", fill="y")
        self._tabla.configure(yscrollcommand=scroll.set)
        botones = ttk.Frame(self)
        botones.pack(fill="x", padx=16, pady=(0, 12))
        self._btn_usar = ttk.Button(
            botones, text="ACEPTAR EMPLEADO SELECCIONADO",
            command=self._seleccionar, state="disabled",
        )
        self._btn_usar.pack(side="right")
        ttk.Button(botones, text="CANCELAR", command=self.destroy).pack(
            side="right", padx=(0, 8)
        )
        self._tabla.bind("<<TreeviewSelect>>", self._actualizar_seleccion)

    @staticmethod
    def _seccion(padre: tk.Misc, texto: str, color: str) -> None:
        etiqueta = tk.Label(
            padre, text=f"  {texto}", anchor="w", bg=color, fg=TEXTO,
            font=(ui_tema.FUENTE, 11, "bold"), padx=8, pady=8,
        )
        etiqueta.pack(fill="x", padx=16, pady=(4, 0))

    def _buscar_todas(self) -> None:
        nombre = self._var_nombre.get().strip()
        if not nombre:
            messagebox.showinfo("Nombre requerido", "Ingrese el nombre del empleado.", parent=self)
            return
        self._consultar("empleado", lambda repo: repo.buscar_por_nombres([nombre]))

    def _buscar_serviam(self) -> None:
        nombre = self._var_nombre.get().strip()
        if not nombre:
            messagebox.showinfo("Nombre requerido", "Ingrese el nombre del empleado.", parent=self)
            return
        self._consultar("empleado", lambda repo: repo.buscar_serviam_manual(nombre))

    def _buscar_departamento(self) -> None:
        empresa = self._var_empresa.get().strip().upper()
        codigo = self._var_codigo_depto.get().strip()
        if empresa not in EMPRESAS:
            messagebox.showwarning("Empresa no válida", "Elija una empresa de la lista.", parent=self)
            return
        if not codigo:
            messagebox.showinfo("Código requerido", "Ingrese el código del departamento.", parent=self)
            return
        self._consultar(
            "departamento",
            lambda repo: repo.buscar_departamento_manual(empresa, codigo),
        )

    def _consultar(self, tipo: str, consulta: Callable[[Any], Any]) -> None:
        if self._consultando:
            return
        from config.sqlserver import cargar_configuracion
        from database.repository import RepositorioEmpleados

        self._consultando = True
        self._cambiar_estado_botones_consulta("disabled")
        self._tipo_resultado = tipo
        self._btn_usar.configure(state="disabled")
        self._var_estado.set("Consultando SQL Server…")

        def tarea():
            repo = RepositorioEmpleados(cargar_configuracion())
            return consulta(repo)

        def terminar(resultado):
            self._consultando = False
            self._cambiar_estado_botones_consulta("normal")
            try:
                if not self.winfo_exists():
                    return
            except tk.TclError:
                return
            if isinstance(resultado, Exception):
                self._var_estado.set("La consulta no pudo completarse.")
                messagebox.showerror("Error de consulta", str(resultado), parent=self)
                return
            if tipo == "departamento":
                filas = [dict(fila) for fila in resultado]
            else:
                empleados = getattr(resultado, "empleados", [])
                filas = [empleado.como_dict() for empleado in empleados]
            self._mostrar_filas(filas)

        self._padre._ejecutar(tarea, terminar)

    def _cambiar_estado_botones_consulta(self, estado: str) -> None:
        for boton in (
            self._btn_buscar_todas,
            self._btn_buscar_serviam,
            self._btn_buscar_departamento,
        ):
            boton.configure(state=estado)

    def _mostrar_filas(self, filas: list[dict[str, Any]]) -> None:
        self._filas = filas
        self._tabla.delete(*self._tabla.get_children())
        if self._tipo_resultado == "empleado":
            columnas = ["NOMBRE", "EMPLEADO", "EMPRESA", "DEPARTAMENTO", "PUESTO", "ACTIVO"]
            aliases = {
                "NOMBRE": ("NOMBRE", "nombre"), "EMPLEADO": ("EMPLEADO", "empleado"),
                "EMPRESA": ("EMPRESA", "empresa"), "DEPARTAMENTO": ("DEPARTAMENTO", "departamento"),
                "PUESTO": ("PUESTO", "puesto"), "ACTIVO": ("ACTIVO", "activo"),
            }
        else:
            columnas = list(dict.fromkeys(key for fila in filas for key in fila))
            aliases = {col: (col,) for col in columnas}
        self._tabla.configure(columns=columnas)
        for columna in columnas:
            self._tabla.heading(columna, text=columna.title())
            self._tabla.column(columna, width=145, anchor="w")
        for indice, fila in enumerate(filas):
            valores = []
            for columna in columnas:
                valor = next((fila.get(alias) for alias in aliases[columna] if fila.get(alias) not in (None, "")), "")
                if columna == "EMPRESA" and not valor:
                    valor = fila.get("FUENTE") or fila.get("fuente") or ""
                valores.append(str(valor))
            self._tabla.insert("", "end", iid=str(indice), values=valores)
        self._btn_usar.configure(state="disabled")
        self._var_estado.set(f"{len(filas)} resultado(s). Seleccione un registro para continuar.")

    def _actualizar_seleccion(self, _evento=None) -> None:
        if self._tabla.selection() and self._filas and self._tipo_resultado == "empleado":
            self._btn_usar.configure(state="normal")

    def _seleccionar(self) -> None:
        seleccion = self._tabla.selection()
        if not seleccion:
            return
        fila = self._filas[int(seleccion[0])]
        normalizado = {str(clave).strip().upper(): valor for clave, valor in fila.items()}
        empleado = normalizado.get("EMPLEADO") or normalizado.get("CODIGO") or ""
        nombre = normalizado.get("NOMBRE") or normalizado.get("NOMBRE_COMPLETO") or ""
        empresa = str(normalizado.get("EMPRESA") or normalizado.get("FUENTE") or "").strip().upper()
        faltantes = [campo for campo, valor in (("NOMBRE", nombre), ("EMPRESA/FUENTE", empresa)) if not valor]
        if faltantes:
            messagebox.showwarning(
                "Columnas insuficientes",
                "No se puede identificar al empleado. Faltan: " + ", ".join(faltantes),
                parent=self,
            )
            return
        if empresa not in EMPRESAS:
            messagebox.showwarning(
                "Empresa no reconocida",
                "FUENTE debe identificar una de las 18 empresas autorizadas. No se modificó la asignación.",
                parent=self,
            )
            return
        candidato = {
            "EMPLEADO": empleado,
            "NOMBRE": nombre,
            "FUENTE": empresa,
            "EMPRESA": empresa,
            "DEPARTAMENTO": normalizado.get("DEPARTAMENTO"),
            "PUESTO": normalizado.get("PUESTO"),
            "ACTIVO": normalizado.get("ACTIVO"),
        }
        if not candidato["DEPARTAMENTO"] and not candidato["PUESTO"]:
            messagebox.showwarning(
                "Sin información de área",
                "El resultado no incluye DEPARTAMENTO ni PUESTO. No se modificó la asignación.",
                parent=self,
            )
            return
        self._al_elegir(candidato)
        self.destroy()


class VentanaConfirmarAsignacionArea(tk.Toplevel):
    """Confirmación breve para un candidato encontrado que requiere acción."""

    def __init__(
        self,
        padre: tk.Misc,
        *,
        usuario: str,
        candidato: dict[str, Any],
        area: str,
        area_sql: str = "",
        area_maestro: str = "",
        motivo: str,
        inactivo: bool,
        al_confirmar: Callable[[], bool],
    ) -> None:
        super().__init__(padre)
        self.title("Confirmar asignación de área")
        self.geometry("480x300")
        self.resizable(False, False)
        self.configure(bg=FONDO)
        self.transient(padre)
        self.grab_set()
        estado = "INACTIVO" if inactivo else str(candidato.get("ACTIVO") or "")
        ttk.Label(
            self,
            text="Se encontró una coincidencia sin asignar el área",
            style="Seccion.TLabel",
            wraplength=440,
        ).pack(anchor="w", padx=18, pady=(18, 10))
        datos = (
            ("Usuario", usuario),
            ("Empleado", str(candidato.get("NOMBRE") or candidato.get("nombre") or "—")),
            ("Empresa", str(candidato.get("EMPRESA") or candidato.get("empresa") or candidato.get("FUENTE") or "—")),
            ("Área SQL", area_sql or area or "—"),
            ("Área Excel maestro", area_maestro or "—"),
            ("Estado", estado or "No disponible"),
        )
        for etiqueta, valor in datos:
            fila = ttk.Frame(self)
            fila.pack(fill="x", padx=18, pady=2)
            ttk.Label(fila, text=f"{etiqueta}:", style="Clave.TLabel", width=12).pack(side="left")
            ttk.Label(fila, text=valor, style="Subtitulo.TLabel", wraplength=330).pack(side="left")
        nota = motivo or "¿Desea asignar esta área al usuario?"
        if inactivo:
            nota += " La asignación no reactivará al empleado."
        ttk.Label(
            self, text=nota, style="Subtitulo.TLabel", wraplength=440,
        ).pack(anchor="w", padx=18, pady=(8, 4))
        botones = ttk.Frame(self)
        botones.pack(fill="x", padx=18, pady=(8, 14))
        ttk.Button(botones, text="Cancelar", command=self.destroy).pack(side="right")
        ttk.Button(
            botones, text="Asignar área", style="Principal.TButton",
            command=lambda: self._confirmar(al_confirmar),
        ).pack(side="right", padx=(0, 8))
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _confirmar(self, callback: Callable[[], bool]) -> None:
        try:
            guardada = bool(callback())
        except Exception as exc:  # noqa: BLE001 - conservar el diálogo ante fallo
            messagebox.showerror("No se pudo asignar el área", str(exc), parent=self)
            return
        if guardada:
            self.destroy()


class VentanaCoincidencias(tk.Toplevel):
    """Selección manual del empleado, empresa y fuente correctos."""

    _COLUMNAS = (
        ("empleado", "EMPLEADO", 95),
        ("nombre", "NOMBRE", 200),
        ("tipo", "COINCIDENCIA", 120),
        ("similitud", "SIMILITUD", 95),
        ("cruce", "CRUCE EXCEL/SQL", 185),
        ("empresa", "EMPRESA", 105),
        ("fuente", "FUENTE", 110),
        ("tabla", "TABLA", 110),
        ("departamento", "DEPARTAMENTO", 180),
        ("area_sql", "ÁREA SQL", 150),
        ("area_maestro", "ÁREA MAESTRO", 150),
        ("puesto", "PUESTO", 170),
        ("activo", "ESTADO", 110),
        ("correo", "CORREO", 200),
    )

    def __init__(
        self,
        padre: tk.Misc,
        *,
        usuario: str,
        candidatos: Iterable[dict[str, Any]],
        al_elegir: Callable[[dict[str, Any]], bool | None],
        al_cancelar: Callable[[], None] | None = None,
        al_crear_area: Callable[[dict[str, Any]], None] | None = None,
        al_asignar_inactivo: Callable[[dict[str, Any]], bool | None] | None = None,
    ) -> None:
        super().__init__(padre)
        self.title("Seleccionar empleado / empresa correcta")
        self.geometry("1180x560")
        self.minsize(900, 420)
        self.configure(bg=FONDO)
        self.transient(padre)
        self.grab_set()
        self._candidatos = coincidencias_vista.consolidar(candidatos)
        self._al_elegir = al_elegir
        self._al_cancelar = al_cancelar
        self._al_crear_area = al_crear_area
        self._al_asignar_inactivo = al_asignar_inactivo
        self._iids: dict[str, dict[str, Any]] = {}
        self._orden = ("empleado", False)
        self._pestana = coincidencias_vista.PESTANA_TODOS
        self._botones_pestana: dict[str, ttk.Button] = {}

        self._var_empleado = tk.StringVar()
        self._var_nombre = tk.StringVar()
        self._var_empresa = tk.StringVar(value="Todas")
        self._var_fuente = tk.StringVar(value="Todas")
        self._var_departamento = tk.StringVar(value="Todos")
        self._var_conteo = tk.StringVar(value="")
        self._var_reporte = tk.StringVar(value="")

        ttk.Label(
            self,
            text=f"Seleccionar empleado / empresa correcta — {usuario}",
            style="Seccion.TLabel",
        ).pack(anchor="w", padx=18, pady=(14, 3))
        ttk.Label(
            self,
            text=(
                "Hay varias coincidencias. Elija el registro correcto; el área "
                "se calculará con el departamento de esa empresa y fuente."
            ),
            style="Subtitulo.TLabel",
        ).pack(anchor="w", padx=18)
        ttk.Label(
            self,
            textvariable=self._var_reporte,
            style="Subtitulo.TLabel",
            wraplength=1120,
            justify="left",
        ).pack(anchor="w", padx=18, pady=(4, 0))

        self._construir_pestanas()
        self._construir_filtros()
        self._construir_tabla()
        self._construir_botones()
        self._refrescar()
        self.protocol("WM_DELETE_WINDOW", self._cancelar)

    # ------------------------------------------------------------------
    def _construir_pestanas(self) -> None:
        """Pestañas visibles sin ``ttk.Notebook`` (el tema oculta sus solapas)."""

        marco = ttk.Frame(self)
        marco.pack(fill="x", padx=18, pady=(8, 0))
        for nombre in coincidencias_vista.PESTANAS:
            boton = ttk.Button(
                marco,
                text=nombre,
                command=lambda valor=nombre: self._cambiar_pestana(valor),
            )
            boton.pack(side="left", padx=(0, 6))
            self._botones_pestana[nombre] = boton

    def _cambiar_pestana(self, nombre: str) -> None:
        if nombre == self._pestana:
            return
        self._pestana = nombre
        self._refrescar()

    def _actualizar_pestanas(self, resumen: dict[str, int]) -> None:
        conteos = {
            coincidencias_vista.PESTANA_TODOS: resumen.get("total", 0),
            coincidencias_vista.PESTANA_EXCEL: resumen.get("excel", 0),
            coincidencias_vista.PESTANA_SQL: resumen.get("sql", 0),
        }
        for nombre, boton in self._botones_pestana.items():
            boton.configure(
                text=f"{nombre} ({conteos.get(nombre, 0)})",
                style=(
                    "Principal.TButton"
                    if nombre == self._pestana
                    else "TButton"
                ),
            )

    # ------------------------------------------------------------------
    def _construir_filtros(self) -> None:
        marco = ttk.Frame(self)
        marco.pack(fill="x", padx=18, pady=(10, 4))

        ttk.Label(marco, text="Empleado:").grid(row=0, column=0, sticky="w")
        entrada_empleado = ttk.Entry(
            marco, textvariable=self._var_empleado, width=16
        )
        entrada_empleado.grid(row=0, column=1, padx=(4, 12))
        ttk.Label(marco, text="Nombre:").grid(row=0, column=2, sticky="w")
        entrada_nombre = ttk.Entry(
            marco, textvariable=self._var_nombre, width=28
        )
        entrada_nombre.grid(row=0, column=3, padx=(4, 12))

        for entrada in (entrada_empleado, entrada_nombre):
            entrada.bind("<KeyRelease>", lambda _e: self._refrescar())

        ttk.Label(marco, text="Empresa:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self._combo_empresa = ttk.Combobox(
            marco,
            textvariable=self._var_empresa,
            values=["Todas"] + sel.opciones(self._candidatos, "empresa"),
            state="readonly",
            width=14,
        )
        self._combo_empresa.grid(row=1, column=1, padx=(4, 12), pady=(6, 0))
        self._combo_empresa.bind("<<ComboboxSelected>>", lambda _e: self._refrescar())

        ttk.Label(marco, text="Fuente:").grid(row=1, column=2, sticky="w", pady=(6, 0))
        self._combo_fuente = ttk.Combobox(
            marco,
            textvariable=self._var_fuente,
            values=["Todas"] + sel.opciones(self._candidatos, "fuente"),
            state="readonly",
            width=16,
        )
        self._combo_fuente.grid(row=1, column=3, padx=(4, 12), pady=(6, 0))
        self._combo_fuente.bind("<<ComboboxSelected>>", lambda _e: self._refrescar())

        ttk.Label(marco, text="Departamento:").grid(
            row=1, column=4, sticky="w", pady=(6, 0)
        )
        self._combo_departamento = ttk.Combobox(
            marco,
            textvariable=self._var_departamento,
            values=["Todos"] + sel.opciones(self._candidatos, "departamento"),
            state="readonly",
            width=24,
        )
        self._combo_departamento.grid(row=1, column=5, padx=(4, 8), pady=(6, 0))
        self._combo_departamento.bind(
            "<<ComboboxSelected>>", lambda _e: self._refrescar()
        )

        ttk.Button(
            marco, text="Limpiar filtros", command=self._limpiar_filtros
        ).grid(row=1, column=6, padx=(6, 0), pady=(6, 0))
        ttk.Label(marco, textvariable=self._var_conteo).grid(
            row=0, column=4, columnspan=3, sticky="e"
        )

    def _construir_tabla(self) -> None:
        marco = ttk.Frame(self)
        marco.pack(fill="both", expand=True, padx=18, pady=(4, 8))
        columnas = tuple(columna for columna, _titulo, _ancho in self._COLUMNAS)
        self._tabla = ttk.Treeview(
            marco, columns=columnas, show="headings", selectmode="browse"
        )
        for columna, titulo, ancho in self._COLUMNAS:
            opciones_heading: dict[str, Any] = {"text": titulo}
            if columna not in ("tabla", "cruce"):
                opciones_heading["command"] = lambda c=columna: self._ordenar(c)
            self._tabla.heading(columna, **opciones_heading)
            self._tabla.column(columna, width=ancho, anchor="w", stretch=False)
        barra_y = ttk.Scrollbar(marco, orient="vertical", command=self._tabla.yview)
        barra_x = ttk.Scrollbar(marco, orient="horizontal", command=self._tabla.xview)
        self._tabla.configure(
            yscrollcommand=barra_y.set, xscrollcommand=barra_x.set
        )
        self._tabla.grid(row=0, column=0, sticky="nsew")
        barra_y.grid(row=0, column=1, sticky="ns")
        barra_x.grid(row=1, column=0, sticky="ew")
        marco.rowconfigure(0, weight=1)
        marco.columnconfigure(0, weight=1)

        self._tabla.tag_configure("activo", background="#E7F7F0")
        self._tabla.tag_configure("inactivo", background="#FDECEC")
        self._tabla.tag_configure("desconocido", background="#F3F4F6")
        self._tabla.bind("<<TreeviewSelect>>", lambda _e: self._actualizar_boton())

    def _construir_botones(self) -> None:
        botones = ttk.Frame(self)
        botones.pack(fill="x", padx=18, pady=(0, 14))
        self._btn_confirmar = ttk.Button(
            botones,
            text="Confirmar selección",
            style="Principal.TButton",
            command=self._elegir,
            state="disabled",
        )
        self._btn_confirmar.pack(side="right")
        ttk.Button(botones, text="Cancelar", command=self._cancelar).pack(
            side="right", padx=(0, 8)
        )
        self._btn_crear_area = ttk.Button(
            botones,
            text="Crear área nueva…",
            command=self._crear_area,
            state="disabled",
        )
        if self._al_crear_area is not None:
            self._btn_crear_area.pack(side="left")
        self._btn_asignar_inactivo = ttk.Button(
            botones,
            text="Asignar área aunque esté inactivo",
            command=self._asignar_inactivo,
            state="disabled",
        )
        if self._al_asignar_inactivo is not None:
            self._btn_asignar_inactivo.pack(side="left", padx=(8, 0))

    # ------------------------------------------------------------------
    def _filtros(self) -> dict[str, str]:
        def limpio(valor: str, comodin: str) -> str:
            return "" if valor == comodin else valor

        return {
            "empleado": self._var_empleado.get().strip(),
            "nombre": self._var_nombre.get().strip(),
            "empresa": limpio(self._var_empresa.get(), "Todas"),
            "fuente": limpio(self._var_fuente.get(), "Todas"),
            "departamento": limpio(self._var_departamento.get(), "Todos"),
        }

    def _refrescar(self) -> None:
        resumen = coincidencias_vista.resumen(self._candidatos)
        self._actualizar_pestanas(resumen)
        self._var_reporte.set(
            f"Total {resumen['total']} · Excel {resumen['excel']} · "
            f"SQL {resumen['sql']} · Activos {resumen['activos']} · "
            f"Inactivos {resumen['inactivos']} · "
            f"No disponibles {resumen['no_disponibles']} · "
            f"Cruces: {resumen['filas_cruce_confirmado']} confirmadas / "
            f"{resumen['filas_cruce_posible']} posibles"
        )

        base = coincidencias_vista.filtrar(self._candidatos, self._pestana)
        filtrados = sel.filtrar_candidatos(base, **self._filtros())
        columna, descendente = self._orden
        if columna in ("tipo", "similitud"):
            filtrados = sorted(
                filtrados,
                key=lambda candidato: self._clave_orden_especial(
                    candidato, columna
                ),
                reverse=descendente,
            )
        else:
            filtrados = sel.ordenar_candidatos(
                filtrados, columna, descendente=descendente
            )
        self._tabla.delete(*self._tabla.get_children())
        self._iids.clear()
        for indice, candidato in enumerate(filtrados):
            iid = f"c{indice}"
            self._iids[iid] = candidato
            estado = coincidencias_vista.estado_candidato(candidato)
            tag = (
                "activo"
                if estado == coincidencias_vista.ESTADO_ACTIVO
                else "inactivo"
                if estado == coincidencias_vista.ESTADO_INACTIVO
                else "desconocido"
            )
            self._tabla.insert(
                "",
                "end",
                iid=iid,
                values=tuple(
                    self._valor_columna(candidato, columna)
                    for columna, _titulo, _ancho in self._COLUMNAS
                ),
                tags=(tag,),
            )
        self._var_conteo.set(
            f"Mostrando {len(filtrados)} de {len(base)} en «{self._pestana}»"
        )
        self._actualizar_boton()

    @staticmethod
    def _clave_orden_especial(
        candidato: dict[str, Any], columna: str
    ) -> tuple[int, float | int]:
        if columna == "similitud":
            try:
                return (0, float(candidato.get("similitud") or 0))
            except (TypeError, ValueError):
                return (1, 0)
        tipos = {
            "exacta": 0,
            "alta": 1,
            "intermedia": 2,
            "baja": 3,
        }
        tipo = str(candidato.get("tipo") or "").strip().lower()
        return (0, tipos.get(tipo, 4))

    def _valor_columna(self, candidato: dict[str, Any], columna: str) -> str:
        if columna == "area_sql":
            return str(candidato.get("AREA_SQL") or "—")
        if columna == "area_maestro":
            return str(candidato.get("AREA_MAESTRO") or "—")
        if columna == "tabla":
            return str(candidato.get("tabla_origen") or "—")
        if columna == "cruce":
            return coincidencias_vista.texto_cruce(candidato) or "—"
        if columna == "activo":
            return coincidencias_vista.estado_candidato(candidato)
        if columna == "tipo":
            tipo = str(candidato.get("tipo") or "").strip().lower()
            return {
                "exacta": "Exacta",
                "alta": "Alta",
                "intermedia": "Intermedia",
                "baja": "Baja",
            }.get(tipo, "—")
        if columna == "similitud":
            try:
                return f"{float(candidato.get('similitud') or 0):.0%}"
            except (TypeError, ValueError):
                return "—"
        return sel.valor(candidato, columna)

    def _actualizar_boton(self) -> None:
        hay_seleccion = bool(self._tabla.selection())
        self._btn_confirmar.configure(
            state="normal" if hay_seleccion else "disabled"
        )
        if self._al_crear_area is not None:
            self._btn_crear_area.configure(
                state="normal" if hay_seleccion else "disabled"
            )
        if self._al_asignar_inactivo is not None:
            candidato = (
                self._iids.get(self._tabla.selection()[0])
                if hay_seleccion else None
            )
            # Solo INACTIVO inequívoco habilita la acción excepcional.
            # NO DISPONIBLE nunca se interpreta como inactivo.
            inactivo = candidato is not None and coincidencias_vista.es_inactivo(
                candidato
            )
            self._btn_asignar_inactivo.configure(
                state="normal" if inactivo else "disabled"
            )

    def _limpiar_filtros(self) -> None:
        self._var_empleado.set("")
        self._var_nombre.set("")
        self._var_empresa.set("Todas")
        self._var_fuente.set("Todas")
        self._var_departamento.set("Todos")
        self._refrescar()

    def _ordenar(self, columna: str) -> None:
        actual, descendente = self._orden
        self._orden = (columna, not descendente if actual == columna else False)
        self._refrescar()

    def _elegir(self) -> None:
        seleccion = self._tabla.selection()
        if not seleccion:
            messagebox.showinfo(
                "Sin selección", "Seleccione una coincidencia de la lista."
            )
            return
        candidato = self._iids.get(seleccion[0])
        if candidato is None:
            return
        # La ventana solo se cierra si la asignación se completó. Si el usuario
        # cancela la confirmación, hay conflicto o falla el guardado, se
        # conserva la ventana y la selección para reintentar.
        resultado = self._al_elegir(dict(candidato))
        if resultado is False:
            return
        self.destroy()

    def _crear_area(self) -> None:
        seleccion = self._tabla.selection()
        if not seleccion:
            messagebox.showinfo(
                "Sin selección", "Seleccione una coincidencia de la lista."
            )
            return
        candidato = self._iids.get(seleccion[0])
        if candidato is None or self._al_crear_area is None:
            return
        self._al_crear_area(dict(candidato))
        self.destroy()

    def _asignar_inactivo(self) -> None:
        seleccion = self._tabla.selection()
        if not seleccion or self._al_asignar_inactivo is None:
            return
        candidato = self._iids.get(seleccion[0])
        # Solo se permite con INACTIVO inequívoco; NO DISPONIBLE no habilita
        # esta acción excepcional.
        if candidato is None or not coincidencias_vista.es_inactivo(candidato):
            return
        resultado = self._al_asignar_inactivo(dict(candidato))
        if resultado is False:
            return
        self.destroy()

    def _cancelar(self) -> None:
        if self._al_cancelar is not None:
            self._al_cancelar()
        self.destroy()


class VentanaDetalleRegistro(tk.Toplevel):
    def __init__(self, padre: tk.Misc, registro: dict[str, Any]) -> None:
        super().__init__(padre)
        self.title("Detalle del registro")
        self.geometry("620x520")
        self.configure(bg=FONDO)
        self.transient(padre)
        self.grab_set()

        ttk.Label(
            self,
            text=str(registro.get("usuario") or "Registro"),
            style="Seccion.TLabel",
        ).pack(anchor="w", padx=18, pady=(14, 6))

        marco = ttk.Frame(self)
        marco.pack(fill="both", expand=True, padx=18, pady=(0, 8))
        texto = tk.Text(
            marco,
            wrap="word",
            bg=SUPERFICIE,
            fg=TEXTO,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDE,
            font=(ui_tema.FUENTE, 10),
            padx=12,
            pady=10,
        )
        barra = ttk.Scrollbar(marco, orient="vertical", command=texto.yview)
        texto.configure(yscrollcommand=barra.set)
        texto.pack(side="left", fill="both", expand=True)
        barra.pack(side="right", fill="y")

        seleccionado = registro.get("seleccionado") or {}
        lineas = [
            f"Solicitante: {registro.get('usuario', '')}",
            f"Empresa: {registro.get('empresa', '') or '—'}",
            f"Ticket / fila: {registro.get('ticket', '') or '—'}",
            f"Área actual: {registro.get('area_actual', '') or '—'}",
            f"Área sugerida: {registro.get('area_sugerida', '') or '—'}",
            f"Estado: {registro.get('estado', '')}",
            f"Caso: {registro.get('caso', '') or '—'}",
            f"Origen del área: {registro.get('origen', '') or '—'}",
            f"Validado: {'Sí' if registro.get('validado') else 'No'}",
            f"Motivo: {registro.get('motivo', '') or '—'}",
        ]
        if seleccionado:
            lineas.extend(
                [
                    "",
                    "Coincidencia seleccionada:",
                    f"  Empresa: {seleccionado.get('EMPRESA', '')}",
                    f"  Código: {seleccionado.get('EMPLEADO', '')}",
                    f"  Nombre: {seleccionado.get('NOMBRE', '')}",
                    f"  Departamento: {seleccionado.get('DEPARTAMENTO', '')}",
                ]
            )
        historial = registro.get("historial") or []
        if historial:
            lineas.extend(["", "Historial de cambios:"])
            for item in historial:
                lineas.append(
                    f"  {item.get('fecha', '')} · {item.get('accion', '')} · "
                    f"{item.get('area', '')} · {item.get('estado', '')}"
                )
        texto.insert("1.0", "\n".join(lineas))
        texto.configure(state="disabled")

        ttk.Button(self, text="Cerrar", command=self.destroy).pack(
            anchor="e", padx=18, pady=(0, 14)
        )
