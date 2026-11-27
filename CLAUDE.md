# CLAUDE.md

## Qué es
Integración de Home Assistant (`custom_components/siar`) con los datos de las estaciones SiAR.
Repo público.

## Por dónde empezar
1. `docs/STATUS.md`.
2. Solo los archivos implicados:
   - `api.py`: cliente de la API (sin dependencias de Home Assistant), errores y lectura de datos.
   - `config_flow.py`: alta, reidentificación, reconfiguración y opciones.
   - `coordinator.py`: qué se pide y cada cuánto.
   - `sensor.py`, `weather.py`, `sky.py` (estado estimado del cielo), `entity.py`.
   - `tests/conftest.py`: SiAR simulado.

Manual oficial: https://servicio.mapa.gob.es/siarweb/documentos/manuales/ManualTecnico_WEB_API_SIAR.pdf

## Reglas
- Cambios en rama y por PR. Merge solo con autorización del autor.
- Repo público: sin datos reales, credenciales, nombres, lugares ni IPs. En pruebas, estaciones inventadas.
- Ninguna URL de la API en registros ni errores (llevan las credenciales).
- Un token no demuestra nada: las credenciales se validan con una petición protegida.
- Antes de publicar: `ruff check .` y `pytest -q`; versión en `manifest.json` y apartado en `CHANGELOG.md`.
- Comprobar versiones al día y vulnerabilidades conocidas.
