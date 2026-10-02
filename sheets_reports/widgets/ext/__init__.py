"""
Widgets de extensión: cada módulo de este paquete es UN widget completo (su data_spec, su plan
si necesita una forma de datos propia, sus opciones de vista y su compilación) que importa
solo desde `sheets_reports.sdk`. No hace falta registrarlo en ningún otro archivo: la app los
carga al arrancar (SheetsReportsConfig.ready) y cada uno se registra con @WIDGETS.register.
"""
import importlib
import pkgutil


def load(package: str = __name__) -> list[str]:
    """Importa cada módulo de `package` (lo que registra sus widgets). Retorna sus nombres."""
    module = importlib.import_module(package)
    names = []
    for info in pkgutil.iter_modules(module.__path__):
        if info.name.startswith("_"):
            continue
        importlib.import_module(f"{package}.{info.name}")
        names.append(info.name)
    return names
