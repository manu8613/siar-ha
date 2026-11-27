# Estado

Actualizado: 2026-11-27 · versión 0.1.0

## Hecho
- Integración completa (alta, estaciones, sensores, tiempo, opciones, diagnósticos, es/en).
- 82 pruebas en verde con Home Assistant 2026.8.0 y 2026.10.4.
- Recorrido completo en Home Assistant 2026.11.3 con un SiAR simulado (capturas en `docs/img`).

## Sin verificar
- Funcionamiento con una cuenta real de SiAR: formato exacto de `obtenerToken`, códigos de error,
  caducidad del token y retraso de publicación (el manual no los detalla).
- Instalación desde la tienda de Home Assistant.
- El kit de pruebas (`pytest-homeassistant-custom-component` 0.13.375) llega a 2026.10.4 y no arranca
  forzándolo a 2026.11.3.

## Pendiente / próximo paso
1. Probar con una cuenta real y ajustar lo que difiera del manual.
2. Valorar proponer el icono a `home-assistant/brands` si la tienda lo pide.

## Notas
- Mínimo 2026.11.0: las anteriores incluyen versiones de librerías con vulnerabilidades publicadas.
- La antigua API con clave (`/apisiar/API/v1`) respondía 404 al crear la integración.
