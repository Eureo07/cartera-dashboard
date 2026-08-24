# Workflow n8n: Alertas Watchlist

Workflow que cada día laborable a las 17:35 CET consulta `GET /api/alertas` del dashboard, compara cada señal de la watchlist con su precio actual vía Yahoo Finance y envía un email Gmail solo cuando se cumple la condición del trigger y el ticker no está ya marcado como alertado.

Para importarlo: en n8n, **Workflows → Add workflow → ⋯ → Import from File** y selecciona `workflow_alertas_watchlist.json`.

Configuración manual (no automatizable): crea la credencial **Gmail OAuth2** en el nodo "Enviar email Gmail" (requiere login interactivo de Google) y rellena la URL real de la app en los nodos HTTP y el destinatario `TU-EMAIL@gmail.com`.

Tras importar, activa el workflow con el toggle **Active** (viene desactivado a propósito). El estado de dedup se guarda en `alertas_state.json` del servidor: si algo sale mal, pásale el IT o marca a mano el ticker como no alertado.

Un ejemplo real de la respuesta de `/api/alertas` está en `ejemplo_alertas_response.json` (el campo que hay que partir es `items`, no `alertas`).

## Rama de candidatos (viernes tras cierre)

El mismo workflow incluye una segunda rama, con su propio cron (viernes
17:35 CET), que consulta `GET /api/candidatos` — listado de toda la
watchlist evaluada contra el modelo (score, señal técnica, distancia,
tamaño de posición sugerido, deuda neta/EBITDA, ROIC vs ROE, PEG,
concentración temática). Solo envía email para candidatos con
`signal_active: true` y `alertado: false`, y marca el ticker con
`POST /api/candidatos/marcar` (mismo `alertas_state.json` que la rama de
alertas — dedupe compartido por ticker entre ambas ramas).

El campo `deuda_neta_ebitda` solo se rellena si se ha ejecutado
`python generate_dashboard.py` en local recientemente (yfinance `.info`
está bloqueado en Render); si no hay cache, sale `null` y el candidato
lleva un aviso "no disponible" en `warnings`, sin bloquear el resto de
criterios.