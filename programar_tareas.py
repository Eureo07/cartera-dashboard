# -*- coding: utf-8 -*-
"""
Configurador del Programador de Tareas de Windows.
Crea 3 tareas: Dashboard diario, Screener diario, Alertas.
Ejecutar como Administrador.
"""
import subprocess, sys, os, getpass

# ========== DETECTAR RUTAS ==========
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_PATH = sys.executable

TASKS = {
    "Dashboard_Cartera_Diario": {
        "script": os.path.join(SCRIPT_DIR, "generate_dashboard.py"),
        "time": "08:00",
        "days": "MON,TUE,WED,THU,FRI",
        "desc": "Genera el dashboard diario de la cartera"
    },
    "Screener_Cartera_Diario": {
        "script": os.path.join(SCRIPT_DIR, "screener.py"),
        "time": "08:15",
        "days": "MON,TUE,WED,THU,FRI",
        "desc": "Ejecuta el screener de oportunidades"
    },
    "Alertas_Cartera": {
        "script": os.path.join(SCRIPT_DIR, "alertas.py"),
        "time": "08:30",
        "days": "MON,TUE,WED,THU,FRI",
        "desc": "Env\u00eda alertas de la cartera"
    },
}

def create_task(name, info):
    script = info["script"]
    time = info["time"]
    days = info["days"]
    cmd = f'"{PYTHON_PATH}" "{script}"'
    schtasks_cmd = [
        "schtasks", "/Create", "/F",
        "/TN", name,
        "/TR", cmd,
        "/SC", "WEEKLY",
        "/D", days,
        "/ST", time,
        "/RL", "HIGHEST",
    ]
    try:
        result = subprocess.run(schtasks_cmd, capture_output=True, text=True, shell=True)
        if result.returncode == 0:
            print(f"  [OK] Tarea creada: {name}")
            print(f"       Script: {script}")
            print(f"       Horario: {days} a las {time}")
            print(f"       Python: {PYTHON_PATH}")
            return True
        else:
            print(f"  [ERROR] No se pudo crear {name}:")
            print(f"          {result.stderr.strip()}")
            return False
    except Exception as e:
        print(f"  [ERROR] Excepci\u00f3n al crear {name}: {e}")
        return False

def delete_task(name):
    cmd = ["schtasks", "/Delete", "/F", "/TN", name]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, shell=True)
        if result.returncode == 0:
            print(f"  [OK] Tarea eliminada: {name}")
            return True
        else:
            print(f"  [ERROR] No se pudo eliminar {name}: {result.stderr.strip()}")
            return False
    except Exception as e:
        print(f"  [ERROR] Excepci\u00f3n al eliminar {name}: {e}")
        return False

def list_tasks():
    cmd = ["schtasks", "/Query", "/FO", "LIST", "/TN", "Dashboard_Cartera_*"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, shell=True)
        if result.returncode == 0:
            print("Tareas encontradas:")
            print(result.stdout[:500])
        else:
            print("No se encontraron tareas de la cartera.")
    except:
        pass

if __name__ == "__main__":
    print("=" * 60)
    print("PROGRAMAR TAREAS — Cartera de Inversi\u00f3n")
    print("=" * 60)
    print()
    print(f"Directorio del proyecto: {SCRIPT_DIR}")
    print(f"Python: {PYTHON_PATH}")
    print(f"Usuario: {getpass.getuser()}")
    print()

    if len(sys.argv) > 1 and sys.argv[1] == "delete":
        print("Eliminando tareas existentes...")
        for name in TASKS:
            delete_task(name)
        print("\nHecho.")
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "list":
        list_tasks()
        sys.exit(0)

    print("Creando tareas programadas...")
    print("  (Ejecuta este script como Administrador)")
    print()

    # Check if running as admin (Windows)
    try:
        is_admin = subprocess.run(
            ["net", "session"],
            capture_output=True, text=True, shell=True
        ).returncode == 0
        if not is_admin:
            print("  ADVERTENCIA: No ejecutado como Administrador.")
            print("  Algunas tareas pueden fallar. Ejecuta como Administrador.")
            print()
    except:
        pass

    success = 0
    for name, info in TASKS.items():
        if create_task(name, info):
            success += 1
        print()

    print(f"\nTareas creadas exitosamente: {success}/{len(TASKS)}")
    if success == len(TASKS):
        print("\nTodas las tareas se crearon correctamente.")
        print("Verifica en: Programador de tareas > Biblioteca > Tareas_Cartera")
    else:
        print("\nAlgunas tareas no se crearon. Revisa los errores anteriores.")

    print("\nPara ELIMINAR todas las tareas:")
    print(f"  python \"{os.path.join(SCRIPT_DIR, 'programar_tareas.py')}\" delete")
    print("\nPara LISTAR las tareas:")
    print(f"  python \"{os.path.join(SCRIPT_DIR, 'programar_tareas.py')}\" list")
