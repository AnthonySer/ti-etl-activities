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
from config.sqlserver import cargar_configuracion
from core import acciones_revision as acciones
from core import asignacion_asistida as asistida
from core import busqueda_solicitantes as busqueda_core
from core import catalogo_areas
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
    ) -> None:
        super().__init__(padre)
        self.title("Validación de Usuarios y Áreas")
        self.geometry("1280x800")
        self.minsize(1024, 640)
        self.configure(bg=FONDO)
        self.transient(padre)

        self.revision = revision
        self.maestro = maestro or Maestro()
        self._al_asignar = al_asignar
        self._al_generar = al_generar
        self._ocupado = False
        self._generando = False
        self._iids: dict[str, dict[str, Any]] = {}

        self._var_busqueda = tk.StringVar()
        self._var_estado = tk.StringVar(value="Todos")
        self._var_modo = tk.StringVar(value="Excel normal")
        self._var_resumen = tk.StringVar(value="")
        self._var_conexion = tk.StringVar(value="")

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
        entrada.bind("<KeyRelease>", lambda _e: self._refrescar())

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
            width=26,
        )
        self._combo_estado.pack(side="left", padx=(6, 14))
        self._combo_estado.bind("<<ComboboxSelected>>", lambda _e: self._refrescar())

        ttk.Button(
            marco, text="Actualizar", command=self._refrescar
        ).pack(side="left")

        self._pill_revision = Pill(
            marco, "0 pendientes", estilo="warning", punto=True
        )
        self._pill_revision.pack(side="right")

    def _construir_tabla(self) -> None:
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
            izquierda, text="Asignar área", command=self._asignar_area
        ).pack(side="left", padx=6)
        ttk.Button(
            izquierda, text="Revisar coincidencias", command=self._revisar_coincidencias
        ).pack(side="left", padx=6)
        ttk.Button(
            izquierda, text="Ver detalles", command=self._ver_detalles
        ).pack(side="left", padx=6)
        ttk.Button(
            izquierda, text="Validar seleccionados", command=self._validar_seleccionados
        ).pack(side="left", padx=6)

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
            "Seleccione una o varias filas. También puede usar clic derecho "
            "para las acciones por fila."
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
    def _registros_filtrados(self) -> list[dict[str, Any]]:
        busqueda = self._var_busqueda.get().strip().lower()
        estado = self._var_estado.get()
        salida = []
        for registro in self.revision.registros:
            if estado != "Todos" and str(registro.get("estado")) != estado:
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
        self._tabla.delete(*self._tabla.get_children())
        self._iids.clear()
        for indice, registro in enumerate(self._registros_filtrados(), start=1):
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
            self._tabla.insert(
                "",
                "end",
                iid=iid,
                values=(
                    indice,
                    _texto(registro.get("usuario")),
                    _texto(registro.get("empresa")),
                    _texto(registro.get("ticket")),
                    _texto(registro.get("area_actual")) or "—",
                    estado,
                    acciones.texto_acciones(registro),
                ),
                tags=(tag,),
            )
        self._actualizar_kpis()

    def _actualizar_kpis(self) -> None:
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
            empresas=config.empresas_activas(),
        )
        self._ocupado = True
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
                clave = registro.get("clave") or ""
                resolucion = resoluciones.get(clave)
                if resolucion is None:
                    continue
                candidatos = resolucion.candidatos_dict()
                elegido = resolucion.empleado_dict()
                if resolucion.asignable and resolucion.caso == CASO_A:
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
            # La subventana se abre desde el hilo principal, nunca desde el
            # trabajador. Si hay una sola coincidencia ambigua, se ofrece de
            # inmediato la selección manual.
            if not cancelada and len(registros) == 1 and len(ambiguos) == 1:
                self.after(10, lambda: self._abrir_seleccion(ambiguos[0]))

        self._ejecutar(tarea, terminar)

    def _asignar_area(self) -> None:
        registro = self._un_registro()
        if registro is None:
            return
        VentanaAsignarArea(
            self,
            usuario=str(registro.get("usuario") or ""),
            areas=self._areas_disponibles(),
            al_guardar=lambda area: self._aplicar_asignacion(registro, area),
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
        resultado = catalogo_areas.validar_area(area, maestro=self.maestro)
        if not resultado.valido:
            messagebox.showwarning("Área no válida", resultado.mensaje)
            return

        if resultado.existente:
            area_final = resultado.area_reutilizable or resultado.nombre
            if not messagebox.askyesno("Área existente", resultado.mensaje):
                return
        elif resultado.similares:
            similar = resultado.similares[0]
            if messagebox.askyesno(
                "Área parecida",
                f"Ya existe el área '{similar.nombre}' "
                f"({similar.similitud:.0%} de similitud).\n\n"
                f"¿Desea reutilizar '{similar.nombre}' en lugar de crear "
                f"'{resultado.nombre}'?",
            ):
                area_final = similar.nombre
            elif messagebox.askyesno(
                "Confirmar área nueva",
                f"'{resultado.nombre}' es parecida a '{similar.nombre}', pero "
                "distinta.\n\n¿Registrarla como área nueva?",
            ):
                area_final = resultado.nombre
            else:
                return
        else:
            if not messagebox.askyesno(
                "Confirmar área nueva",
                f"El área '{resultado.nombre}' no existe en el maestro.\n\n"
                f"¿Registrarla y asignarla a '{usuario}'?",
            ):
                return
            area_final = resultado.nombre

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
        self.revision.asignar_manual(registro.get("usuario"), area)
        self.revision.guardar()
        self._asignar_en_maestro(
            str(registro.get("usuario") or ""), area, procedencia.MANUAL
        )
        self._refrescar()
        self._var_resumen.set(
            f"{registro.get('usuario')}: área asignada manualmente → {area}"
        )

    def _asignar_en_maestro(
        self,
        usuario: str,
        area: str,
        origen: str,
        *,
        detalle: dict[str, Any] | None = None,
    ) -> None:
        if self._al_asignar is not None:
            try:
                self._al_asignar(usuario, area, origen)
            except Exception as exc:  # noqa: BLE001
                LOGGER.error("No se pudo persistir la asignación: %s", exc)
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

    def _revisar_coincidencias(self) -> None:
        registro = self._un_registro()
        if registro is None:
            return
        self._abrir_seleccion(registro)

    def _abrir_seleccion(self, registro: dict[str, Any]) -> None:
        candidatos = registro.get("candidatos") or []
        if not candidatos:
            messagebox.showinfo(
                "Sin coincidencias",
                "No hay coincidencias registradas. Use 'Consultar SQL' para "
                "volver a buscar al empleado.",
            )
            return
        VentanaCoincidencias(
            self,
            usuario=str(registro.get("usuario") or ""),
            candidatos=candidatos,
            al_elegir=lambda candidato: self._elegir_candidato(registro, candidato),
            al_crear_area=lambda candidato: self._crear_area_desde_coincidencia(
                registro, candidato
            ),
        )

    def _elegir_candidato(
        self, registro: dict[str, Any], candidato: dict[str, Any]
    ) -> None:
        """Resuelve el área usando exclusivamente el registro elegido."""

        usuario = str(registro.get("usuario") or "")
        resolver = ResolverAreas(cargar_empleados(), maestro=self.maestro)
        resolucion = resolver.resolver_candidato(usuario, candidato)
        area = resolucion.area if resolucion.caso == CASO_A and resolucion.asignable else ""
        estado = ESTADO_VALIDADO if area else resolucion.estado
        if area and not self._confirmar_asignacion_candidato(
            usuario, candidato, area
        ):
            return
        self.revision.registrar_seleccion(
            usuario,
            candidato,
            area=area,
            estado=estado,
            motivo=resolucion.motivo,
            tipo_resolucion="manual",
        )
        if area:
            self._asignar_en_maestro(
                usuario,
                area,
                procedencia.MANUAL,
                detalle=sel.registro_trazable(
                    candidato, area=area, tipo_resolucion="manual"
                ),
            )
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
        self.revision.guardar()
        self._refrescar()
        self._var_resumen.set(mensaje)

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
        areas: set[str] = set()
        try:
            df = self.maestro.como_dataframe()
            if not df.empty:
                for valor in df[cfg.COLUMNA_AREA].tolist():
                    texto = str(valor or "").strip()
                    if texto:
                        areas.add(texto)
        except Exception:  # noqa: BLE001
            pass
        for registro in self.revision.registros:
            texto = str(registro.get("area_actual") or "").strip()
            if texto:
                areas.add(texto)
        return sorted(areas, key=str.casefold)


# ---------------------------------------------------------------------------
# Diálogos auxiliares
# ---------------------------------------------------------------------------
class VentanaAsignarArea(tk.Toplevel):
    def __init__(
        self,
        padre: tk.Misc,
        *,
        usuario: str,
        areas: Iterable[str],
        al_guardar: Callable[[str], None],
        area_inicial: str = "",
    ) -> None:
        super().__init__(padre)
        self.title("Asignar área")
        self.geometry("520x230")
        self.configure(bg=FONDO)
        self.transient(padre)
        self.grab_set()
        self._al_guardar = al_guardar

        ttk.Label(
            self, text=f"Usuario: {usuario}", style="Seccion.TLabel"
        ).pack(anchor="w", padx=18, pady=(16, 6))
        ttk.Label(
            self,
            text="Seleccione un área existente o escriba una nueva.",
            style="Subtitulo.TLabel",
        ).pack(anchor="w", padx=18)

        self._var_area = tk.StringVar(value=area_inicial)
        combo = ttk.Combobox(
            self,
            textvariable=self._var_area,
            values=list(areas),
            width=46,
        )
        combo.pack(fill="x", padx=18, pady=10)
        combo.focus_set()

        botones = ttk.Frame(self)
        botones.pack(fill="x", padx=18, pady=(4, 16))
        ttk.Button(
            botones,
            text="Guardar",
            style="Principal.TButton",
            command=self._guardar,
        ).pack(side="right")
        ttk.Button(botones, text="Cancelar", command=self.destroy).pack(
            side="right", padx=(0, 8)
        )

    def _guardar(self) -> None:
        area = self._var_area.get().strip()
        if not area:
            messagebox.showwarning("Área vacía", "Indique un área válida.")
            return
        self._al_guardar(area)
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
                "Se detectaron tres o más palabras. Ordene los términos con los "
                "botones ↑ / ↓ o con doble clic en la lista."
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
        self._actualizar()

    def _actualizar(self) -> None:
        self._lista.delete(0, "end")
        for termino in self._terminos:
            self._lista.insert("end", termino)
        self._var_patron.set(busqueda_core.patron_visible(self._terminos))

    def _confirmar(self) -> None:
        self._resultado = list(self._terminos)
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
        combinado = self._resumen.registrar(evento)
        empresa = str(combinado.get("empresa") or "")
        if not empresa:
            return
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
                empresa,
                seguimiento.etiqueta(estado),
                "—" if resultados is None else str(resultados),
                self._formato_tiempo(duracion),
                detalle or "—",
            ),
            tags=(self._TAG_POR_ESTADO.get(estado, "pendiente"),),
        )
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


