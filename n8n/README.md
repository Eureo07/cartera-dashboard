# Workflow n8n: Alertas Watchlist

Workflow que cada día laborable a las 17:35 CET consulta `GET /api/alertas` del dashboard, compara cada señal de la watchlist con su precio actual vía Yahoo Finance y envía un email Gmail solo cuando se cumple la condición del trigger y el ticker no está ya marcado como alertado.

Para importarlo: en n8n, **Workflows → Add workflow → ⋯ → Import from File** y selecciona `workflow_alertas_watchlist.json`.

Configuración manual (no automatizable): crea la credencial **Gmail OAuth2** en el nodo "Enviar email Gmail" (requiere login interactivo de Google) y rellena la URL real de la app en los nodos HTTP y el destinatario `TU-EMAIL@gmail.com`.

Tras importar, activa el workflow con el toggle **Active** (viene desactivado a propósito). El estado de dedup se guarda en `alertas_state.json` del servidor: si algo sale mal, pásale el IT o marca a mano el ticker como no alertado.

Un ejemplo real de la respuesta de `/api/alertas` está en `ejemplo_alertas_response.json` (el campo que hay que partir es `items`, no `alertas`).