# Despliegue en Railway (gratuito)

## Requisitos
- Cuenta gratuita en [Railway](https://railway.app/) (GitHub login)
- Git instalado localmente
- Docker Desktop (opcional, para pruebas locales)

---

## Paso 1: Subir el proyecto a GitHub

```bash
# Desde la carpeta del proyecto:
git init
git add .
git commit -m "Initial commit"
# Crear repo en GitHub y luego:
git remote add origin https://github.com/tu-usuario/tu-repo.git
git push -u origin main
```

## Paso 2: Desplegar en Railway

1. Ve a [railway.app](https://railway.app/) e inicia sesión con GitHub
2. Haz clic en **"New Project"** → **"Deploy from GitHub repo"**
3. Selecciona tu repositorio
4. Railway detecta automáticamente el `Dockerfile` y construye la imagen
5. En segundos tendrás una URL pública tipo `https://cartera-dashboard.up.railway.app`

Railway asigna automáticamente:
- Puerto 5000 (via `PORT` env var)
- Un dominio `*.railway.app` público
- HTTPS gratuito con certificado Let's Encrypt
- Reinicio automático si el proceso falla

## Paso 3: Abrir el dashboard

```
https://tu-proyecto.up.railway.app/dashboard.html
```

**Funciona en el móvil** — abre la URL desde Chrome/Safari en tu teléfono. La tabla se adapta al ancho de pantalla (responsive). Los precios se actualizan automáticamente al cargar la página via `/api/price/` (mismo servidor).

---

## Mantenimiento

### Regenerar el dashboard (precios actualizados)

Railway reinicia el contenedor periódicamente. Cada reinicio ejecuta `start.sh` que:
1. Ejecuta `generate_dashboard.py` (descarga precios actuales + screener)
2. Inicia el servidor web

Para forzar un reinicio manual desde Railway Dashboard → botón **"Restart"**.

### Variables de entorno (opcional)

Para activar alertas por email, añade en Railway → Variables de entorno:

```
GMAIL_PASSWORD=tu_app_password_de_gmail
```

Sin esta variable, el servidor web funciona igual (solo que no se envían alertas).

---

## Pruebas locales con Docker

```bash
docker compose up --build
# Abrir: http://localhost:5000/dashboard.html
```

---

## Notas

- El dashboard se genera con datos de Yahoo Finance en cada inicio del contenedor
- `fin_data_final.xlsx` y `tickers_universo.json` están empaquetados en la imagen
- El Screener se ejecuta automáticamente al iniciar (consume ~30s y requiere internet)
- La imagen es ~450MB (Python slim + pandas + yfinance)
- Railway gratis: 500 horas/mes, 512MB RAM, 1GB disco — suficiente para este dashboard