class VentanaCoincidencias(tk.Toplevel):
    """Selección manual del empleado, empresa y fuente correctos."""

    _COLUMNAS = (
        ("empleado", "EMPLEADO", 95),
        ("nombre", "NOMBRE", 200),
        ("tipo", "COINCIDENCIA", 120),
        ("similitud", "SIMILITUD", 95),
        ("empresa", "EMPRESA", 105),
        ("fuente", "FUENTE", 110),
        ("departamento", "DEPARTAMENTO", 180),
        ("puesto", "PUESTO", 170),
        ("activo", "ACTIVO", 95),
        ("correo", "CORREO", 200),
    )

    def __init__(
        self,
        padre: tk.Misc,
        *,
        usuario: str,
        candidatos: Iterable[dict[str, Any]],
        al_elegir: Callable[[dict[str, Any]], None],
        al_cancelar: Callable[[], None] | None = None,
        al_crear_area: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        super().__init__(padre)
        self.title("Seleccionar empleado / empresa correcta")
        self.geometry("1180x560")
        self.minsize(900, 420)
        self.configure(bg=FONDO)
        self.transient(padre)
        self.grab_set()
        self._candidatos = list(candidatos)
        self._al_elegir = al_elegir
        self._al_cancelar = al_cancelar
        self._al_crear_area = al_crear_area
        self._iids: dict[str, dict[str, Any]] = {}
        self._orden = ("empleado", False)

        self._var_empleado = tk.StringVar()
        self._var_nombre = tk.StringVar()
        self._var_empresa = tk.StringVar(value="Todas")
        self._var_fuente = tk.StringVar(value="Todas")
        self._var_departamento = tk.StringVar(value="Todos")
        self._var_conteo = tk.StringVar(value="")

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

        self._construir_filtros()
        self._construir_tabla()
        self._construir_botones()
        self._refrescar()
        self.protocol("WM_DELETE_WINDOW", self._cancelar)

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
            self._tabla.heading(
                columna,
                text=titulo,
                command=lambda c=columna: self._ordenar(c),
            )
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
        filtrados = sel.filtrar_candidatos(self._candidatos, **self._filtros())
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
            etiqueta = sel.valor(candidato, "activo")
            tag = (
                "activo"
                if etiqueta == "Activo"
                else "inactivo"
                if etiqueta == "Inactivo"
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
            f"{len(filtrados)} de {len(self._candidatos)} coincidencias"
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
        self._al_elegir(dict(candidato))
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
