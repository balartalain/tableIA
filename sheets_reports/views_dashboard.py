import copy
import json
import logging
import threading
import uuid
from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection, transaction
from django.http import JsonResponse
from django.utils.timesince import timesince
from django.utils.timezone import now
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from sheets_reports.models import CalculatedColumn, Dashboard, DashboardUtilFunction, DataSource, WidgetInstance
from sheets_reports.utils.generate_dashboard_ia import generate_board_from_prompt

logger = logging.getLogger(__name__)


def _get_user(request):
    if request.user.is_authenticated:
        return request.user
    user = get_user_model().objects.filter(is_superuser=True).first()
    if not user:
        user = get_user_model().objects.first()
    return user


def _sheet_name(source_url):
    if not source_url:
        return ""
    import re
    m = re.search(r'/spreadsheets/d/([^/]+)', source_url)
    if m:
        return m.group(1)
    try:
        path = urlparse(source_url).path
        return path.strip("/").split("/")[-1] or path.strip("/")
    except Exception:
        return source_url


def _serialize(dashboard):
    data_source = dashboard.data_source
    source_url = ""
    if data_source and data_source.source_type == DataSource.SourceType.GOOGLE_SHEETS:
        source_url = data_source.config.get("source_url", "")
    return {
        "id": dashboard.id,
        "title": dashboard.title,
        "slug": dashboard.slug,
        "source_url": source_url,
        "sheetName": _sheet_name(source_url),
        "data_source": {
            "id": data_source.id,
            "name": data_source.name,
            "source_type": data_source.source_type,
        } if data_source else None,
        "cardCount": dashboard.widgets.count(),
        "created_at": dashboard.created_at.isoformat(),
        "updated": timesince(dashboard.created_at, now()),
    }


def _resolve_data_source(data, user, name_hint):
    """
    Resuelve la DataSource a partir del payload de un request de creación/generación de
    dashboard: si trae `data_source_id`, reutiliza una DataSource ya existente (el único
    camino hoy para usar un origen no-Sheets, ej. Postgres, dado que todavía no hay UI para
    crearlos -- se dan de alta vía Django admin). Si trae `source_url`, crea una DataSource
    "google_sheets" nueva sobre la marcha, por compatibilidad con el formulario actual (que
    solo conoce source_url). Retorna (data_source, None) o (None, JsonResponse de error).
    """
    data_source_id = data.get("data_source_id")
    if data_source_id:
        try:
            return DataSource.objects.get(id=data_source_id), None
        except DataSource.DoesNotExist:
            return None, JsonResponse({"error": "Origen de datos no encontrado"}, status=404)

    source_url = (data.get("source_url") or "").strip()
    if source_url:
        data_source = DataSource.objects.create(
            name=f"{name_hint} (Sheets)",
            source_type=DataSource.SourceType.GOOGLE_SHEETS,
            config={"source_url": source_url},
            owner=user,
        )
        return data_source, None

    return None, JsonResponse({"error": "Se requiere data_source_id o source_url"}, status=400)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def dashboard_list(request):
    if request.method == "GET":
        dashboards = Dashboard.objects.all().order_by("-created_at")
        return JsonResponse([_serialize(d) for d in dashboards], safe=False)

    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON inválido"}, status=400)

    title = data.get("title", "").strip()
    if not title:
        return JsonResponse({"error": "El título es obligatorio"}, status=400)

    user = _get_user(request)
    if not user:
        return JsonResponse({"error": "No hay usuario disponible"}, status=401)

    data_source, error = _resolve_data_source(data, user, title)
    if error:
        return error

    dashboard = Dashboard.objects.create(
        title=title,
        data_source=data_source,
        user=user,
    )
    return JsonResponse(_serialize(dashboard), status=201)


