"""Ventana independiente de Validación de Usuarios y Áreas.

Se abre después del procesamiento cuando existen áreas vacías, usuarios no
encontrados, coincidencias ambiguas, empleados sin departamento o conflictos
con el maestro local. No reemplaza ninguna pestaña: es una ventana ``Toplevel``.

Toda acción se guarda de inmediato en la revisión persistente, de modo que
cerrar la ventana o la aplicación no hace perder el trabajo.
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable, Iterable

from config import configuracion as cfg
from config.sqlserver import cargar_configuracion
from core import procedencia
from core import revision as svc_revision
from core.maestro import Maestro
from database.connection import controlador_disponible
from database.resolucion import (
    CASO_A,
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

        self._tabla.bind("<Double-1>", lambda _e: self._ver_detalles())
        self._menu = tk.Menu(self, tearoff=0)
        self._menu.add_command(label="Consultar SQL", command=self._consultar_sql)
        self._menu.add_command(label="Asignar área", command=self._asignar_area)
        self._menu.add_command(
            label="Revisar coincidencias", command=self._revisar_coincidencias
        )
        self._menu.add_command(label="Ver detalles", command=self._ver_detalles)
        self._tabla.bind("<Button-3>", self._mostrar_menu)

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
                    "Consultar · Asignar · Revisar · Detalles",
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

    def _mostrar_menu(self, evento) -> None:
        iid = self._tabla.identify_row(evento.y)
        if iid:
            self._tabla.selection_set(iid)
            self._menu.tk_popup(evento.x_root, evento.y_root)

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

        nombres = [str(registro.get("usuario") or "") for registro in registros]
        self._ocupado = True
        self._var_resumen.set("Consultando SQL Server por lotes…")

        def tarea():
            from database.repository import RepositorioEmpleados

            repositorio = RepositorioEmpleados(config)
            consulta = repositorio.buscar_por_nombres(nombres)
            if consulta.empleados:
                Sincronizador(
                    config, repositorio=repositorio
                ).actualizar_desde_busqueda(consulta.empleados)
            resolver = ResolverAreas(cargar_empleados(), maestro=self.maestro)
            return resolver.resolver_lote(nombres), consulta.errores

        def terminar(resultado):
            self._ocupado = False
            if isinstance(resultado, Exception):
                self._var_resumen.set(f"Error al consultar SQL: {resultado}")
                messagebox.showerror("Error de consulta", str(resultado))
                return
            resoluciones, errores = resultado
            asignados = 0
            for registro in registros:
                clave = registro.get("clave") or ""
                resolucion = resoluciones.get(clave)
                if resolucion is None:
                    continue
                if resolucion.asignable and resolucion.caso == CASO_A:
                    self.revision.actualizar(
                        registro.get("usuario"),
                        area=resolucion.area,
                        estado=ESTADO_VALIDADO,
                        validado=True,
                        origen=procedencia.SQL_SERVER,
                        motivo=resolucion.motivo,
                        accion="consulta_sql",
                    )
                    self._asignar_en_maestro(
                        str(registro.get("usuario") or ""),
                        resolucion.area,
                        procedencia.SQL_SERVER,
                    )
                    asignados += 1
                elif resolucion.conflicto:
                    self.revision.actualizar(
                        registro.get("usuario"),
                        estado=resolucion.estado,
                        validado=False,
                        motivo=resolucion.motivo,
                        accion="conflicto_sql",
                    )
                else:
                    self.revision.actualizar(
                        registro.get("usuario"),
                        estado=resolucion.estado,
                        area=resolucion.area or registro.get("area_actual"),
                        validado=False,
                        motivo=resolucion.motivo,
                        accion="consulta_sql",
                    )
            self.revision.guardar()
            self._refrescar()
            mensaje = f"Consulta completada. Áreas asignadas: {asignados}."
            if errores:
                mensaje += f" Empresas con error: {len(errores)}."
            self._var_resumen.set(mensaje)

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

    def _asignar_en_maestro(self, usuario: str, area: str, origen: str) -> None:
        if self._al_asignar is not None:
            try:
                self._al_asignar(usuario, area, origen)
            except Exception as exc:  # noqa: BLE001
                LOGGER.error("No se pudo persistir la asignación: %s", exc)
        try:
            procedencia.registrar(
                usuario, area, origen, nota="Validación de usuarios y áreas."
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("No se pudo registrar la procedencia: %s", exc)

    def _revisar_coincidencias(self) -> None:
        registro = self._un_registro()
        if registro is None:
            return
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
        )

    def _elegir_candidato(
        self, registro: dict[str, Any], candidato: dict[str, Any]
    ) -> None:
        self.revision.seleccionar_candidato(registro.get("usuario"), candidato)
        area = (
            candidato.get("DEPARTAMENTO")
            or candidato.get("departamento")
            or registro.get("area_actual")
        )
        if area:
            self._asignar_en_maestro(
                str(registro.get("usuario") or ""),
                str(area),
                procedencia.MANUAL,
            )
        self.revision.guardar()
        self._refrescar()
        self._var_resumen.set(
            f"{registro.get('usuario')}: coincidencia seleccionada."
        )

    def _validar_seleccionados(self) -> None:
        registros = self._seleccionados()
        if not registros:
            messagebox.showinfo("Sin selección", "Seleccione al menos una fila.")
            return
        validados = 0
        for registro in registros:
            if str(registro.get("area_actual") or "").strip():
                self.revision.actualizar(
                    registro.get("usuario"),
                    estado=ESTADO_VALIDADO,
                    validado=True,
                    accion="validacion_manual",
                )
                validados += 1
        self.revision.guardar()
        self._refrescar()
        self._var_resumen.set(f"Registros marcados como validados: {validados}")

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

        self._var_area = tk.StringVar()
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


class VentanaCoincidencias(tk.Toplevel):
    def __init__(
        self,
        padre: tk.Misc,
        *,
        usuario: str,
        candidatos: Iterable[dict[str, Any]],
        al_elegir: Callable[[dict[str, Any]], None],
    ) -> None:
        super().__init__(padre)
        self.title("Revisar coincidencias")
        self.geometry("820x420")
        self.configure(bg=FONDO)
        self.transient(padre)
        self.grab_set()
        self._candidatos = list(candidatos)
        self._al_elegir = al_elegir

        ttk.Label(
            self,
            text=f"Coincidencias para: {usuario}",
            style="Seccion.TLabel",
        ).pack(anchor="w", padx=18, pady=(14, 4))
        ttk.Label(
            self,
            text=(
                "El sistema no elige automáticamente cuando hay varias "
                "personas con el mismo nombre. Seleccione la correcta."
            ),
            style="Subtitulo.TLabel",
        ).pack(anchor="w", padx=18)

        marco = ttk.Frame(self)
        marco.pack(fill="both", expand=True, padx=18, pady=10)
        columnas = ("empresa", "codigo", "nombre", "departamento", "estado")
        self._tabla = ttk.Treeview(
            marco, columns=columnas, show="headings", selectmode="browse"
        )
        for columna, titulo, ancho in (
            ("empresa", "Empresa", 110),
            ("codigo", "Código", 100),
            ("nombre", "Nombre completo", 240),
            ("departamento", "Departamento", 240),
            ("estado", "Estado", 110),
        ):
            self._tabla.heading(columna, text=titulo)
            self._tabla.column(columna, width=ancho, anchor="w")
        barra = ttk.Scrollbar(marco, orient="vertical", command=self._tabla.yview)
        self._tabla.configure(yscrollcommand=barra.set)
        self._tabla.pack(side="left", fill="both", expand=True)
        barra.pack(side="right", fill="y")

        for candidato in self._candidatos:
            self._tabla.insert(
                "",
                "end",
                values=(
                    candidato.get("EMPRESA") or candidato.get("empresa") or "",
                    candidato.get("EMPLEADO") or candidato.get("empleado") or "",
                    candidato.get("NOMBRE") or candidato.get("nombre") or "",
                    candidato.get("DEPARTAMENTO")
                    or candidato.get("departamento")
                    or "",
                    candidato.get("ESTADO_EMPLEADO")
                    or candidato.get("estado_empleado")
                    or "",
                ),
            )

        botones = ttk.Frame(self)
        botones.pack(fill="x", padx=18, pady=(0, 14))
        ttk.Button(
            botones,
            text="Seleccionar coincidencia",
            style="Principal.TButton",
            command=self._elegir,
        ).pack(side="right")
        ttk.Button(botones, text="Cancelar", command=self.destroy).pack(
            side="right", padx=(0, 8)
        )

    def _elegir(self) -> None:
        seleccion = self._tabla.selection()
        if not seleccion:
            messagebox.showinfo(
                "Sin selección", "Seleccione una coincidencia de la lista."
            )
            return
        indice = self._tabla.index(seleccion[0])
        self._al_elegir(self._candidatos[indice])
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
