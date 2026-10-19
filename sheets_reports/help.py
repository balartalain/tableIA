"""Centro de ayuda del editor: los tópicos, en el orden en que se leen.

Cada tópico es `templates/sheets_reports/help/<slug>.html`. Agregar uno = una entrada aquí y
su partial. Desde cualquier contenido se enlaza con `<a href="#help:<slug>">` (o
`#help:<slug>/<id>` para ir a una parte del tópico) y desde un botón con
`$store.help.open('<slug>')`; test_help comprueba que todos apunten a un tópico que existe.
"""
from django.templatetags.static import static

SECTIONS = [
    ("inicio", "Primeros pasos", "ti-rocket"),
    ("datos", "Datos", "ti-database"),
    ("widgets", "Widgets", "ti-layout-dashboard"),
    ("configurar", "Configurar los datos de un widget", "ti-adjustments"),
    ("filtros", "Filtros del tablero", "ti-filter"),
    ("ia", "Inteligencia artificial", "ti-sparkles"),
    ("compartir", "Compartir", "ti-share"),
    ("ajustes", "Configuración", "ti-settings"),
]

# slug, sección, título, palabras para el buscador (además del título y del texto).
HELP_TOPICS = [
    ("bienvenida", "inicio", "Bienvenida", "qué es inicio introducción flujo"),
    ("crear-tablero", "inicio", "Crear un tablero", "nuevo tablero empezar"),
    ("recorrido-editor", "inicio", "Recorrido por el editor", "módulos lienzo filas guardado menú"),
    ("conectar-hoja", "datos", "Conectar una hoja de Google", "google sheets drive pestaña forms formulario"),
    ("fuentes", "datos", "Gestionar las fuentes", "fuente agregar actualizar cambiar eliminar varias hojas"),
    ("columnas", "datos", "Columnas: tipos, formatos y nombres", "tipo formato moneda porcentaje nombre alias incluir escala"),
    ("campos-calculados", "datos", "Campos calculados", "fórmula calcular si suma bloques"),
    ("agregar-widgets", "widgets", "Agregar y organizar widgets", "arrastrar fila mover tamaño eliminar"),
    ("panel-edicion", "widgets", "El panel de edición", "configurar personalizar guardar título fuente"),
    ("tipos-de-widget", "widgets", "Tipos de widget", "kpi barras líneas dona tabla dinámica ranking dispersión cramér"),
    ("dimensiones", "configurar", "Dimensiones", "agrupar por categoría"),
    ("pivotes", "configurar", "Pivotes", "series columnas desagregar"),
    ("metricas", "configurar", "Métricas", "suma promedio conteo agregación medida"),
    ("mostrar-como", "configurar", "Mostrar como", "porcentaje acumulado variación ventana total"),
    ("filtros-orden-limite", "configurar", "Condiciones, filtros, orden y límite", "solo filas donde ordenar top tendencia"),
    ("correlacion", "configurar", "Matriz de correlación", "pearson spearman relación variables"),
    ("filtros-tablero", "filtros", "Filtros del tablero", "filtrar añadir filtro todos los widgets"),
    ("ia-generar-tablero", "ia", "Generar un tablero con IA", "inteligencia artificial pedido automático"),
    ("ia-asistente", "ia", "El asistente del widget", "consultar a la ia pedir cambio sugerencias"),
    ("ia-analisis", "ia", "Análisis de un widget con IA", "insight interpretar resumen"),
    ("chat-datos", "ia", "Chat con los datos", "preguntar asistente de datos vista compartida"),
    ("compartir", "compartir", "Vista previa y compartir", "enlace link ver"),
    ("plantillas", "compartir", "Plantillas y duplicar", "plantilla copiar reutilizar"),
    ("configuracion", "ajustes", "Configuración del tablero", "nombre color tema eliminar"),
    ("correos", "ajustes", "Envío de correos", "email campaña destinatarios remitentes gmail seguimiento"),
]


# El video de demostración de cada tópico (tests/e2e/demo_videos.py): archivo en
# static/sheets_reports/help/videos/ (.webm y su portada .png) y su duración.
HELP_VIDEOS = {
    "crear-tablero": ("01_crear_tablero", "30 s"),
    "columnas": ("02_nombres_de_columnas_con_ia", "30 s"),
    "fuentes": ("03_agregar_fuente", "27 s"),
    "campos-calculados": ("09_campos_calculados", "45 s"),
    "agregar-widgets": ("04_agregar_widgets", "40 s"),
    "ia-generar-tablero": ("05_generar_tablero_con_ia", "25 s"),
    "ia-asistente": ("06_asistente_ia_del_widget", "25 s"),
    "ia-analisis": ("07_analisis_ia_de_un_widget", "12 s"),
    "chat-datos": ("08_chat_con_los_datos", "17 s"),
}
VIDEO_DIR = "sheets_reports/help/videos"


def help_index() -> list[dict]:
    """Los tópicos en orden, con su sección: lo que dibuja el índice y lo que recorre
    «Anterior / Siguiente»."""
    sections = {key: (title, icon) for key, title, icon in SECTIONS}
    topics = []
    for slug, section, title, keywords in HELP_TOPICS:
        video, length = HELP_VIDEOS.get(slug, (None, None))
        topics.append({
            "slug": slug, "section": section, "section_title": sections[section][0],
            "icon": sections[section][1], "title": title,
            "keywords": f"{keywords} video" if video else keywords,
            "template": f"sheets_reports/help/{slug}.html",
            "video": static(f"{VIDEO_DIR}/{video}.webm") if video else None,
            "poster": static(f"{VIDEO_DIR}/{video}.png") if video else None,
            "video_length": length,
        })
    return topics


def help_sections() -> list[dict]:
    """Las secciones con sus tópicos, para el índice."""
    topics = help_index()
    return [{"key": key, "title": title, "icon": icon,
             "topics": [t for t in topics if t["section"] == key]}
            for key, title, icon in SECTIONS]
