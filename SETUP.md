# SETUP — Cartera de Inversión

## 1. Crear contraseña de aplicación en Gmail

Para que `alertas.py` pueda enviar correos desde tu Gmail, necesitas una **contraseña de aplicación** (no tu contraseña principal):

1. Ve a https://myaccount.google.com/apppasswords
2. Inicia sesión con tu cuenta de Gmail
3. En "Seleccionar aplicación", elige **"Otra (nombre personalizado)"**
4. Escribe: `Cartera Inversión`
5. Haz clic en **Generar**
6. Google te mostrará una contraseña de 16 caracteres (ej: `abcd efgh ijkl mnop`)
7. **Copia esa contraseña** — no podrás verla después

> **Nota:** Si no ves la opción "Contraseñas de aplicación", activa primero la **verificación en dos pasos** en:
> https://myaccount.google.com/security

## 2. Configurar variable de entorno GMAIL_PASSWORD

Abre PowerShell (como usuario normal, no administrador) y ejecuta:

```powershell
[System.Environment]::SetEnvironmentVariable('GMAIL_PASSWORD', 'tu_contraseña_de_16_caracteres', 'User')
```

Reemplaza `tu_contraseña_de_16_caracteres` por la que te dio Google (sin espacios).

Para verificar que quedó configurada:

```powershell
$env:GMAIL_PASSWORD
```

Debería mostrar la contraseña.

> **Nota:** La variable de entorno es por usuario. Solo tu usuario de Windows podrá usarla.

## 3. Configurar el email en alertas.py

Abre `alertas.py` y cambia esta línea si usas otro email:

```python
GMAIL_USER = "franlopez.ef@gmail.com"  # ← cambia por TU email
```

## 4. Ejecutar programar_tareas.py como Administrador

Las tareas programadas necesitan permisos de administrador:

1. Busca **PowerShell** en el menú Inicio
2. Haz clic derecho > **Ejecutar como administrador**
3. Navega al directorio del proyecto:
   ```powershell
   cd "C:\Users\franl\OneDrive\Escritorio\Inversión\OpenCode\2026"
   ```
4. Ejecuta:
   ```powershell
   python programar_tareas.py
   ```

Deberías ver:

```
[OK] Tarea creada: Dashboard_Cartera_Diario
[OK] Tarea creada: Screener_Cartera_Diario
[OK] Tarea creada: Alertas_Cartera
```

## 5. Verificar que las tareas están activas

### Opción A: PowerShell

```powershell
python programar_tareas.py list
```

### Opción B: Programador de tareas

1. Presiona `Win + R`, escribe `taskschd.msc` y presiona Enter
2. En el panel izquierdo, selecciona **Biblioteca del Programador de tareas**
3. Busca las tareas que empiezan por `Dashboard_Cartera_`, `Screener_Cartera_` y `Alertas_Cartera`
4. Haz clic en cada una y verifica:
   - **Desencadenadores**: Lun-Vie a las 8:00 / 8:15 / 8:30
   - **Acciones**: Ejecuta `python.exe` con el script correspondiente
   - **Condiciones**: "Iniciar solo si hay conexión de red"

## 6. Probar las alertas manualmente

```powershell
python alertas.py daily
```

Si todo está configurado correctamente, recibirás un email de prueba.

## Resumen de scripts

| Script | Función | Horario |
|--------|---------|---------|
| `generate_dashboard.py` | Genera dashboard.html | Lun-Vie 8:00 |
| `screener.py` | Busca oportunidades | Lun-Vie 8:15 |
| `alertas.py` | Envía alertas por email | Lun-Vie 8:30 |
| `programar_tareas.py` | Configura el Programador de tareas | Una vez |

## Solución de problemas

### "Variable de entorno no configurada"
Ejecuta el paso 2 de nuevo. La variable es por sesión — si abres una nueva terminal, la variable persiste (configurada como `User`).

### "No se pudo crear la tarea"
Ejecuta PowerShell como **Administrador**. Sin permisos de admin, `schtasks` falla.

### "Error al enviar email"
- Verifica que la contraseña de aplicación sea correcta (16 caracteres, sin espacios)
- Verifica que `GMAIL_USER` en `alertas.py` sea tu email completo
- Si usas un email de Google Workspace, puede que necesites permisos adicionales

### "El screener no encuentra oportunidades"
- Revisa que `fin_data_final.xlsx` tenga datos actualizados
- Ejecuta `python screener.py` manualmente para ver qué muestra