@csrf_exempt
@require_http_methods(["PUT", "DELETE"])
def dashboard_detail(request, dashboard_id):
    try:
        dashboard = Dashboard.objects.get(id=dashboard_id)
    except Dashboard.DoesNotExist:
        return JsonResponse({"error": "Dashboard no encontrado"}, status=404)

    if request.method == "DELETE":
        dashboard.delete()
        return JsonResponse({"deleted": True})

    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON inválido"}, status=400)

    if "title" in data:
        title = data["title"].strip()
        if not title:
            return JsonResponse({"error": "El título no puede estar vacío"}, status=400)
        dashboard.title = title
    if "data_source_id" in data:
        try:
            dashboard.data_source = DataSource.objects.get(id=data["data_source_id"])
        except DataSource.DoesNotExist:
            return JsonResponse({"error": "Origen de datos no encontrado"}, status=404)
    elif "source_url" in data:
        source_url = (data["source_url"] or "").strip()
        if dashboard.data_source_id and dashboard.data_source.source_type == DataSource.SourceType.GOOGLE_SHEETS:
            dashboard.data_source.config = {"source_url": source_url}
            dashboard.data_source.save(update_fields=["config", "updated_at"])
        else:
            dashboard.data_source = DataSource.objects.create(
                name=f"{dashboard.title} (Sheets)",
                source_type=DataSource.SourceType.GOOGLE_SHEETS,
                config={"source_url": source_url},
                owner=dashboard.user,
            )
    dashboard.save()
    return JsonResponse(_serialize(dashboard))


def _clone_data_source(data_source, user):
    """Copia una DataSource junto con sus columnas calculadas. Se clona en vez de compartirla
    porque dashboard_detail edita `config` in place al cambiar la URL de la hoja: si la copia
    compartiera la DataSource, cambiar la hoja de un tablero cambiaría también la del otro."""
    clone = DataSource.objects.create(
        name=f"{data_source.name} (copia)",
        source_type=data_source.source_type,
        config=copy.deepcopy(data_source.config),
        owner=user,
    )
    CalculatedColumn.objects.bulk_create([
        CalculatedColumn(
            data_source=clone,
            table_name=cc.table_name,
            column_name=cc.column_name,
            expression=cc.expression,
            description=cc.description,
            created_from_prompt=cc.created_from_prompt,
            is_active=cc.is_active,
        )
        for cc in data_source.calculated_columns.all()
    ])
    return clone


@csrf_exempt
@require_http_methods(["POST"])
def dashboard_duplicate(request, dashboard_id):
    """POST: clona un tablero completo (origen de datos, widgets y funciones utilitarias)."""
    try:
        original = Dashboard.objects.select_related("data_source").get(id=dashboard_id)
    except Dashboard.DoesNotExist:
        return JsonResponse({"error": "Dashboard no encontrado"}, status=404)

    user = _get_user(request) or original.user

    with transaction.atomic():
        data_source = _clone_data_source(original.data_source, user) if original.data_source else None
        dashboard = Dashboard.objects.create(
            title=f"{original.title} (copia)",
            source_url=original.source_url,
            data_source=data_source,
            user=user,
        )
        WidgetInstance.objects.bulk_create([
            WidgetInstance(
                dashboard=dashboard,
                title=w.title,
                chart_type=w.chart_type,
                code=w.code,
                prompt=w.prompt,
                summary=w.summary,
                properties=copy.deepcopy(w.properties),
                order=w.order,
            )
            for w in original.widgets.all()
        ])
        DashboardUtilFunction.objects.bulk_create([
            DashboardUtilFunction(
                dashboard=dashboard,
                name=u.name,
                signature=u.signature,
                description=u.description,
                category=u.category,
                source_code=u.source_code,
                created_from_prompt=u.created_from_prompt,
                is_active=u.is_active,
            )
            for u in original.custom_utils.all()
        ])

    return JsonResponse(_serialize(dashboard), status=201)


def _get_request_data(request):
    """Extrae datos del request sin importar el método HTTP o content-type."""
    if request.content_type and "application/json" in request.content_type:
        return json.loads(request.body)
    if request.method == "POST":
        return request.POST
    if request.method == "PUT":
        try:
            return json.loads(request.body) if request.body else {}
        except (json.JSONDecodeError, AttributeError):
            return {}
    return request.GET


