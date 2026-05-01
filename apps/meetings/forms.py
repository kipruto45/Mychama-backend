from django import forms

from apps.meetings.models import Attendance, Meeting, Resolution


class MeetingForm(forms.ModelForm):
    class Meta:
        model = Meeting
        fields = ("title", "date", "agenda")
        widgets = {
            "date": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "agenda": forms.Textarea(attrs={"rows": 4}),
        }


class MeetingMinutesForm(forms.ModelForm):
    class Meta:
        model = Meeting
        fields = ("minutes_text", "minutes_file")
        widgets = {
            "minutes_text": forms.Textarea(attrs={"rows": 8}),
        }


class AttendanceForm(forms.ModelForm):
    class Meta:
        model = Attendance
        fields = ("member", "status", "notes")
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}


class ResolutionForm(forms.ModelForm):
    class Meta:
        model = Resolution
        fields = ("text", "assigned_to", "due_date", "status")
        widgets = {"text": forms.Textarea(attrs={"rows": 4})}
