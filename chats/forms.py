from django import forms
from django.conf import settings
from uuid import uuid4
from research.models import DeveloperRating


class QuestionForm(forms.Form):
    research_mode = forms.ChoiceField(choices=[("balanced", "Balanced"), ("deep", "Deep Research")], required=False, widget=forms.Select(attrs={"class": "form-select"}))
    deep_synthesizer = forms.ChoiceField(required=False, widget=forms.Select(attrs={"class": "form-select"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["research_mode"].initial = settings.RESEARCH_MODE
        self.fields["deep_synthesizer"].choices = [(o["key"], o["label"]) for o in settings.DEEP_SYNTHESIZER_OPTIONS]
        self.fields["deep_synthesizer"].initial = settings.DEEP_DEFAULT_SYNTHESIZER

    def clean(self):
        data = super().clean()
        data["research_mode"] = data.get("research_mode") or settings.RESEARCH_MODE
        return data

    submission_key = forms.UUIDField(required=False, initial=uuid4, widget=forms.HiddenInput)
    question = forms.CharField(
        max_length=20000,
        strip=True,
        widget=forms.Textarea(attrs={
            "class": "form-control question-input",
            "placeholder": "Ask a follow-up research question...",
            "rows": 2,
            "aria-describedby": "composer-help",
        }),
    )


class RatingForm(forms.ModelForm):
    class Meta:
        model = DeveloperRating
        fields = ["answer_quality", "citation_quality", "evidence_relevance", "synthesis_quality", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3, "maxlength": 5000})}

    def clean_notes(self):
        from agents.security import redact
        notes = self.cleaned_data["notes"]
        if len(notes) > 5000:
            raise forms.ValidationError("Use at most 5000 characters.")
        return redact(notes)