# Estado de cada generación de tablero en curso, en el cache (DatabaseCache, compartido entre
# procesos: el hilo que genera y el request que consulta el estado pueden caer en workers
# distintos). Una hora sobra para que el navegador lea el resultado final.
BOARD_JOB_CACHE_TIMEOUT = 3600


def _board_job_key(job_id):
    return f"board_gen:{job_id}"


def _run_board_job(job_id, prompt, data_source, user):
    """
    Cuerpo del hilo en segundo plano de generate_dashboard_from_prompt: consume el generador
    de progreso y va guardando el último evento en el cache para que lo lea
    generate_dashboard_status.
    """
    key = _board_job_key(job_id)
    try:
        for event in generate_board_from_prompt(prompt, data_source, user):
            if event["event"] == "done":
                event = {"event": "done", "dashboard": _serialize(event["dashboard"])}
            cache.set(key, event, BOARD_JOB_CACHE_TIMEOUT)
    except Exception as e:
        logger.exception("Falló el job de generación de tablero %s", job_id)
        cache.set(key, {"event": "error", "message": str(e)}, BOARD_JOB_CACHE_TIMEOUT)
    finally:
        connection.close()


@csrf_exempt
@require_http_methods(["POST"])
def generate_dashboard_from_prompt(request):
    """
    POST /api/dashboards/generate-from-prompt/
    Crea un tablero completo desde una descripción en lenguaje natural. La generación (plan +
    código de cada widget con Gemini) puede tardar minutos, más de lo que aguanta una sola
    petición HTTP detrás de Apache/proxy -- por eso corre en un hilo en segundo plano y esto
    responde enseguida con 202 { "job_id": "..." }; el navegador consulta el avance en
    GET /api/dashboards/generate-from-prompt/<job_id>/ (generate_dashboard_status).
    Body: { "prompt": "...", "source_url": "..." } o { "prompt": "...", "data_source_id": N }
    (data_source_id apunta a una DataSource ya creada, ej. una conexión Postgres dada de alta
    vía Django admin; source_url crea una DataSource "google_sheets" nueva al vuelo, por
    compatibilidad con el formulario actual).
    """
    try:
        data = _get_request_data(request)
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON inválido"}, status=400)

    prompt = (data.get("prompt") or "").strip()

    if not prompt:
        return JsonResponse({"error": "El prompt es obligatorio"}, status=400)

    user = _get_user(request)
    if not user:
        return JsonResponse({"error": "No hay usuario disponible"}, status=401)

    data_source, error = _resolve_data_source(data, user, "Tablero generado")
    if error:
        return error

    job_id = uuid.uuid4().hex
    cache.set(_board_job_key(job_id), {"event": "queued"}, BOARD_JOB_CACHE_TIMEOUT)
    threading.Thread(
        target=_run_board_job,
        args=(job_id, prompt, data_source, user),
        daemon=True,
    ).start()
    return JsonResponse({"job_id": job_id}, status=202)


@require_http_methods(["GET"])
def generate_dashboard_status(request, job_id):
    """
    GET /api/dashboards/generate-from-prompt/<job_id>/
    Último evento de progreso del job lanzado por generate_dashboard_from_prompt:
      queued                            -> todavía no arrancó
      planning                          -> armando el plan del tablero
      plan        {title, total}        -> plan listo, se van a generar `total` widgets
      widget_done {done, total, title}  -> `done` de `total` widgets ya generados
      done        {dashboard: {...}}    -> tablero completo, mismo shape que POST /api/dashboards/
      error       {message}             -> algo falló; no queda ningún registro huérfano
    """
    state = cache.get(_board_job_key(job_id))
    if state is None:
        return JsonResponse({"error": "Generación no encontrada o expirada"}, status=404)
    return JsonResponse(state)
