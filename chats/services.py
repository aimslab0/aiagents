from django.db import transaction, IntegrityError

from research.models import ResearchQuery

from .models import Chat, Message


def save_question(question, chat=None, submission_key=None, selection=None):
    if submission_key:
        previous = ResearchQuery.objects.filter(submission_key=submission_key).first()
        if previous:
            if previous.question != question or (chat is not None and previous.chat_id != chat.pk):
                raise ValueError("Submission token was already used for a different question.")
            if selection is not None and previous.execution_data.get("selection") != selection:
                raise ValueError("Submission token was already used with different research settings.")
            return previous
    try:
        with transaction.atomic():
            return _save_question(question, chat, submission_key, selection)
    except IntegrityError:
        if not submission_key:
            raise
        previous = ResearchQuery.objects.get(submission_key=submission_key)
        if previous.question != question or (chat is not None and previous.chat_id != chat.pk):
            raise ValueError("Submission token was already used for a different question.") from None
        if selection is not None and previous.execution_data.get("selection") != selection:
            raise ValueError("Submission token was already used with different research settings.") from None
        return previous


def _save_question(question, chat, submission_key, selection):
    if chat is None:
        chat = Chat.objects.create()
    if not chat.messages.exists():
        chat.title = " ".join(question.split())[:200]
    Message.objects.create(chat=chat, role=Message.Role.USER, content=question)
    query = ResearchQuery.objects.create(chat=chat, question=question, submission_key=submission_key, execution_data={"selection": selection} if selection is not None else {})
    chat.save(update_fields=["title", "updated_at"])
    return query
