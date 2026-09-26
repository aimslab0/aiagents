from django.http import JsonResponse
from django.conf import settings
from django.views.decorators.http import require_POST

from agents.prompt_enhancer import MAX_INPUT_CHARS, EnhancementError, enhance_prompt
from agents.schemas import strict_json_loads


@require_POST
def enhance(request):
    if request.content_type != "application/json":
        return JsonResponse({"error": "Send a JSON object containing text."}, status=415)
    # Allow JSON escaping overhead, but reject oversized bodies before decoding.
    limit = MAX_INPUT_CHARS * 12 + 100
    try:
        if int(request.META.get("CONTENT_LENGTH") or 0) > limit or len(request.body) > limit:
            return JsonResponse({"error": "Prompt is too long (maximum 2000 characters)."}, status=413)
        data = strict_json_loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        return JsonResponse({"error": "Invalid JSON input."}, status=400)
    text = data.get("text") if isinstance(data, dict) else None
    if not isinstance(text, str) or not text.strip():
        return JsonResponse({"error": "Enter a research idea first."}, status=400)
    if len(text) > MAX_INPUT_CHARS:
        return JsonResponse({"error": "Prompt is too long (maximum 2000 characters)."}, status=400)
    try:
        return JsonResponse(enhance_prompt(text.strip()))
    except EnhancementError as error:
        if error.category == "configuration" and settings.DEBUG:
            return JsonResponse({"error": "OpenRouter API key is not configured.", "code": "configuration"}, status=503)
        return JsonResponse({"error": str(error)}, status=503)
