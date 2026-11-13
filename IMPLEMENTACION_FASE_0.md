# IMPLEMENTACIÓN FASE 0 — Auditoría y diagnóstico del sistema

## Objetivo

Comprender la arquitectura real del proyecto, documentar el flujo de
procesamiento de Excel, la resolución de usuarios sin área, las consultas SQL,
la persistencia del maestro y los riesgos de duplicación antes de modificar el
sistema.

## Archivos modificados

- `AUDITORIA_SISTEMA_ACTUAL.md` (nuevo).
- `scripts/audit_diagnostico.py` (nuevo, solo lectura).

## Componentes creados

- Script de auditoría que inspecciona la estructura del repositorio, los
  módulos, las rutas de configuración y los puntos de integración sin abrir
  conexiones de escritura ni modificar datos.

## Funcionalidades implementadas

- Documentación de la arquitectura actual y del flujo ETL.
- Identificación de los módulos de normalización, resolución, coincidencias,
  maestro, auditoría y presentación.
- Inventario de funcionalidades que deben conservarse.
- Detección de riesgos: comparaciones solo por nombre, refrescos completos de
  la interfaz, escrituras sin detección de cambios externos, ausencia de
  diario de operaciones y posible duplicación de áreas.
- Propuesta de arquitectura objetivo alineada con las fases siguientes.

## Pruebas ejecutadas

- Ejecución del script de auditoría sobre el proyecto real.
- Verificación de que el script no escribe en `data/`, `output/` ni en el
  maestro.
- Suite completa del proyecto: `python -m unittest discover -s tests`.

## Resultados obtenidos

- `AUDITORIA_SISTEMA_ACTUAL.md` generado con datos verificables del proyecto.
- Suite completa: **212 pruebas, 4 omitidas, 0 fallos**.
- No se modificó el comportamiento de la aplicación en esta fase.

## Problemas encontrados

- La búsqueda dependía principalmente del nombre exacto normalizado.
- Las coincidencias de SQL, caché y maestro no se consolidaban en un solo
  resultado.
- La interfaz no disponía de un mecanismo de actualización dirigido por
  eventos.
- El guardado del maestro no detectaba cambios externos ni archivos abiertos.
- No existía un diario persistente de operaciones con reintentos idempotentes.

## Pendientes

- Ninguno para la fase 0.

## Compatibilidad con fases anteriores

Es la primera fase. No se eliminaron archivos ni se alteraron contratos
existentes. La auditoría es de solo lectura.
